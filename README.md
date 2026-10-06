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

## Known limitations (MVP)

- All allowlisted doctors share one patient list. There are no roles (admin) and no patient archive or delete.
- Google login was verified end-to-end only through the dev login. Test it against your own Google OAuth client before relying on it.
- The signature is an HMAC plus a typed name, not a certificate-based digital signature (PAdES).
- The formulary and interaction list are a small demo set. A real clinic needs its own formulary or a drug-interaction database.
- A visit is saved only when a prescription is signed.
- No automated tests yet.

## Roadmap

- Per-doctor signature images, roles (admin manages the allowlist), per-clinic patient scoping
- Real certificate-based PDF signing
- A `visits` table to store every consultation, with notes and vitals fields
- Real drug database and interaction checks
- Hard-stop rule for dangerous vitals (emergency banner, no medicines until confirmed)
- Voice dictation, guideline RAG, patient app, pharmacy e-prescription, EHR integration
- Tests for the safety rules and the AI endpoint, and database migrations (Alembic)
