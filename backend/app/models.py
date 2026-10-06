import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now():
    return datetime.now(timezone.utc)


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    reg_no: Mapped[str] = mapped_column(String(60), default="")
    clinic: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), nullable=True)
    picture: Mapped[str | None] = mapped_column(String(400), nullable=True)


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    age: Mapped[int] = mapped_column(Integer)
    sex: Mapped[str] = mapped_column(String(10))
    weight_kg: Mapped[float] = mapped_column(Float)
    pregnant: Mapped[bool] = mapped_column(Boolean, default=False)
    allergies: Mapped[list] = mapped_column(JSON, default=list)
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    current_meds: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=_now, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Medicine(Base):
    """Clinic formulary. The AI may only suggest medicines from this table."""

    __tablename__ = "medicines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    drug_class: Mapped[str] = mapped_column(String(60))
    typical_dose: Mapped[str] = mapped_column(String(200))
    allergy_class: Mapped[str] = mapped_column(String(60), default="")
    interacts_with: Mapped[list] = mapped_column(JSON, default=list)
    pregnancy_safe: Mapped[bool] = mapped_column(Boolean, default=True)
    min_age: Mapped[int] = mapped_column(Integer, default=0)


class Prescription(Base):
    __tablename__ = "prescriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    complaint: Mapped[str] = mapped_column(Text)
    assessments: Mapped[list] = mapped_column(JSON, default=list)
    items: Mapped[list] = mapped_column(JSON, default=list)
    issued_at: Mapped[str] = mapped_column(String(40))
    signature: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    event: Mapped[str] = mapped_column(String(40))
    patient_id: Mapped[int] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
