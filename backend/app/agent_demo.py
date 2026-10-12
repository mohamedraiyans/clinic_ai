"""Command-line demo of the tool-calling agent.

    docker compose exec api python -m app.agent_demo --list
    docker compose exec api python -m app.agent_demo --patient 1 "Is Amoxicillin safe for this patient?"
    docker compose exec -it api python -m app.agent_demo --patient 1      # ask several questions

Every tool call is printed, so you can see the loop: model asks, code runs the tool, model answers.
The patient's name is shown here on your screen only. It is never sent to the model.
"""

import argparse
import json
import sys

from . import agent
from .db import SessionLocal
from .models import Patient


def _short(value, limit=150) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _run_question(db, patient, question: str, prefer: str | None) -> None:
    result = agent.ask(db, patient, question, prefer=prefer)
    for i, step in enumerate(result.steps, 1):
        print(f"  step {i}: model called {step['tool']}({_short(step['args'])})")
        print(f"          -> {_short(step['result'])}")
    if not result.steps:
        print("  (the model answered without calling a tool)")
    print(f"\nAnswer ({result.provider}):\n{result.answer}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Tool-calling agent demo (read-only).")
    parser.add_argument("question", nargs="?", help="Question for the doctor's assistant. Omit to type several.")
    parser.add_argument("--patient", type=int, help="Patient id (see --list)")
    parser.add_argument("--list", action="store_true", help="List patient ids and exit")
    parser.add_argument("--provider", choices=["groq", "azure"], default="groq",
                        help="Which model provider to try first (default: groq)")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        if args.list:
            for p in db.query(Patient).order_by(Patient.id):
                print(f"{p.id}: {p.name} ({p.age}y, {p.sex})")
            return 0
        if args.patient is None:
            print("Choose a patient with --patient ID (see --list).", file=sys.stderr)
            return 2
        patient = db.get(Patient, args.patient)
        if patient is None:
            print(f"No patient with id {args.patient}.", file=sys.stderr)
            return 2

        print(f"Patient {patient.id} selected. The model gets no name and no id.\n")
        try:
            if args.question:
                _run_question(db, patient, args.question, args.provider)
                return 0
            print("Type a question, or press Enter on an empty line to quit.")
            while True:
                try:
                    question = input("\nDoctor> ").strip()
                except EOFError:
                    break
                if not question:
                    break
                _run_question(db, patient, question, args.provider)
        except RuntimeError as e:  # no provider configured, or every provider failed
            print(f"Error: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
