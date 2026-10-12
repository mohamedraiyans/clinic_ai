"""Deterministic safety checks. The LLM is never trusted to do these."""

from .models import Medicine, Patient


def check(med: Medicine, patient: Patient) -> list[dict]:
    """Return a list of {severity: 'block'|'warn', message}."""
    out: list[dict] = []
    allergies = [a.lower() for a in patient.allergies]
    if med.name.lower() in allergies or (med.allergy_class and med.allergy_class.lower() in allergies):
        out.append({"severity": "block", "message": f"Patient is allergic to {med.allergy_class or med.name}"})
    if patient.pregnant and not med.pregnancy_safe:
        out.append({"severity": "block", "message": "Not recommended in pregnancy"})
    if patient.age < med.min_age:
        out.append({"severity": "block", "message": f"Minimum age is {med.min_age}"})
    current = [m.lower() for m in patient.current_meds]
    for other in med.interacts_with:
        if other.lower() in current:
            out.append({"severity": "warn", "message": f"Interaction with current medicine: {other}"})
    return out
