"""Shared test setup.

Safety rails (do not remove):
  * Tests run against their own database. The name must end in "_test", because every test
    drops and recreates all tables.
  * Real LLM keys are removed from the environment, so no test can ever call Azure or Groq.
"""

import os

import psycopg2
from sqlalchemy.engine import make_url

_TEST_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. Run the tests with: docker compose --profile test run --rm tests"
    )
_url = make_url(_TEST_URL)
if not (_url.database or "").endswith("_test"):
    raise RuntimeError(f"Refusing to run: test database must end in '_test', got '{_url.database}'")


def _ensure_database() -> None:
    conn = psycopg2.connect(host=_url.host, port=_url.port or 5432, user=_url.username,
                            password=_url.password, dbname="postgres")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_url.database,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{_url.database}"')
    conn.close()


_ensure_database()

# Must happen before anything imports `app` (the engine is created at import time).
os.environ.update({
    "DATABASE_URL": _TEST_URL,
    "JWT_SECRET": "test-jwt-secret-not-for-production",
    "SIGNING_SECRET": "test-signing-secret-not-for-production",
    "ALLOWED_DOCTOR_EMAILS": "doc@test.com, other@test.com",
    "DEV_LOGIN": "1",
    "PUBLIC_BASE_URL": "http://testserver",
    "FRONTEND_URL": "http://frontend.test",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "GOOGLE_REDIRECT_URI": "http://testserver/api/auth/google/callback",
})
for _key in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "GROQ_API_KEY"):
    os.environ.pop(_key, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

DOCTOR_EMAIL = "doc@test.com"
PROFILE = {"name": "Dr. Test", "reg_no": "REG-TEST-1", "clinic": "Test Clinic"}


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts from the same seeded database (4 patients, 17 medicines, 1 demo doctor)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def anon():
    """A client that is not signed in."""
    return TestClient(app)


@pytest.fixture
def client():
    """A signed-in doctor with a complete profile."""
    c = TestClient(app)
    assert c.post("/api/auth/dev-login", json={"email": DOCTOR_EMAIL}).status_code == 200
    assert c.put("/api/auth/me", json=PROFILE).status_code == 200
    return c


@pytest.fixture
def new_doctor():
    """A signed-in doctor who has NOT completed their profile yet."""
    c = TestClient(app)
    assert c.post("/api/auth/dev-login", json={"email": "other@test.com"}).status_code == 200
    return c


@pytest.fixture
def pid(client):
    """Patient ids by name, e.g. pid["Aisha Rahman"]."""
    return {p["name"]: p["id"] for p in client.get("/api/patients").json()}


def med(name, dose="1 tab", frequency="twice daily", duration="3 days", instructions=""):
    return {"name": name, "dose": dose, "frequency": frequency, "duration": duration,
            "instructions": instructions, "for_condition": "test"}


class FakeAI:
    """Stands in for app.ai.analyze. Records what the app sent and returns a scripted answer."""

    def __init__(self):
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.response: dict = {"status": "ready", "questions": [], "assessments": [], "medicines": [],
                               "red_flags": []}

    def ready(self, *medicines, assessments=None, red_flags=None):
        self.response = {"status": "ready", "questions": [], "medicines": list(medicines),
                         "assessments": assessments or [{"condition": "Test condition",
                                                          "likelihood": "most_likely", "reasoning": "r"}],
                         "red_flags": red_flags or []}

    def needs_info(self, *questions, medicines=()):
        self.response = {"status": "needs_info", "questions": list(questions), "assessments": [],
                         "medicines": list(medicines), "red_flags": []}

    def __call__(self, patient, complaint, qa, formulary, previous_visits=None):
        self.calls.append({"patient": patient, "complaint": complaint, "qa": qa,
                           "formulary": formulary, "previous_visits": previous_visits})
        if self.error:
            raise self.error
        return {**self.response, "_provider": "fake"}


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(ai, "analyze", fake)
    return fake


def sign(client, patient_id, items=None, complaint="sore throat"):
    items = items if items is not None else [med("Paracetamol", "500 mg", "every 6 hours", "3 days")]
    return client.post("/api/prescriptions", json={"patient_id": patient_id, "complaint": complaint,
                                                    "assessments": [], "items": items})


def analyze(client, patient_id, complaint="cough and fever", qa=None):
    return client.post("/api/analyze", json={"patient_id": patient_id, "complaint": complaint, "qa": qa or []})
