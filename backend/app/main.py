import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import Cookie, Depends, FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ai, auth, pdf, safety
from .auth import current_doctor
from .db import Base, SessionLocal, engine, get_db
from .models import AuditLog, Doctor, Medicine, Patient, Prescription
from .seed import seed

# create_all() never alters existing tables, so new columns are added here (idempotent).
MIGRATIONS = [
    "ALTER TABLE doctors ADD COLUMN IF NOT EXISTS email VARCHAR(200)",
    "ALTER TABLE doctors ADD COLUMN IF NOT EXISTS google_sub VARCHAR(64)",
    "ALTER TABLE doctors ADD COLUMN IF NOT EXISTS picture VARCHAR(400)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_doctors_email ON doctors (email)",
    "ALTER TABLE patients ADD COLUMN IF NOT EXISTS created_by INTEGER",
    "ALTER TABLE patients ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now()",
    "ALTER TABLE patients ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in MIGRATIONS:
            conn.execute(text(stmt))
    with SessionLocal() as db:
        seed(db)
    yield


app = FastAPI(title="Clinic AI", lifespan=lifespan)
app.include_router(auth.router)


def _secret() -> bytes:
    s = os.getenv("SIGNING_SECRET")
    if not s:
        raise HTTPException(500, "SIGNING_SECRET is not configured")
    return s.encode()


def _sign(rx_id: str, patient_id: int, doctor_id: int, items: list, issued_at: str) -> str:
    payload = json.dumps(
        {"id": rx_id, "patient": patient_id, "doctor": doctor_id, "items": items, "issued_at": issued_at},
        sort_keys=True, separators=(",", ":"),
    )
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def _patient_dict(p: Patient) -> dict:
    return {"id": p.id, "name": p.name, "age": p.age, "sex": p.sex, "weight_kg": p.weight_kg,
            "pregnant": p.pregnant, "allergies": p.allergies, "conditions": p.conditions,
            "current_meds": p.current_meds}


# ---------------------------------------------------------------- patients

class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    age: int = Field(ge=0, le=130)
    sex: Literal["male", "female", "other"]
    weight_kg: float = Field(gt=0, le=400)
    pregnant: bool = False
    allergies: list[str] = []
    conditions: list[str] = []
    current_meds: list[str] = []

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        return v

    @field_validator("allergies", "conditions", "current_meds")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            item = item.strip()[:100]
            if item and item.lower() not in (o.lower() for o in out):
                out.append(item)
        if len(out) > 50:
            raise ValueError("too many entries")
        return out

    @model_validator(mode="after")
    def _pregnancy(self):
        if self.pregnant and self.sex == "male":
            raise ValueError("a male patient cannot be pregnant")
        return self


@app.get("/api/patients")
def patients(db: Session = Depends(get_db), _: Doctor = Depends(current_doctor)):
    return [_patient_dict(p) for p in db.query(Patient).order_by(Patient.name)]


@app.post("/api/patients", status_code=201)
def create_patient(body: PatientIn, db: Session = Depends(get_db), doctor: Doctor = Depends(current_doctor)):
    p = Patient(**body.model_dump(), created_by=doctor.id)
    db.add(p)
    db.flush()
    db.add(AuditLog(event="patient_created", patient_id=p.id,
                    detail={"doctor": doctor.id, "data": _patient_dict(p)}))
    db.commit()
    return _patient_dict(p)


@app.put("/api/patients/{patient_id}")
def update_patient(patient_id: int, body: PatientIn, db: Session = Depends(get_db),
                   doctor: Doctor = Depends(current_doctor)):
    p = db.get(Patient, patient_id)
    if not p:
        raise HTTPException(404, "Patient not found")
    before = _patient_dict(p)
    for k, v in body.model_dump().items():
        setattr(p, k, v)
    p.updated_at = datetime.now(timezone.utc)
    after = _patient_dict(p)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    db.add(AuditLog(event="patient_updated", patient_id=p.id, detail={"doctor": doctor.id, "changes": changes}))
    db.commit()
    return after


def _visits(db: Session, patient_id: int, limit: int = 50) -> list[Prescription]:
    return (db.query(Prescription).filter(Prescription.patient_id == patient_id)
            .order_by(Prescription.created_at.desc()).limit(limit).all())


def _pdf_url(rx_id: str) -> str:
    return f"/api/prescriptions/{rx_id}/pdf?t={auth.pdf_token(rx_id)}"


@app.get("/api/patients/{patient_id}/history")
def history(patient_id: int, db: Session = Depends(get_db), doctor: Doctor = Depends(current_doctor)):
    if not db.get(Patient, patient_id):
        raise HTTPException(404, "Patient not found")
    doctors = {d.id: d.name for d in db.query(Doctor)}
    db.add(AuditLog(event="history_viewed", patient_id=patient_id, detail={"doctor": doctor.id}))
    db.commit()
    return [{"id": r.id, "issued_at": r.issued_at, "doctor": doctors.get(r.doctor_id, ""),
             "complaint": r.complaint, "assessments": r.assessments, "items": r.items,
             "pdf_url": _pdf_url(r.id)} for r in _visits(db, patient_id)]


# ---------------------------------------------------------------- AI analysis

class AnalyzeIn(BaseModel):
    patient_id: int
    complaint: str
    qa: list[dict] = []  # [{"question": "...", "answer": "..."}]


@app.post("/api/analyze")
def analyze(body: AnalyzeIn, db: Session = Depends(get_db), doctor: Doctor = Depends(current_doctor)):
    patient = db.get(Patient, body.patient_id)
    if not patient:
        raise HTTPException(404, "Patient not found")
    meds = {m.name.lower(): m for m in db.query(Medicine)}
    formulary = [{"name": m.name, "class": m.drug_class, "typical_dose": m.typical_dose} for m in meds.values()]
    # de-identified: no name / contact / ID is sent to the model
    anon = {k: v for k, v in _patient_dict(patient).items() if k not in ("id", "name")}
    previous = [{"date": r.issued_at, "complaint": r.complaint[:300],
                 "conditions": [a.get("condition") for a in r.assessments[:2]],
                 "medicines": [i["name"] for i in r.items]} for r in _visits(db, patient.id, 5)]
    try:
        data = ai.analyze(anon, body.complaint, body.qa, formulary, previous)
    except Exception as e:
        raise HTTPException(502, str(e))

    result = {
        "status": "needs_info" if data.get("status") == "needs_info" else "ready",
        "questions": data.get("questions", [])[:3],
        "assessments": data.get("assessments", []),
        "red_flags": data.get("red_flags", []),
        "medicines": [],
        "removed": [],
        "provider": data.get("_provider"),
    }
    if result["status"] == "ready":
        for item in data.get("medicines", []):
            med = meds.get(str(item.get("name", "")).lower())
            if not med:
                result["removed"].append({"name": item.get("name"), "reason": "Not in clinic formulary"})
                continue
            item["name"] = med.name
            warnings = safety.check(med, patient)
            if any(w["severity"] == "block" for w in warnings):
                result["removed"].append({"name": med.name, "reason": "; ".join(w["message"] for w in warnings)})
                continue
            item["warnings"] = warnings
            result["medicines"].append(item)
    db.add(AuditLog(event="ai_analyze", patient_id=patient.id,
                    detail={"doctor": doctor.id, "status": result["status"], "provider": result["provider"],
                            "medicines": [m["name"] for m in result["medicines"]],
                            "removed": result["removed"]}))
    db.commit()
    return result


# ---------------------------------------------------------------- prescriptions

class RxIn(BaseModel):
    patient_id: int
    complaint: str
    assessments: list[dict] = []
    items: list[dict]


@app.post("/api/prescriptions")
def create_rx(body: RxIn, db: Session = Depends(get_db), doctor: Doctor = Depends(current_doctor)):
    patient = db.get(Patient, body.patient_id)
    if not patient:
        raise HTTPException(404, "Patient not found")
    if not (doctor.reg_no and doctor.clinic):
        raise HTTPException(400, "Complete your doctor profile (registration number and clinic) before signing")
    if not body.items:
        raise HTTPException(400, "Prescription has no medicines")
    meds = {m.name.lower(): m for m in db.query(Medicine)}
    items = []
    for it in body.items:  # re-check server side: the doctor's edits are not trusted either
        med = meds.get(str(it.get("name", "")).lower())
        if not med:
            raise HTTPException(400, f"{it.get('name')} is not in the formulary")
        if any(w["severity"] == "block" for w in safety.check(med, patient)):
            raise HTTPException(400, f"{med.name} fails safety checks for this patient")
        items.append({k: it.get(k, "") for k in ("name", "dose", "frequency", "duration", "instructions")})
    rx = Prescription(patient_id=patient.id, doctor_id=doctor.id, complaint=body.complaint,
                      assessments=body.assessments, items=items,
                      issued_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), signature="")
    db.add(rx)
    db.flush()
    rx.signature = _sign(rx.id, patient.id, doctor.id, items, rx.issued_at)
    db.add(AuditLog(event="rx_signed", patient_id=patient.id,
                    detail={"doctor": doctor.id, "rx": rx.id, "items": items}))
    db.commit()
    return {"id": rx.id, "pdf_url": _pdf_url(rx.id)}


@app.get("/api/prescriptions/{rx_id}/pdf")
def rx_pdf(rx_id: str, t: str | None = None, session: str | None = Cookie(default=None),
           db: Session = Depends(get_db)):
    if not (auth.pdf_token_valid(t, rx_id) or auth._doctor_from_cookie(session, db)):
        raise HTTPException(401, "Not signed in")
    rx = db.get(Prescription, rx_id)
    if not rx:
        raise HTTPException(404, "Not found")
    data = pdf.build(rx, db.get(Doctor, rx.doctor_id), db.get(Patient, rx.patient_id))
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="rx-{rx.id[:8]}.pdf"'})


@app.get("/api/verify/{rx_id}")
def verify(rx_id: str, db: Session = Depends(get_db)):
    """Public on purpose: the QR code on the PDF points here. Reveals no patient data."""
    rx = db.get(Prescription, rx_id)
    if not rx:
        return {"valid": False}
    expected = _sign(rx.id, rx.patient_id, rx.doctor_id, rx.items, rx.issued_at)
    doctor = db.get(Doctor, rx.doctor_id)
    return {"valid": hmac.compare_digest(expected, rx.signature), "issued_at": rx.issued_at,
            "doctor": doctor.name, "reg_no": doctor.reg_no}
