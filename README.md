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

`.env` needs: the database login, the Azure OpenAI endpoint/key/deployment, the Groq key, and a `SIGNING_SECRET` (generate one with `openssl rand -hex 32`). `.env` is git-ignored. **Never commit it.**

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
- The PDF has the clinic and doctor header, patient details, allergies, complaint, medicines, signature block and a **QR code**. Scanning it opens `/api/verify/{id}`, which recomputes the signature and says whether the prescription is genuine and unaltered.

### 4. Patient history
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
| GET | `/api/patients` | list patients |
| GET | `/api/patients/{id}/history` | a patient's signed visits |
| POST | `/api/analyze` | AI draft or follow-up questions |
| POST | `/api/prescriptions` | approve, sign, save |
| GET | `/api/prescriptions/{id}/pdf` | signed PDF |
| GET | `/api/verify/{id}` | check a signature (public, used by the QR code) |

## Known limitations (MVP)

- No login. Everything is signed as one demo doctor.
- The signature is an HMAC plus a typed name, not a certificate-based digital signature (PAdES).
- The formulary and interaction list are a small demo set. A real clinic needs its own formulary or a drug-interaction database.
- A visit is saved only when a prescription is signed.
- No automated tests yet.

## Roadmap

- Doctor accounts (JWT login) and per-doctor signature images
- Real certificate-based PDF signing
- A `visits` table to store every consultation, with notes and vitals fields
- Real drug database and interaction checks
- Hard-stop rule for dangerous vitals (emergency banner, no medicines until confirmed)
- Voice dictation, guideline RAG, patient app, pharmacy e-prescription, EHR integration
- Tests for the safety rules and the AI endpoint, and database migrations (Alembic)
