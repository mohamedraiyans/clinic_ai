"""App startup: database setup, migrations and seed data."""

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.db import SessionLocal, engine
from app.main import app
from app.models import Doctor, Medicine, Patient
from app.seed import seed


def test_startup_can_run_repeatedly():
    with TestClient(app):
        pass
    with TestClient(app):
        pass


def test_startup_adds_columns_missing_from_an_older_database():
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE patients DROP COLUMN created_by"))
        conn.execute(text("ALTER TABLE doctors DROP COLUMN google_sub"))
    with TestClient(app):
        pass
    assert "created_by" in {c["name"] for c in inspect(engine).get_columns("patients")}
    assert "google_sub" in {c["name"] for c in inspect(engine).get_columns("doctors")}


def test_seeding_twice_does_not_duplicate_data():
    with SessionLocal() as db:
        seed(db)
        seed(db)
        assert db.query(Medicine).count() == 17
        assert db.query(Patient).count() == 4
        assert db.query(Doctor).count() == 1


def test_seeded_patients_cover_the_safety_scenarios():
    with SessionLocal() as db:
        by_name = {p.name: p for p in db.query(Patient)}
    assert by_name["Aisha Rahman"].allergies == ["penicillin"]
    assert by_name["Sara Khan"].pregnant is True
    assert "warfarin" in by_name["John Perera"].current_meds
    assert by_name["Maya Silva"].age < 12
