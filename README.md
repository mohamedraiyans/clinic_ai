# Clinic AI

An AI assistant for doctors. The doctor types a patient's complaint and exam findings. The AI either asks the doctor for the missing information or drafts the likely conditions and 2-3 medicines. The doctor reviews and edits the draft, then approves it. The app then issues a digitally signed PDF prescription and saves the visit in the patient's history.

> **The AI drafts, the doctor decides.** This is a decision-support prototype, not a medical device. It has not been clinically validated and must not be used on real patients without clinical, legal and regulatory review.

## Tech stack

| Part | Technology |
|---|---|
| Frontend | React + TypeScript (Vite) |
| Backend | Python 3.12, FastAPI, SQLAlchemy |
| Database | PostgreSQL 16 |
| PDF | ReportLab (with QR code) |
| AI | Azure OpenAI (primary), Groq / Llama 3.3 70B (fallback), through the OpenAI SDK |
| Runtime | Docker Compose |

## Quick start

```bash
cp .env.example .env        # then fill in your keys (see below)
docker compose up -d --build
```

- App: http://localhost:5173
- API docs: http://localhost:8000/docs

`.env` needs: the database login, the Azure OpenAI endpoint/key/deployment, the Groq key, `SIGNING_SECRET` and `JWT_SECRET` (generate each with `openssl rand -hex 32`), the doctor allowlist and the Google OAuth client. `.env` is git-ignored. **Never commit it.**

### Doctor login (Google)

1. In [Google Cloud Console](https://console.cloud.google.com/apis/credentials) create an **OAuth client ID** of type *Web application*.
2. Add this **Authorized redirect URI**: `http://localhost:8000/api/auth/google/callback`
3. Put the client ID and secret in `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).
4. Put the doctors' Gmail addresses in `ALLOWED_DOCTOR_EMAILS` (comma-separated). **Only these accounts can sign in.** Everyone else is refused, even with a valid Google account.
5. On first login a doctor fills in a profile (name, registration number, clinic). It is printed on every prescription they sign.

For local testing without Google, set `DEV_LOGIN=1`. The login page then shows an email box that still honours the allowlist. **Set it to `0` anywhere real.**

The database is created and filled with demo data on first start: 4 patients, 17 medicines and 1 demo doctor.

## How it works

```
Doctor                 Frontend              Backend (FastAPI)                 LLM
  | pick patient          |                        |                            |
  |---------------------->| GET /api/patients      |                            |
  | type complaint        |                        |                            |
  |---------------------->| POST /api/analyze ---->| 1. load patient + last visits
  |                       |                        | 2. remove name/ID (de-identify)
  |                       |                        | 3. send patient facts,    |
  |                       |                        |    complaint, formulary ->|
  |                       |                        |<-- JSON: questions OR     |
  |                       |                        |    conditions + medicines |
  |                       |                        | 4. safety-check each drug |
  |<-- questions / draft -|<-----------------------|                            |
  | answer, edit, approve |                        |                            |
  |---------------------->| POST /api/prescriptions| 5. re-check safety, sign,
  |                       |                        |    save, audit log
  |<-- signed PDF + QR ---|<-----------------------|
```

### 1. Analyze (`/api/analyze`)
- Loads the patient (age, sex, weight, pregnancy, allergies, conditions, current medicines) and their last 5 visits.
- **Strips name and ID before calling the AI.** The model never sees who the patient is.
- Sends the complaint and the clinic **formulary** (the list of allowed medicines) to the LLM and asks for JSON only.
- The AI returns one of two things:
  - `needs_info`: 1-3 short questions for the doctor. The doctor answers, and the AI runs again with the answers. After 6 answered questions it must give a final answer.
  - `ready`: ranked conditions (`most_likely` / `possible` / `less_likely` with reasoning), 2-3 medicines (dose, frequency, duration, instructions) and `red_flags` for urgent findings.

### 2. Safety checks (plain code, not AI)
After the AI answers, `backend/app/safety.py` checks every suggested medicine:

| Check | Result |
|---|---|
| Medicine not in the formulary | Dropped (AI can't invent drugs) |
| Patient allergy (name or drug class) | **Blocked** |
| Pregnant and medicine unsafe in pregnancy | **Blocked** |
| Patient below the medicine's minimum age | **Blocked** |
| Interaction with a current medicine (e.g. NSAID + warfarin) | **Warning** shown to the doctor |

Blocked medicines are not shown as options, only listed as "Blocked" with the reason. The same checks run again when the doctor signs, so edits can't bypass them.

### 3. Sign and store (`/api/prescriptions`)
- The doctor can edit or remove any medicine before approving.
- The server builds a signature with HMAC-SHA256 over the prescription (ID, patient, doctor, medicines, time) using `SIGNING_SECRET`.
- The prescription is saved in Postgres. The PDF is **built on demand** from that data, so there are no files to lose.
- The PDF has the clinic and doctor header, patient details, allergies, complaint, medicines, signature block and a **QR code**. Scanning it opens `/verify/{id}`, which recomputes the signature and shows whether the prescription is genuine and unaltered. The signature covers the prescription ID, patient, doctor, medicines and issue time. It does not cover the complaint text. For the QR to work from a phone, set `PUBLIC_BASE_URL` to the server's real address (not `localhost`).

### 4. Doctors and patients
- Doctors sign in with Google. The server checks the email against the allowlist and sets an HttpOnly session cookie (JWT, 12 hours). Every API route except `/api/verify` requires it.
- Any signed-in doctor can **add or edit patients** (name, age, sex, weight, pregnancy, allergies, conditions, current medicines). Input is validated and cleaned (duplicates dropped, a male patient can't be pregnant), and every change is saved in the audit log with a before/after diff, because allergies and current medicines drive the safety checks.
- PDFs open through a short-lived link (10 minutes) so they work in a new tab or an external PDF viewer.

### 5. Patient history
Every signed prescription appears in the patient's **Visit history** card (date, complaint, diagnoses, medicines, PDF link). The last 5 visits are also given to the AI, so it can notice repeated problems or treatments that did not work.

## Design rules

1. **The LLM never does safety checks.** Code does.
2. **Only formulary medicines** can be suggested.
3. **No patient identifiers go to the LLM.**
4. **No fake percentage scores.** LLM confidence numbers are not calibrated, so the AI gives ranked likelihood plus reasoning instead.
5. **Nothing is signed without the doctor's approval.** AI calls, history views and signatures are written to an `audit_log` table.
6. **Provider fallback:** if Azure OpenAI fails, the request goes to Groq.

## Project layout

```
backend/app/
  main.py      endpoints
  ai.py        prompt, provider fallback, JSON call
  safety.py    allergy / pregnancy / age / interaction checks
  pdf.py       prescription PDF + QR
  models.py    tables: doctors, patients, medicines, prescriptions, audit_log
  seed.py      demo data
frontend/src/
  App.tsx      the UI (patients, history, analyze, questions, prescription editor)
  api.ts       API client and types
docker-compose.yml   db + api + web
```

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/auth/google/login` | start Google sign-in |
| GET/PUT | `/api/auth/me` | current doctor / update profile |
| POST | `/api/auth/logout` | sign out |
| GET | `/api/patients` | list patients |
| POST | `/api/patients` | add a patient |
| PUT | `/api/patients/{id}` | edit a patient |
| GET | `/api/patients/{id}/history` | a patient's signed visits |
| POST | `/api/analyze` | AI draft or follow-up questions |
| POST | `/api/prescriptions` | approve, sign, save |
| GET | `/api/prescriptions/{id}/pdf` | signed PDF |
| GET | `/verify/{id}` | public page the QR code opens: green "Authentic" with doctor, registration, date and medicines (no patient identity), or red "Not valid" |
| GET | `/api/verify/{id}` | same check as JSON |

## Testing

177 automated backend tests run in about a minute. They check the safety rules, how the app handles the AI's answers, login and permissions, patient editing, prescription signing and verification, and the PDF.

### Where the tests are

```
backend/
  tests/                  <- all test files live here
    conftest.py           shared setup: test database, fake AI, logged-in doctor, helpers
    test_safety.py        allergy / pregnancy / age / interaction rules
    test_analyze.py       AI answers, formulary filter, privacy, Azure -> Groq fallback
    test_auth.py          login gate, allowlist, sessions, Google flow, doctor profile
    test_patients.py      add / edit patients, audit trail, visit history
    test_signing.py       signing, verify page, tamper detection, PDF links
    test_pdf.py           PDF content, wrapping, page breaks
    test_startup.py       database upgrade on startup, seed data
  pytest.ini              pytest settings
  requirements-dev.txt    test-only packages (pytest, pypdf)
.github/workflows/
  tests.yml               runs the tests automatically on GitHub for every push
```

### How to run them

You need Docker running (the same Docker you use for the app). From the project folder:

```bash
docker compose --profile test run --rm tests
```

Wait for the last line, for example `177 passed in 85.58s`. Do not press Ctrl+C before it appears, or the run is cut short and only shows the tests done so far.

Run just one file, or just one test (add `-v` to see every test name):

```bash
docker compose --profile test run --rm tests sh -c "pip install -q --root-user-action=ignore -r requirements-dev.txt && python -m pytest tests/test_safety.py -v"
docker compose --profile test run --rm tests sh -c "pip install -q --root-user-action=ignore -r requirements-dev.txt && python -m pytest -k penicillin -v"
```

`-k penicillin` runs every test whose name contains "penicillin".

The `tests` service is not started by a normal `docker compose up`. It only runs when you ask for it with `--profile test`. It does not need the app to be running, only the `db` container, which starts by itself.

### How to read the result

| Output | Meaning |
|---|---|
| `.` | one test passed |
| `F` | one test failed. The details are printed at the end: which test, which line, expected vs actual value |
| `E` | the test could not run (setup error) |
| `177 passed` | everything is fine |
| `2 failed, 175 passed` | something broke. Read the `FAILED tests/...` lines at the bottom |

Test names read like sentences, for example `test_penicillin_allergy_blocks_amoxicillin`, so a failure tells you which rule broke.

### How to add a test

1. Open the file for that area, for example `backend/tests/test_signing.py`.
2. Add a function whose name starts with `test_`. Use the ready-made fixtures from `conftest.py`:
   - `client`: a signed-in doctor with a complete profile
   - `anon`: a visitor who is not signed in
   - `pid`: patient ids by name, e.g. `pid["Aisha Rahman"]`
   - `fake_ai`: the pretend AI. Script its answer with `fake_ai.ready(med("Amoxicillin"))`
   - `db`: a direct database session, for checking what was stored

```python
def test_penicillin_allergy_blocks_amoxicillin(client, pid, fake_ai):
    fake_ai.ready(med("Amoxicillin"), med("Paracetamol"))
    result = analyze(client, pid["Aisha Rahman"]).json()   # Aisha is allergic to penicillin
    assert [m["name"] for m in result["medicines"]] == ["Paracetamol"]
```

3. Run `docker compose --profile test run --rm tests` and check it passes.
4. A good habit: make the test fail first (break the code on purpose), then restore it. That proves the test really protects something.

### Running without Docker (optional)

If you have Python 3.12 and a Postgres server:

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/clinic_test   # Windows PowerShell: $env:TEST_DATABASE_URL="..."
python -m pytest
```

The database name must end in `_test`. It is created automatically if it does not exist.

### Why the tests are safe to run

- **Own database.** Tests use `clinic_test` (the name must end in `_test`, or the tests refuse to start), never your real data. Every test starts from the same seeded data.
- **No real AI.** Azure and Groq keys are removed while testing and the model is replaced by a scripted fake. Tests are free, fast and give the same result every time.
- **Google is faked too**, so the whole Google sign-in flow (allowlist, bad state, unverified email, Google being down) is tested without Google.

### What each file protects

| File | What it protects |
|---|---|
| `test_safety.py` | allergy, pregnancy, minimum age and interaction rules for every medicine; formulary data sanity |
| `test_analyze.py` | AI answers are filtered by the safety rules; medicines outside the formulary are dropped; questions flow; **no patient name or ID is sent to the model**; Azure-to-Groq fallback |
| `test_auth.py` | **every API route requires login** except a fixed public list (the test fails if a new open route appears); allowlist; forged, expired and garbage sessions; Google flow; doctor profile |
| `test_patients.py` | validation and cleaning; every add/edit is audited with a before/after diff; visit history |
| `test_signing.py` | the server re-checks safety on signing; any change to medicines, dose, date or patient breaks verification; the verify page escapes HTML and never shows patient details; PDF links are scoped and expire |
| `test_pdf.py` | PDF content, long text wrapping, page breaks keep the signature on the last page |
| `test_startup.py` | database upgrade on startup and seed data are repeatable |

GitHub Actions runs the same tests (plus a TypeScript type-check of the frontend) on every push and pull request. See `.github/workflows/tests.yml`.

**When you change something**
- Changed a safety rule or the formulary: update `test_safety.py` first, then the code.
- Added an API route: it must require login. If it is meant to be public, add it to `PUBLIC_API_ROUTES` in `test_auth.py` and say why in the commit.
- Fixed a bug: add a test that fails without the fix. (The first run of this suite found two real bugs this way: a malformed session cookie caused a server error, and a very long unbroken word ran off the PDF page.)

What the tests do **not** cover: how good the real model's medical suggestions are (they vary and cost money, so check them by hand or with a small separate evaluation), and how the screens look.

## Known limitations (MVP)

- All allowlisted doctors share one patient list. There are no roles (admin) and no patient archive or delete.
- Google login was verified end-to-end only through the dev login. Test it against your own Google OAuth client before relying on it.
- The signature is an HMAC plus a typed name, not a certificate-based digital signature (PAdES).
- The formulary and interaction list are a small demo set. A real clinic needs its own formulary or a drug-interaction database.
- A visit is saved only when a prescription is signed.
- No frontend component or end-to-end tests yet (only a type-check).

## Roadmap

- Per-doctor signature images, roles (admin manages the allowlist), per-clinic patient scoping
- Real certificate-based PDF signing
- A `visits` table to store every consultation, with notes and vitals fields
- Real drug database and interaction checks
- Hard-stop rule for dangerous vitals (emergency banner, no medicines until confirmed)
- Voice dictation, guideline RAG, patient app, pharmacy e-prescription, EHR integration
- Frontend component tests, one end-to-end test (Playwright), a small real-model evaluation set, secret scanning in CI, and database migrations (Alembic)
