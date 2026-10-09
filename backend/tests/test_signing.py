"""Signing a prescription, and checking one later (the QR-code page)."""

import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import AuditLog, Prescription

from conftest import med, sign


def _issue(client, patient_id, **kw):
    r = sign(client, patient_id, **kw)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ------------------------------------------------------------------ signing rules

def test_sign_returns_an_id_and_a_pdf_link(client, pid):
    r = sign(client, pid["Aisha Rahman"])
    body = r.json()
    assert r.status_code == 200 and body["pdf_url"].startswith(f"/api/prescriptions/{body['id']}/pdf?t=")


def test_signature_is_stored_and_looks_like_sha256(client, pid, db):
    rx = db.get(Prescription, _issue(client, pid["Aisha Rahman"]))
    assert len(rx.signature) == 64 and int(rx.signature, 16) >= 0


def test_two_prescriptions_get_different_signatures(client, pid, db):
    a = db.get(Prescription, _issue(client, pid["Aisha Rahman"]))
    b = db.get(Prescription, _issue(client, pid["Aisha Rahman"]))
    assert a.signature != b.signature


def test_server_blocks_an_allergic_medicine_even_if_the_doctor_adds_it(client, pid):
    r = sign(client, pid["Aisha Rahman"], items=[med("Amoxicillin")])  # penicillin allergy
    assert r.status_code == 400 and "safety" in r.json()["detail"].lower()


def test_server_blocks_a_pregnancy_unsafe_medicine(client, pid):
    assert sign(client, pid["Sara Khan"], items=[med("Ibuprofen")]).status_code == 400


def test_one_unsafe_medicine_stops_the_whole_prescription(client, pid, db):
    r = sign(client, pid["Aisha Rahman"], items=[med("Paracetamol"), med("Amoxicillin")])
    assert r.status_code == 400 and db.query(Prescription).count() == 0


def test_medicine_outside_the_formulary_cannot_be_signed(client, pid):
    assert sign(client, pid["John Perera"], items=[med("Wonderdrug")]).status_code == 400


def test_empty_prescription_is_refused(client, pid):
    assert sign(client, pid["John Perera"], items=[]).status_code == 400


def test_unknown_patient_is_404(client):
    assert sign(client, 9999).status_code == 404


def test_only_known_fields_are_stored(client, pid, db):
    item = {**med("Paracetamol"), "evil": "<script>", "doctor_id": 1}
    rx = db.get(Prescription, _issue(client, pid["John Perera"], items=[item]))
    assert set(rx.items[0]) == {"name", "dose", "frequency", "duration", "instructions"}


def test_doctors_edits_are_kept(client, pid, db):
    rx = db.get(Prescription, _issue(client, pid["John Perera"],
                                     items=[med("Paracetamol", dose="250 mg", duration="2 days")]))
    assert rx.items[0]["dose"] == "250 mg" and rx.items[0]["duration"] == "2 days"


def test_signing_is_audited(client, pid, db):
    rx_id = _issue(client, pid["Aisha Rahman"])
    row = db.query(AuditLog).filter_by(event="rx_signed").one()
    assert row.detail["rx"] == rx_id and row.patient_id == pid["Aisha Rahman"]


def test_prescription_belongs_to_the_signing_doctor(client, pid, db):
    from app.models import Doctor
    rx = db.get(Prescription, _issue(client, pid["Aisha Rahman"]))
    assert db.get(Doctor, rx.doctor_id).email == "doc@test.com"


# ------------------------------------------------------------------ public verification

def test_genuine_prescription_verifies(anon, client, pid):
    rx_id = _issue(client, pid["Aisha Rahman"])
    data = anon.get(f"/api/verify/{rx_id}").json()  # anon: verification needs no login
    assert data["valid"] is True and data["doctor"] == "Dr. Test" and data["reg_no"] == "REG-TEST-1"


def test_verify_page_shows_issuer_and_medicines(anon, client, pid):
    rx_id = _issue(client, pid["Aisha Rahman"], items=[med("Paracetamol", "500 mg", "every 6 hours", "3 days")])
    page = anon.get(f"/verify/{rx_id}")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    html = page.text
    for expected in ("Authentic prescription", "Dr. Test", "REG-TEST-1", "Test Clinic", "Paracetamol", "500 mg"):
        assert expected in html
    assert page.headers["cache-control"] == "no-store"


def test_verify_page_never_reveals_who_the_patient_is(anon, client, pid):
    rx_id = _issue(client, pid["Aisha Rahman"], complaint="very private complaint")
    html = anon.get(f"/verify/{rx_id}").text
    for secret in ("Aisha", "Rahman", "penicillin", "very private complaint"):
        assert secret not in html
    assert "Aisha" not in anon.get(f"/api/verify/{rx_id}").text


def test_changed_medicines_make_the_prescription_invalid(anon, client, pid, db):
    rx_id = _issue(client, pid["Aisha Rahman"])
    rx = db.get(Prescription, rx_id)
    rx.items = [{**rx.items[0], "name": "Morphine"}]
    db.commit()
    assert anon.get(f"/api/verify/{rx_id}").json()["valid"] is False
    html = anon.get(f"/verify/{rx_id}").text
    assert "Not valid" in html and "Authentic" not in html and "Morphine" not in html


def test_changed_dose_makes_the_prescription_invalid(anon, client, pid, db):
    rx_id = _issue(client, pid["Aisha Rahman"])
    rx = db.get(Prescription, rx_id)
    rx.items = [{**rx.items[0], "dose": "5000 mg"}]
    db.commit()
    assert anon.get(f"/api/verify/{rx_id}").json()["valid"] is False


def test_changed_date_makes_the_prescription_invalid(anon, client, pid, db):
    rx_id = _issue(client, pid["Aisha Rahman"])
    db.get(Prescription, rx_id).issued_at = "2020-01-01 00:00 UTC"
    db.commit()
    assert anon.get(f"/api/verify/{rx_id}").json()["valid"] is False


def test_prescription_moved_to_another_patient_is_invalid(anon, client, pid, db):
    rx_id = _issue(client, pid["Aisha Rahman"])
    db.get(Prescription, rx_id).patient_id = pid["John Perera"]
    db.commit()
    assert anon.get(f"/api/verify/{rx_id}").json()["valid"] is False


def test_unknown_prescription_is_not_found(anon):
    assert anon.get("/api/verify/does-not-exist").json() == {"valid": False}
    assert "Prescription not found" in anon.get("/verify/does-not-exist").text


def test_verify_page_escapes_html_in_free_text_fields(anon, client, pid):
    rx_id = _issue(client, pid["Aisha Rahman"],
                   items=[med("Paracetamol", dose="<script>alert(1)</script>", frequency='"><img src=x onerror=alert(2)>')])
    html = anon.get(f"/verify/{rx_id}").text
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


# ------------------------------------------------------------------ PDF access

def _link_token(url):
    return url.split("t=")[1]


def test_pdf_opens_with_its_link_and_no_login(anon, client, pid):
    rx = sign(client, pid["Aisha Rahman"]).json()
    r = anon.get(rx["pdf_url"])
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"


def test_pdf_opens_for_a_signed_in_doctor_without_a_link(client, pid):
    rx = sign(client, pid["Aisha Rahman"]).json()
    assert client.get(f"/api/prescriptions/{rx['id']}/pdf").status_code == 200


def test_pdf_needs_a_link_or_a_login(anon, client, pid):
    rx = sign(client, pid["Aisha Rahman"]).json()
    assert anon.get(f"/api/prescriptions/{rx['id']}/pdf").status_code == 401
    assert anon.get(f"/api/prescriptions/{rx['id']}/pdf?t=garbage").status_code == 401


def test_pdf_link_only_works_for_its_own_prescription(anon, client, pid):
    one = sign(client, pid["Aisha Rahman"]).json()
    two = sign(client, pid["John Perera"]).json()
    assert anon.get(f"/api/prescriptions/{two['id']}/pdf?t={_link_token(one['pdf_url'])}").status_code == 401


def test_pdf_link_expires(anon, client, pid):
    rx = sign(client, pid["Aisha Rahman"]).json()
    old = jwt.encode({"rx": rx["id"], "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                     os.environ["JWT_SECRET"], algorithm="HS256")
    assert anon.get(f"/api/prescriptions/{rx['id']}/pdf?t={old}").status_code == 401


def test_pdf_link_signed_with_another_key_is_rejected(anon, client, pid):
    rx = sign(client, pid["Aisha Rahman"]).json()
    forged = jwt.encode({"rx": rx["id"], "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
                        "wrong-key", algorithm="HS256")
    assert anon.get(f"/api/prescriptions/{rx['id']}/pdf?t={forged}").status_code == 401


def test_pdf_for_unknown_prescription_is_404(client):
    assert client.get("/api/prescriptions/nope/pdf").status_code == 404
