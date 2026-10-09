"""Who may use the app: login gate, allowlist, sessions, Google flow, doctor profile."""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from app.models import AuditLog, Doctor

from conftest import DOCTOR_EMAIL, PROFILE, med, sign

FRONT = "http://frontend.test"

# Routes that are allowed to work without signing in. Adding a new open route must be a conscious
# decision: this list has to be edited too, and a reviewer will see it.
PUBLIC_API_ROUTES = {
    ("GET", "/api/auth/config"),
    ("GET", "/api/auth/google/login"),
    ("GET", "/api/auth/google/callback"),
    ("POST", "/api/auth/dev-login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/verify/{rx_id}"),
}


# ------------------------------------------------------------------ the login gate

def test_every_api_route_requires_login_except_the_known_public_ones(anon):
    open_routes, checked = set(), 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api"):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            path = route.path.replace("{rx_id}", "x").replace("{patient_id}", "1")
            response = anon.request(method, path, follow_redirects=False)
            checked += 1
            if response.status_code != 401:
                open_routes.add((method, route.path))
    assert checked >= 12  # guards against the loop silently checking nothing
    assert open_routes == PUBLIC_API_ROUTES


def test_the_only_public_html_page_is_the_verify_page():
    docs = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    pages = {r.path for r in app.routes if not r.path.startswith("/api")} - docs
    assert pages == {"/verify/{rx_id}"}


# ------------------------------------------------------------------ allowlist and dev login

def test_allowlisted_email_can_sign_in(anon):
    r = anon.post("/api/auth/dev-login", json={"email": DOCTOR_EMAIL})
    assert r.status_code == 200 and r.json()["email"] == DOCTOR_EMAIL
    assert anon.get("/api/auth/me").status_code == 200


def test_email_outside_the_allowlist_is_refused(anon):
    assert anon.post("/api/auth/dev-login", json={"email": "stranger@example.com"}).status_code == 403
    assert anon.get("/api/auth/me").status_code == 401


def test_allowlist_ignores_case_and_spaces(anon):
    assert anon.post("/api/auth/dev-login", json={"email": "  DOC@Test.COM "}).status_code == 200


def test_dev_login_does_not_exist_when_switched_off(anon, monkeypatch):
    monkeypatch.setenv("DEV_LOGIN", "0")
    assert anon.post("/api/auth/dev-login", json={"email": DOCTOR_EMAIL}).status_code == 404
    assert anon.get("/api/auth/config").json()["dev_login"] is False


def test_signing_in_twice_does_not_duplicate_the_doctor(anon, db):
    anon.post("/api/auth/dev-login", json={"email": DOCTOR_EMAIL})
    anon.post("/api/auth/dev-login", json={"email": DOCTOR_EMAIL})
    assert db.query(Doctor).filter_by(email=DOCTOR_EMAIL).count() == 1


def test_login_is_audited(anon, db):
    anon.post("/api/auth/dev-login", json={"email": DOCTOR_EMAIL})
    assert db.query(AuditLog).filter_by(event="login").count() == 1


# ------------------------------------------------------------------ sessions

def test_session_cookie_is_http_only(anon):
    r = anon.post("/api/auth/dev-login", json={"email": DOCTOR_EMAIL})
    assert "httponly" in r.headers["set-cookie"].lower()


def test_logout_ends_the_session(client):
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def _with_cookie(token):
    c = TestClient(app)
    c.cookies.set("session", token)
    return c


def _token(sub, secret=None, expires=timedelta(hours=1)):
    return jwt.encode({"sub": str(sub), "exp": datetime.now(timezone.utc) + expires},
                      secret or os.environ["JWT_SECRET"], algorithm="HS256")


def test_a_valid_token_works(client, db):
    doctor_id = db.query(Doctor).filter_by(email=DOCTOR_EMAIL).one().id
    assert _with_cookie(_token(doctor_id)).get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("make", [
    lambda i: _token(i, secret="some-other-secret"),     # forged
    lambda i: _token(i, expires=timedelta(minutes=-1)),  # expired
    lambda i: "garbage.not.a.token",
    lambda i: _token(99999),                             # valid signature, doctor does not exist
], ids=["forged", "expired", "garbage", "unknown-doctor"])
def test_bad_session_tokens_are_rejected(client, db, make):
    doctor_id = db.query(Doctor).filter_by(email=DOCTOR_EMAIL).one().id
    assert _with_cookie(make(doctor_id)).get("/api/auth/me").status_code == 401


def test_pdf_link_token_is_not_a_login_session(client, pid):
    """The short PDF link token must not be usable as a session cookie."""
    rx = sign(client, pid["Aisha Rahman"]).json()
    link_token = parse_qs(urlparse(rx["pdf_url"]).query)["t"][0]
    assert _with_cookie(link_token).get("/api/patients").status_code == 401


# ------------------------------------------------------------------ doctor profile

def test_new_doctor_starts_with_an_incomplete_profile(new_doctor):
    me = new_doctor.get("/api/auth/me").json()
    assert me["profile_complete"] is False and me["reg_no"] == ""


def test_profile_can_be_completed(new_doctor):
    r = new_doctor.put("/api/auth/me", json=PROFILE)
    assert r.status_code == 200 and r.json()["profile_complete"] is True


@pytest.mark.parametrize("bad", [
    {"name": "", "reg_no": "R1", "clinic": "C"},
    {"name": "Dr X", "reg_no": "", "clinic": "C"},
    {"name": "Dr X", "reg_no": "R1", "clinic": ""},
    {"name": "Dr X", "reg_no": "R" * 61, "clinic": "C"},
])
def test_invalid_profile_is_rejected(new_doctor, bad):
    assert new_doctor.put("/api/auth/me", json=bad).status_code == 422


def test_cannot_sign_before_completing_the_profile(new_doctor, pid):
    r = sign(new_doctor, 1)
    assert r.status_code == 400 and "profile" in r.json()["detail"].lower()


# ------------------------------------------------------------------ Google flow (Google itself is faked)

class FakeGoogle:
    """Replaces httpx.Client inside app.auth: the token call and the userinfo call."""

    def __init__(self, profile=None, fail=False):
        self.profile, self.fail = profile or {}, fail

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _resp(self, data):
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: data})()

    def post(self, url, data=None):
        if self.fail:
            raise httpx.ConnectError("google unreachable")
        return self._resp({"access_token": "tok"})

    def get(self, url, headers=None):
        return self._resp(self.profile)


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "client-secret")

    def start(client):
        r = client.get("/api/auth/google/login", follow_redirects=False)
        assert r.status_code == 307
        return r, parse_qs(urlparse(r.headers["location"]).query)["state"][0]

    def install(profile=None, fail=False):
        monkeypatch.setattr("app.auth.httpx.Client", FakeGoogle(profile, fail))

    return type("G", (), {"start": staticmethod(start), "install": staticmethod(install)})


def _profile(email=DOCTOR_EMAIL, verified=True):
    return {"email": email, "email_verified": verified, "name": "Dana Doctor", "sub": "g-123",
            "picture": "https://img.test/p.png"}


def test_google_login_is_unavailable_when_not_configured(anon):
    assert anon.get("/api/auth/google/login", follow_redirects=False).status_code == 503
    assert anon.get("/api/auth/config").json()["google"] is False


def test_google_login_redirects_to_google_with_a_state_cookie(anon, google):
    r, state = google.start(anon)
    query = parse_qs(urlparse(r.headers["location"]).query)
    assert r.headers["location"].startswith("https://accounts.google.com/")
    assert query["client_id"] == ["client-id"] and "email" in query["scope"][0]
    assert query["redirect_uri"] == ["http://testserver/api/auth/google/callback"]
    assert "httponly" in r.headers["set-cookie"].lower() and state


def test_google_login_for_an_allowlisted_doctor_creates_a_session(anon, google, db):
    google.install(_profile())
    _, state = google.start(anon)
    r = anon.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == FRONT
    me = anon.get("/api/auth/me").json()
    assert me["email"] == DOCTOR_EMAIL and me["picture"] == "https://img.test/p.png"
    assert db.query(Doctor).filter_by(email=DOCTOR_EMAIL).one().google_sub == "g-123"


def test_google_email_is_lowercased_before_the_allowlist_check(anon, google):
    google.install(_profile(email="DOC@Test.com"))
    _, state = google.start(anon)
    r = anon.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == FRONT


def test_google_account_outside_the_allowlist_gets_no_session(anon, google, db):
    google.install(_profile(email="stranger@gmail.com"))
    _, state = google.start(anon)
    r = anon.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == f"{FRONT}/?error=not_allowed"
    assert anon.get("/api/auth/me").status_code == 401
    assert db.query(Doctor).filter_by(email="stranger@gmail.com").count() == 0
    assert db.query(AuditLog).filter_by(event="login_denied").count() == 1


def test_unverified_google_email_is_refused(anon, google):
    google.install(_profile(verified=False))
    _, state = google.start(anon)
    r = anon.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == f"{FRONT}/?error=unverified"
    assert anon.get("/api/auth/me").status_code == 401


def test_callback_with_the_wrong_state_is_refused(anon, google):
    google.install(_profile())
    google.start(anon)
    r = anon.get("/api/auth/google/callback?code=abc&state=forged", follow_redirects=False)
    assert r.headers["location"] == f"{FRONT}/?error=bad_state"
    assert anon.get("/api/auth/me").status_code == 401


def test_callback_without_the_state_cookie_is_refused(anon, google):
    google.install(_profile())
    r = anon.get("/api/auth/google/callback?code=abc&state=anything", follow_redirects=False)
    assert r.headers["location"] == f"{FRONT}/?error=bad_state"


def test_user_cancelling_at_google_is_handled(anon, google):
    r = anon.get("/api/auth/google/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"] == f"{FRONT}/?error=cancelled"


def test_google_being_unreachable_is_handled(anon, google):
    google.install(fail=True)
    _, state = google.start(anon)
    r = anon.get(f"/api/auth/google/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == f"{FRONT}/?error=google_failed"
    assert anon.get("/api/auth/me").status_code == 401
