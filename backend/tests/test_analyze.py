"""How the app treats the AI's answer. The real model is replaced by a scripted fake."""

import json
from types import SimpleNamespace

import pytest

from app import ai
from app.models import AuditLog

from conftest import analyze, med, sign


# ------------------------------------------------------------------ safety applied to AI output

def test_medicine_outside_the_formulary_is_dropped(client, pid, fake_ai):
    fake_ai.ready(med("Wonderdrug"), med("Paracetamol"))
    r = analyze(client, pid["John Perera"]).json()
    assert [m["name"] for m in r["medicines"]] == ["Paracetamol"]
    assert r["removed"] == [{"name": "Wonderdrug", "reason": "Not in clinic formulary"}]


def test_medicine_name_matching_ignores_case(client, pid, fake_ai):
    fake_ai.ready(med("paracetamol"))
    r = analyze(client, pid["John Perera"]).json()
    assert [m["name"] for m in r["medicines"]] == ["Paracetamol"]  # canonical formulary spelling


def test_penicillin_allergy_blocks_amoxicillin(client, pid, fake_ai):
    fake_ai.ready(med("Amoxicillin"), med("Paracetamol"))
    r = analyze(client, pid["Aisha Rahman"]).json()  # allergic to penicillin
    assert [m["name"] for m in r["medicines"]] == ["Paracetamol"]
    assert r["removed"][0]["name"] == "Amoxicillin"
    assert "penicillin" in r["removed"][0]["reason"].lower()


def test_pregnancy_blocks_nsaids(client, pid, fake_ai):
    fake_ai.ready(med("Ibuprofen"), med("Paracetamol"))
    r = analyze(client, pid["Sara Khan"]).json()  # pregnant
    assert [m["name"] for m in r["medicines"]] == ["Paracetamol"]
    assert "pregnan" in r["removed"][0]["reason"].lower()


def test_child_is_not_given_an_adult_only_medicine(client, pid, fake_ai):
    fake_ai.ready(med("Ciprofloxacin"), med("Paracetamol"))
    r = analyze(client, pid["Maya Silva"]).json()  # 7 years old
    assert [m["name"] for m in r["medicines"]] == ["Paracetamol"]


def test_interaction_is_kept_as_a_warning_for_the_doctor(client, pid, fake_ai):
    fake_ai.ready(med("Ibuprofen"))
    r = analyze(client, pid["John Perera"]).json()  # takes warfarin
    assert [m["name"] for m in r["medicines"]] == ["Ibuprofen"]
    assert r["medicines"][0]["warnings"][0]["severity"] == "warn"
    assert "warfarin" in r["medicines"][0]["warnings"][0]["message"].lower()


def test_clean_medicine_has_empty_warnings(client, pid, fake_ai):
    fake_ai.ready(med("Paracetamol"))
    assert analyze(client, pid["John Perera"]).json()["medicines"][0]["warnings"] == []


def test_editing_an_allergy_changes_what_is_allowed(client, pid, fake_ai):
    fake_ai.ready(med("Ibuprofen"))
    pat = next(p for p in client.get("/api/patients").json() if p["name"] == "Maya Silva")
    pat["age"] = 30
    assert client.put(f"/api/patients/{pat['id']}", json=pat).status_code == 200
    assert analyze(client, pat["id"]).json()["removed"] == []
    pat["allergies"] = ["nsaid"]
    assert client.put(f"/api/patients/{pat['id']}", json=pat).status_code == 200
    r = analyze(client, pat["id"]).json()
    assert r["medicines"] == [] and r["removed"][0]["name"] == "Ibuprofen"


# ------------------------------------------------------------------ question flow

def test_needs_info_returns_questions_and_no_medicines(client, pid, fake_ai):
    fake_ai.needs_info("How long?", "Any fever?", medicines=[med("Paracetamol")])
    r = analyze(client, pid["John Perera"]).json()
    assert r["status"] == "needs_info"
    assert r["questions"] == ["How long?", "Any fever?"]
    assert r["medicines"] == []  # the app ignores medicines sent alongside questions


def test_at_most_three_questions_are_returned(client, pid, fake_ai):
    fake_ai.needs_info("q1", "q2", "q3", "q4", "q5")
    assert len(analyze(client, pid["John Perera"]).json()["questions"]) == 3


def test_doctors_answers_are_passed_to_the_model(client, pid, fake_ai):
    qa = [{"question": "How long?", "answer": "3 days"}]
    fake_ai.ready(med("Paracetamol"))
    analyze(client, pid["John Perera"], complaint="fever", qa=qa)
    assert fake_ai.calls[0]["qa"] == qa
    assert fake_ai.calls[0]["complaint"] == "fever"


def test_model_only_sees_the_clinic_formulary(client, pid, fake_ai):
    fake_ai.ready(med("Paracetamol"))
    analyze(client, pid["John Perera"])
    names = {m["name"] for m in fake_ai.calls[0]["formulary"]}
    assert "Paracetamol" in names and len(names) == 17


# ------------------------------------------------------------------ failures

def test_unknown_patient_is_404(client, fake_ai):
    assert analyze(client, 9999).status_code == 404
    assert fake_ai.calls == []  # the model is never called


def test_ai_failure_becomes_a_clear_502(client, pid, fake_ai):
    fake_ai.error = RuntimeError("All AI providers failed: boom")
    r = analyze(client, pid["John Perera"])
    assert r.status_code == 502 and "providers failed" in r.json()["detail"]


# ------------------------------------------------------------------ privacy and audit

def test_patient_identity_is_never_sent_to_the_model(client, pid, fake_ai):
    fake_ai.ready(med("Paracetamol"))
    analyze(client, pid["Aisha Rahman"])
    sent = fake_ai.calls[0]["patient"]
    assert "name" not in sent and "id" not in sent
    everything = json.dumps(fake_ai.calls[0], default=str)
    assert "Aisha" not in everything and "Rahman" not in everything
    assert sent["allergies"] == ["penicillin"]  # clinical facts are still sent


def test_previous_visits_are_sent_without_identity(client, pid, fake_ai):
    assert sign(client, pid["Aisha Rahman"], complaint="old cough").status_code == 200
    fake_ai.ready(med("Paracetamol"))
    analyze(client, pid["Aisha Rahman"])
    prev = fake_ai.calls[0]["previous_visits"]
    assert len(prev) == 1 and prev[0]["medicines"] == ["Paracetamol"]
    assert "Aisha" not in json.dumps(fake_ai.calls[0], default=str)


def test_every_analysis_is_audited(client, pid, fake_ai, db):
    fake_ai.ready(med("Amoxicillin"))
    analyze(client, pid["Aisha Rahman"])
    row = db.query(AuditLog).filter_by(event="ai_analyze").one()
    assert row.patient_id == pid["Aisha Rahman"]
    assert row.detail["removed"][0]["name"] == "Amoxicillin"
    assert row.detail["provider"] == "fake"


# ------------------------------------------------------------------ provider fallback (app/ai.py)

class _Completions:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, []

    def create(self, model, messages, response_format):
        self.calls.append({"model": model, "messages": messages})
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def _provider(name, **kw):
    comp = _Completions(**kw)
    return (name, SimpleNamespace(chat=SimpleNamespace(completions=comp)), f"{name}-model"), comp


def _call():
    return ai.analyze({"age": 30}, "cough", [], [{"name": "Paracetamol"}], [])


def test_falls_back_to_groq_when_azure_fails(monkeypatch):
    azure, _ = _provider("azure", error=RuntimeError("azure down"))
    groq, _ = _provider("groq", content='{"status":"ready"}')
    monkeypatch.setattr(ai, "_providers", lambda: [azure, groq])
    assert _call()["_provider"] == "groq"


def test_azure_is_used_when_it_works(monkeypatch):
    azure, _ = _provider("azure", content='{"status":"ready"}')
    groq, groq_calls = _provider("groq", content='{"status":"ready"}')
    monkeypatch.setattr(ai, "_providers", lambda: [azure, groq])
    assert _call()["_provider"] == "azure"
    assert groq_calls.calls == []  # the fallback is not touched


def test_invalid_json_from_a_provider_triggers_the_fallback(monkeypatch):
    azure, _ = _provider("azure", content="not json at all")
    groq, _ = _provider("groq", content='{"status":"ready"}')
    monkeypatch.setattr(ai, "_providers", lambda: [azure, groq])
    assert _call()["_provider"] == "groq"


def test_error_when_every_provider_fails(monkeypatch):
    a, _ = _provider("azure", error=RuntimeError("a"))
    g, _ = _provider("groq", error=RuntimeError("g"))
    monkeypatch.setattr(ai, "_providers", lambda: [a, g])
    with pytest.raises(RuntimeError, match="All AI providers failed"):
        _call()


def test_error_when_no_provider_is_configured(monkeypatch):
    monkeypatch.setattr(ai, "_providers", lambda: [])
    with pytest.raises(RuntimeError, match="All AI providers failed"):
        _call()


def test_model_is_told_to_stop_asking_after_enough_answers(monkeypatch):
    p, comp = _provider("azure", content='{"status":"ready"}')
    monkeypatch.setattr(ai, "_providers", lambda: [p])
    qa = [{"question": f"q{i}", "answer": "a"} for i in range(ai.MAX_ANSWERED)]
    ai.analyze({"age": 30}, "cough", qa, [], [])
    sent = json.loads(comp.calls[0]["messages"][1]["content"])
    assert "no more questions" in sent["instruction"].lower()


def test_providers_list_is_empty_without_keys():
    assert ai._providers() == []  # conftest removed the real keys
