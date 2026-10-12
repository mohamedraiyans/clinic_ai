"""A small tool-calling agent for doctors (demo).

The doctor has already selected a patient. The doctor asks a question in plain words, and the model
may call READ-ONLY tools to look facts up before it answers. The loop is written by hand with the
OpenAI-compatible API (no agent SDK) so every step is visible:

    model -> asks for a tool -> our code runs it -> result goes back to the model -> repeat -> answer

Rules this module must keep (see CLAUDE.md):
  * Tools only read. Nothing here edits a patient, writes a prescription or signs anything.
  * The patient is fixed by the caller. The model never chooses a patient, and never sees a name or ID.
  * Safety verdicts come from app/safety.py, not from the model's opinion.
"""

import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from . import ai, safety
from .models import AuditLog, Medicine, Patient, Prescription

MAX_STEPS = 6            # rounds of "model asks for tools"; stops runaway loops
MAX_CALLS_PER_STEP = 4   # tools the model may request in one round
MAX_HISTORY = 6          # earlier chat messages kept for follow-up questions
MAX_HISTORY_CHARS = 2000

SYSTEM = """You are a read-only assistant for a doctor in a clinic. The doctor has already selected one \
patient. You cannot see or change which patient that is.

Rules:
1. Use the tools to get facts. Never guess allergies, current medicines, past visits or safety results.
2. For any question about whether a medicine is safe for this patient, call check_medicine_safety and \
base your answer on its result. If blocked is true, say it is blocked and give the reason. Warnings are \
for the doctor's attention.
3. You do not diagnose and you do not prescribe. The doctor decides.
4. If a medicine is not in the clinic formulary, say so.
5. Never call a medicine "safe". The clinic's rules are limited (allergy, pregnancy, age and a few \
interactions). Say "not blocked by the clinic's rules", and remind the doctor to use their own judgement.
6. Answer briefly in plain words, and say which tools you used.
7. Never ask for, or mention, the patient's name."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_patient_summary",
        "description": "Age, sex, weight, pregnancy status, allergies, conditions and current medicines "
                       "of the selected patient.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_recent_visits",
        "description": "The selected patient's most recent signed prescriptions: date, conditions and medicines.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "How many visits, 1 to 10. Default 5."}}}}},
    {"type": "function", "function": {
        "name": "check_medicine_safety",
        "description": "Check one medicine from the clinic formulary against this patient's allergies, "
                       "pregnancy, age and current medicines. Returns blocked (true/false) and warnings.",
        "parameters": {"type": "object", "properties": {
            "medicine_name": {"type": "string", "description": "Name of the medicine, e.g. Amoxicillin"}},
            "required": ["medicine_name"]}}},
    {"type": "function", "function": {
        "name": "list_safe_medicines",
        "description": "Every formulary medicine that the clinic's rules do not block for this patient, with "
                       "any warnings. 'Not blocked' is not the same as 'safe'.",
        "parameters": {"type": "object", "properties": {}}}},
]


class ClinicTools:
    """The tools. Each one is read-only and scoped to one patient chosen by the caller."""

    def __init__(self, db: Session, patient: Patient):
        self.db, self.patient = db, patient
        self._table = {
            "get_patient_summary": self.get_patient_summary,
            "list_recent_visits": self.list_recent_visits,
            "check_medicine_safety": self.check_medicine_safety,
            "list_safe_medicines": self.list_safe_medicines,
        }

    def names(self) -> set[str]:
        return set(self._table)

    def run(self, name: str, args) -> dict:
        """Run one tool. Always returns a dict; problems become {"error": ...} so the model can recover."""
        if name not in self._table:
            return {"error": f"Unknown tool '{name}'. Available: {sorted(self._table)}"}
        if not isinstance(args, dict):
            return {"error": "Tool arguments must be a JSON object"}
        try:
            return self._table[name](**args)
        except TypeError:
            return {"error": f"Invalid arguments for {name}"}
        except ValueError as e:
            return {"error": str(e)}

    def get_patient_summary(self) -> dict:
        p = self.patient  # deliberately no name and no id
        return {"age": p.age, "sex": p.sex, "weight_kg": p.weight_kg, "pregnant": p.pregnant,
                "allergies": p.allergies, "conditions": p.conditions, "current_medicines": p.current_meds}

    def list_recent_visits(self, limit: int = 5) -> dict:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError("limit must be a whole number")
        limit = max(1, min(limit, 10))
        rows = (self.db.query(Prescription).filter(Prescription.patient_id == self.patient.id)
                .order_by(Prescription.created_at.desc()).limit(limit).all())
        return {"visits": [{"date": r.issued_at, "complaint": r.complaint[:200],
                            "conditions": [a.get("condition") for a in r.assessments[:2]],
                            "medicines": [i["name"] for i in r.items]} for r in rows]}

    def _formulary(self) -> dict[str, Medicine]:
        return {m.name.lower(): m for m in self.db.query(Medicine)}

    def check_medicine_safety(self, medicine_name: str) -> dict:
        if not isinstance(medicine_name, str) or not medicine_name.strip():
            raise ValueError("medicine_name is required")
        if len(medicine_name) > 100:
            raise ValueError("medicine_name is too long")
        med = self._formulary().get(medicine_name.strip().lower())
        if med is None:
            return {"found": False, "message": "Not in the clinic formulary"}
        warnings = safety.check(med, self.patient)
        return {"found": True, "medicine": med.name,
                "blocked": any(w["severity"] == "block" for w in warnings), "warnings": warnings}

    def list_safe_medicines(self) -> dict:
        safe = []
        for med in sorted(self._formulary().values(), key=lambda m: m.name):
            warnings = safety.check(med, self.patient)
            if not any(w["severity"] == "block" for w in warnings):
                safe.append({"name": med.name, "warnings": [w["message"] for w in warnings]})
        return {"medicines": safe}


@dataclass
class AgentResult:
    answer: str
    steps: list[dict] = field(default_factory=list)  # [{"tool", "args", "result"}] in order
    provider: str = ""
    stopped: bool = False  # True if the loop was cut short (step limit or no answer)


def _providers(prefer: str | None):
    providers = ai._providers()
    if not providers:
        raise RuntimeError("No AI provider is configured (set AZURE_OPENAI_* or GROQ_API_KEY)")
    return sorted(providers, key=lambda p: p[0] != prefer) if prefer else providers


def _chat(providers, messages):
    """One model call. Tries each provider in turn, like ai.analyze does."""
    last_error = None
    for name, client, model in providers:
        try:
            response = client.chat.completions.create(model=model, messages=messages, tools=TOOLS,
                                                      tool_choice="auto")
            return name, response.choices[0].message
        except Exception as e:  # fall through to the next provider
            last_error = e
    raise RuntimeError(f"All AI providers failed: {last_error}")


def clean_history(history) -> list[dict]:
    """Earlier chat turns from the browser. Only plain user/assistant text is kept, so a client cannot
    smuggle in system or tool messages. Tool results are never replayed: the model must look facts up again."""
    out = []
    for m in (history if isinstance(history, list) else [])[-MAX_HISTORY:]:
        role = m.get("role") if isinstance(m, dict) else None
        text = m.get("content") if isinstance(m, dict) else None
        if role in ("user", "assistant") and isinstance(text, str) and text.strip():
            out.append({"role": role, "content": text.strip()[:MAX_HISTORY_CHARS]})
    return out


def ask(db: Session, patient: Patient, question: str, prefer: str | None = None,
        max_steps: int = MAX_STEPS, history: list[dict] | None = None,
        doctor_id: int | None = None) -> AgentResult:
    tools = ClinicTools(db, patient)
    providers = _providers(prefer)
    messages = [{"role": "system", "content": SYSTEM}, *clean_history(history),
                {"role": "user", "content": question}]
    result = AgentResult(answer="")

    for _ in range(max_steps):
        result.provider, msg = _chat(providers, messages)
        calls = list(getattr(msg, "tool_calls", None) or [])
        if not calls:  # the model answered
            result.answer = (msg.content or "").strip()
            if not result.answer:
                result.answer = "The assistant did not produce an answer."
                result.stopped = True
            break

        # keep the model's request in the history, in plain dict form
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in calls]})

        for index, call in enumerate(calls):
            name, raw = call.function.name, call.function.arguments
            if index >= MAX_CALLS_PER_STEP:
                args, output = {}, {"error": f"Too many tool calls in one step (limit {MAX_CALLS_PER_STEP})"}
            else:
                try:
                    args = json.loads(raw) if raw else {}
                    output = tools.run(name, args)
                except json.JSONDecodeError:
                    args, output = {}, {"error": "Tool arguments were not valid JSON"}
            result.steps.append({"tool": name, "args": args, "result": output})
            # every tool call must be answered, or the next model call is rejected
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(output)})
    else:
        result.answer = f"I could not finish within {max_steps} steps. Please ask a narrower question."
        result.stopped = True

    db.add(AuditLog(event="agent_question", patient_id=patient.id,
                    detail={"doctor": doctor_id, "question": question[:300],
                            "tools": [s["tool"] for s in result.steps],
                            "provider": result.provider, "stopped": result.stopped}))
    db.commit()
    return result
