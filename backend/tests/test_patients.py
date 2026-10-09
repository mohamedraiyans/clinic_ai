"""Adding and editing patients: validation, cleaning and the audit trail."""

import pytest

from app.models import AuditLog, Doctor, Patient

from conftest import DOCTOR_EMAIL, sign

VALID = {"name": "Ravi Kumar", "age": 41, "sex": "male", "weight_kg": 75, "pregnant": False,
         "allergies": ["Sulfa"], "conditions": ["asthma"], "current_meds": []}


def test_seeded_patients_are_listed_by_name(client):
    names = [p["name"] for p in client.get("/api/patients").json()]
    assert names == sorted(names) and len(names) == 4


def test_create_patient(client, db):
    r = client.post("/api/patients", json=VALID)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Ravi Kumar" and body["allergies"] == ["Sulfa"]
    saved = db.get(Patient, body["id"])
    assert saved.created_by == db.query(Doctor).filter_by(email=DOCTOR_EMAIL).one().id


def test_new_patient_appears_in_the_list(client):
    client.post("/api/patients", json=VALID)
    assert "Ravi Kumar" in [p["name"] for p in client.get("/api/patients").json()]


def test_input_is_cleaned(client):
    r = client.post("/api/patients", json={**VALID, "name": "  Ravi  ",
                                           "allergies": ["Sulfa", " sulfa ", "", "  ", "Latex"]})
    assert r.json()["name"] == "Ravi"
    assert r.json()["allergies"] == ["Sulfa", "Latex"]  # duplicates (any case) and blanks removed


@pytest.mark.parametrize("change", [
    {"age": -1}, {"age": 131}, {"weight_kg": 0}, {"weight_kg": -5}, {"weight_kg": 401},
    {"sex": "robot"}, {"name": ""}, {"name": "   "}, {"name": "x" * 121},
    {"sex": "male", "pregnant": True},
    {"allergies": [f"a{i}" for i in range(51)]},
    {"age": "old"},
])
def test_invalid_patients_are_rejected(client, change):
    assert client.post("/api/patients", json={**VALID, **change}).status_code == 422


def test_female_patient_can_be_pregnant(client):
    r = client.post("/api/patients", json={**VALID, "sex": "female", "pregnant": True})
    assert r.status_code == 201 and r.json()["pregnant"] is True


def test_creating_a_patient_is_audited(client, db):
    pid = client.post("/api/patients", json=VALID).json()["id"]
    row = db.query(AuditLog).filter_by(event="patient_created", patient_id=pid).one()
    assert row.detail["data"]["name"] == "Ravi Kumar"


def test_update_patient(client, pid):
    update = {**VALID, "name": "Aisha Rahman", "age": 35, "allergies": ["penicillin", "latex"]}
    r = client.put(f"/api/patients/{pid['Aisha Rahman']}", json=update)
    assert r.status_code == 200 and r.json()["age"] == 35
    again = next(p for p in client.get("/api/patients").json() if p["id"] == pid["Aisha Rahman"])
    assert again["allergies"] == ["penicillin", "latex"]


def test_update_records_what_changed(client, db, pid):
    original = next(p for p in client.get("/api/patients").json() if p["name"] == "Aisha Rahman")
    update = {k: v for k, v in original.items() if k != "id"}
    update["allergies"] = ["penicillin", "nsaid"]
    update["age"] = 35
    client.put(f"/api/patients/{original['id']}", json=update)
    row = db.query(AuditLog).filter_by(event="patient_updated", patient_id=original["id"]).one()
    assert row.detail["changes"] == {
        "age": {"from": 34, "to": 35},
        "allergies": {"from": ["penicillin"], "to": ["penicillin", "nsaid"]},
    }


def test_update_that_changes_nothing_records_no_changes(client, db, pid):
    original = next(p for p in client.get("/api/patients").json() if p["name"] == "Aisha Rahman")
    client.put(f"/api/patients/{original['id']}", json={k: v for k, v in original.items() if k != "id"})
    row = db.query(AuditLog).filter_by(event="patient_updated").one()
    assert row.detail["changes"] == {}


def test_update_rejects_invalid_data(client, pid):
    assert client.put(f"/api/patients/{pid['Aisha Rahman']}", json={**VALID, "age": 500}).status_code == 422


def test_update_unknown_patient_is_404(client):
    assert client.put("/api/patients/9999", json=VALID).status_code == 404


# ------------------------------------------------------------------ history

def test_history_is_empty_for_a_new_patient(client, pid):
    assert client.get(f"/api/patients/{pid['Aisha Rahman']}/history").json() == []


def test_history_lists_signed_visits_newest_first(client, pid):
    sign(client, pid["Aisha Rahman"], complaint="first visit")
    sign(client, pid["Aisha Rahman"], complaint="second visit")
    visits = client.get(f"/api/patients/{pid['Aisha Rahman']}/history").json()
    assert [v["complaint"] for v in visits] == ["second visit", "first visit"]
    assert visits[0]["doctor"] == "Dr. Test" and visits[0]["items"][0]["name"] == "Paracetamol"


def test_history_only_contains_that_patients_visits(client, pid):
    sign(client, pid["Aisha Rahman"])
    assert client.get(f"/api/patients/{pid['John Perera']}/history").json() == []


def test_history_pdf_links_work(client, pid):
    sign(client, pid["Aisha Rahman"])
    link = client.get(f"/api/patients/{pid['Aisha Rahman']}/history").json()[0]["pdf_url"]
    assert client.get(link).headers["content-type"] == "application/pdf"


def test_viewing_history_is_audited(client, db, pid):
    client.get(f"/api/patients/{pid['Aisha Rahman']}/history")
    assert db.query(AuditLog).filter_by(event="history_viewed", patient_id=pid["Aisha Rahman"]).count() == 1


def test_history_of_unknown_patient_is_404(client):
    assert client.get("/api/patients/9999/history").status_code == 404
