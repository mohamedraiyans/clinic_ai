"""Doctor authentication: Google OAuth login + signed session cookie.

Only emails listed in ALLOWED_DOCTOR_EMAILS may sign in; Google accounts alone are not enough.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .db import get_db
from .models import AuditLog, Doctor

router = APIRouter(prefix="/api/auth")

COOKIE = "session"
STATE_COOKIE = "oauth_state"
SESSION_HOURS = 12
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _secure_cookie() -> bool:
    return _env("PUBLIC_BASE_URL").startswith("https")


def _secret() -> str:
    s = _env("JWT_SECRET")
    if not s:
        raise HTTPException(500, "JWT_SECRET is not configured")
    return s


def is_allowed(email: str) -> bool:
    allowed = {e.strip().lower() for e in _env("ALLOWED_DOCTOR_EMAILS").split(",") if e.strip()}
    return email.lower() in allowed


def _set_session(resp, doctor_id: int) -> None:
    token = jwt.encode(
        {"sub": str(doctor_id), "exp": datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)},
        _secret(), algorithm="HS256")
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=_secure_cookie(),
                    max_age=SESSION_HOURS * 3600, path="/")


def _doctor_from_cookie(session: str | None, db: Session) -> Doctor | None:
    if not session:
        return None
    try:
        sub = int(jwt.decode(session, _secret(), algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        # not a session token (e.g. a PDF link token has no "sub"): treat as signed out, never crash
        return None
    return db.get(Doctor, sub)


def optional_doctor(session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> Doctor | None:
    return _doctor_from_cookie(session, db)


def current_doctor(session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> Doctor:
    doctor = _doctor_from_cookie(session, db)
    if not doctor:
        raise HTTPException(401, "Not signed in")
    return doctor


# Short-lived link token so a PDF can be opened in a new tab / external PDF viewer
# without depending on the browser sending the session cookie.
def pdf_token(rx_id: str) -> str:
    return jwt.encode({"rx": rx_id, "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
                      _secret(), algorithm="HS256")


def pdf_token_valid(token: str | None, rx_id: str) -> bool:
    if not token:
        return False
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"]).get("rx") == rx_id
    except jwt.PyJWTError:
        return False


def _login_doctor(db: Session, email: str, name: str, sub: str = "", picture: str = "") -> Doctor:
    doctor = db.query(Doctor).filter(Doctor.email == email).first()
    if not doctor:
        doctor = Doctor(name=name or email.split("@")[0], email=email, reg_no="", clinic="",
                        google_sub=sub or None, picture=picture or None)
        db.add(doctor)
    else:
        doctor.google_sub = sub or doctor.google_sub
        doctor.picture = picture or doctor.picture
    db.add(AuditLog(event="login", detail={"email": email}))
    db.commit()
    return doctor


def _doctor_dict(d: Doctor) -> dict:
    return {"id": d.id, "name": d.name, "email": d.email, "picture": d.picture,
            "reg_no": d.reg_no, "clinic": d.clinic, "profile_complete": bool(d.reg_no and d.clinic)}


@router.get("/config")
def config():
    return {"google": bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET")),
            "dev_login": _env("DEV_LOGIN") == "1"}


@router.get("/google/login")
def google_login():
    if not (_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET")):
        raise HTTPException(503, "Google login is not configured")
    state = secrets.token_urlsafe(24)
    params = {"client_id": _env("GOOGLE_CLIENT_ID"), "redirect_uri": _env("GOOGLE_REDIRECT_URI"),
              "response_type": "code", "scope": "openid email profile", "state": state,
              "prompt": "select_account"}
    resp = RedirectResponse(f"{GOOGLE_AUTH}?{urlencode(params)}")
    resp.set_cookie(STATE_COOKIE, state, httponly=True, samesite="lax", secure=_secure_cookie(),
                    max_age=600, path="/")
    return resp


@router.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    oauth_state: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    front = _env("FRONTEND_URL", "http://localhost:5173")

    def back(err: str) -> RedirectResponse:
        r = RedirectResponse(f"{front}/?error={err}")
        r.delete_cookie(STATE_COOKIE, path="/")
        return r

    if error or not code:
        return back("cancelled")
    if not state or not oauth_state or not secrets.compare_digest(state, oauth_state):
        return back("bad_state")
    try:
        with httpx.Client(timeout=15) as http:
            tok = http.post(GOOGLE_TOKEN, data={
                "code": code, "client_id": _env("GOOGLE_CLIENT_ID"),
                "client_secret": _env("GOOGLE_CLIENT_SECRET"),
                "redirect_uri": _env("GOOGLE_REDIRECT_URI"), "grant_type": "authorization_code"})
            tok.raise_for_status()
            info = http.get(GOOGLE_USERINFO, headers={
                "Authorization": f"Bearer {tok.json()['access_token']}"})
            info.raise_for_status()
            profile = info.json()
    except (httpx.HTTPError, KeyError):
        return back("google_failed")

    email = str(profile.get("email", "")).lower()
    if not email or not profile.get("email_verified"):
        return back("unverified")
    if not is_allowed(email):
        db.add(AuditLog(event="login_denied", detail={"email": email}))
        db.commit()
        return back("not_allowed")
    doctor = _login_doctor(db, email, profile.get("name", ""), profile.get("sub", ""), profile.get("picture", ""))
    resp = RedirectResponse(front)
    resp.delete_cookie(STATE_COOKIE, path="/")
    _set_session(resp, doctor.id)
    return resp


class DevLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)


@router.post("/dev-login")
def dev_login(body: DevLoginIn, db: Session = Depends(get_db)):
    """Local testing only (DEV_LOGIN=1). Still honours the email allowlist."""
    if _env("DEV_LOGIN") != "1":
        raise HTTPException(404, "Not found")
    email = body.email.strip().lower()
    if not is_allowed(email):
        raise HTTPException(403, "This email is not on the doctor allowlist")
    doctor = _login_doctor(db, email, email.split("@")[0])
    resp = JSONResponse(_doctor_dict(doctor))
    _set_session(resp, doctor.id)
    return resp


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/me")
def me(doctor: Doctor = Depends(current_doctor)):
    return _doctor_dict(doctor)


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    reg_no: str = Field(min_length=2, max_length=60)
    clinic: str = Field(min_length=2, max_length=160)


@router.put("/me")
def update_me(body: ProfileIn, doctor: Doctor = Depends(current_doctor), db: Session = Depends(get_db)):
    doctor.name, doctor.reg_no, doctor.clinic = body.name.strip(), body.reg_no.strip(), body.clinic.strip()
    db.add(AuditLog(event="profile_updated", detail={"doctor": doctor.id}))
    db.commit()
    return _doctor_dict(doctor)
