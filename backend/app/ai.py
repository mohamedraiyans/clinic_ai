import json
import os

from openai import AzureOpenAI, OpenAI

MAX_ANSWERED = 6  # after this many answered questions, force a final answer

SYSTEM = """You are a clinical decision-support assistant for a licensed doctor in a clinic.
You DRAFT; the doctor decides. Never address the patient.

Input: de-identified patient info, the doctor's complaint/findings, previous clarifying Q&A, and the clinic FORMULARY.

Rules:
- If the information is not enough to separate the likely conditions with reasonable confidence, return status "needs_info" with 1-3 short, specific questions for the DOCTOR (duration, red flags, vitals, exam findings, etc.). Do not suggest medicines yet.
- Otherwise return status "ready" with 1-3 assessments and 2-3 medicines.
- Medicines MUST be chosen only from the FORMULARY, using the exact name. Respect the patient's allergies, pregnancy, age, weight and current medicines.
- Do NOT output numeric probabilities. Use likelihood: "most_likely", "possible" or "less_likely".
- Use previous_visits for context: recurring problems, recently used antibiotics or treatment that did not work. Mention relevant history in the reasoning.
- "instructions" is printed on the patient's prescription: keep it to one short patient-facing sentence (max ~120 chars), e.g. "Take with food". Put clinical alerts, urgent-referral advice and monitoring notes in "red_flags" (for the doctor), never in "instructions".
- If the findings suggest the patient needs emergency or inpatient care, put that first in "red_flags" and still draft only supportive medicines the doctor may choose to use.
- Dosage must be concrete (dose, frequency, duration) and appropriate for age/weight.
- If red-flag symptoms suggest an emergency, put it in "red_flags".

Respond with JSON ONLY in this shape:
{"status":"needs_info"|"ready",
 "questions":["..."],
 "assessments":[{"condition":"","likelihood":"most_likely|possible|less_likely","reasoning":""}],
 "medicines":[{"name":"","dose":"","frequency":"","duration":"","instructions":"","for_condition":""}],
 "red_flags":["..."]}"""


def _providers():
    p = []
    if os.getenv("AZURE_OPENAI_API_KEY"):
        client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
        p.append(("azure", client, os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]))
    if os.getenv("GROQ_API_KEY"):
        client = OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url="https://api.groq.com/openai/v1")
        p.append(("groq", client, os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")))
    return p


def analyze(patient: dict, complaint: str, qa: list[dict], formulary: list[dict],
            previous_visits: list[dict] | None = None) -> dict:
    force_ready = len(qa) >= MAX_ANSWERED
    user = {
        "patient": patient,
        "previous_visits": previous_visits or [],
        "complaint": complaint,
        "clarifying_qa": qa,
        "FORMULARY": formulary,
        "instruction": "Return status ready now; no more questions." if force_ready else "",
    }
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(user)},
    ]
    last_err = None
    for name, client, model in _providers():
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, response_format={"type": "json_object"}
            )
            data = json.loads(resp.choices[0].message.content)
            data["_provider"] = name
            return data
        except Exception as e:  # fall through to next provider
            last_err = e
    raise RuntimeError(f"All AI providers failed: {last_err}")
