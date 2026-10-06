# Clinic AI

AI assistant for doctors. The doctor enters a patient's complaint and exam findings. The AI either asks the doctor clarifying questions or drafts likely conditions plus 2-3 medicines. The doctor edits and approves, and the app issues a digitally signed PDF prescription. **The AI drafts, the doctor decides.**

## Stack
- `backend/`: Python 3.12, FastAPI, SQLAlchemy, Postgres, ReportLab (PDF)
- `frontend/`: React + TypeScript (Vite), proxies `/api` to the api container
- Docker Compose runs `db` (Postgres 16), `api` (:8000), `web` (:5173)
- LLMs go through the OpenAI SDK: Azure OpenAI first (`AZURE_OPENAI_*`), Groq as fallback (`GROQ_*`)

## Commands
```bash
docker compose up -d --build     # start everything (http://localhost:5173, API :8000)
docker compose logs -f api       # backend logs
docker compose down              # stop (add -v to wipe the database)
cd frontend && npx tsc --noEmit  # type-check the frontend
```
Backend and `frontend/src` are bind-mounted, so edits reload automatically. Rebuild after changing `requirements.txt` or `package.json`. The DB is created and seeded on API startup (`backend/app/seed.py`).

## Layout
- `backend/app/main.py`: endpoints (`/api/patients`, `/api/analyze`, `/api/prescriptions`, `/api/prescriptions/{id}/pdf`, `/api/verify/{id}`)
- `backend/app/ai.py`: system prompt, provider fallback, JSON-mode call
- `backend/app/safety.py`: deterministic checks (allergy, pregnancy, min age, interactions)
- `backend/app/pdf.py`: prescription PDF with signature block and QR code
- `backend/app/models.py`, `seed.py`: tables and demo data (4 patients, 17 medicines, 1 demo doctor)
- `frontend/src/App.tsx`, `api.ts`: the whole UI

## Rules that must not be broken
1. **The LLM never does safety checks.** Allergies, pregnancy, age and interactions are checked in `safety.py`, in `/api/analyze` and again in `/api/prescriptions`. A blocked medicine can't be signed.
2. **The AI may only suggest medicines from the `medicines` table (the formulary).** Anything else is dropped in `/api/analyze`.
3. **Send the LLM no patient identifiers.** `/api/analyze` strips `id` and `name`. Never add name, phone, address or national ID to the prompt.
4. **No numeric "% match" from the LLM.** It isn't calibrated. Use `most_likely` / `possible` / `less_likely` plus reasoning.
5. **Nothing is signed without explicit doctor approval**, and AI calls and signatures go into `audit_log`.
6. **Never commit or print `.env`.** It holds the API keys and `SIGNING_SECRET`. `.env.example` is the template.

## Known MVP shortcuts (good next tasks)
- No login. `/api/prescriptions` signs as the first doctor in the table. Add doctor auth (JWT) and per-doctor signature images.
- Signature is HMAC-SHA256 (`SIGNING_SECRET`) plus a typed name. Real PAdES/certificate signing is future work.
- Formulary and interactions are a small demo set. Replace them with the clinic's real formulary or a drug-interaction API.
- Add tests (safety rules, `/api/analyze` mocking the LLM), Alembic migrations, and an edit/search UI for patients.
- Later ideas: voice dictation (Whisper), guideline RAG, patient app, pharmacy e-prescription, EHR integration.
