"""The prescription PDF (app/pdf.py). Includes regression tests for layout bugs we already hit."""

import io
from types import SimpleNamespace

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth

from app import pdf

MAX_W = A4[0] - 75 - 50  # same text width the PDF uses for medicine details


def build(items, complaint="sore throat for 3 days", allergies=("penicillin",)):
    rx = SimpleNamespace(id="11111111-2222-3333-4444-555555555555", issued_at="2026-10-06 10:00 UTC",
                         complaint=complaint, items=items, signature="ab" * 32)
    doctor = SimpleNamespace(name="Dr. Jane Silva", reg_no="REG-777", clinic="City Clinic")
    patient = SimpleNamespace(name="Aisha Rahman", age=34, sex="female", weight_kg=62.0,
                              allergies=list(allergies))
    return pdf.build(rx, doctor, patient)


def item(n=1, **kw):
    return {"name": f"Drug {n}", "dose": "500 mg", "frequency": "every 8 hours", "duration": "5 days",
            "instructions": "Take with food.", **kw}


def pages(data):
    return [p.extract_text() for p in PdfReader(io.BytesIO(data)).pages]


def test_output_is_a_pdf():
    assert build([item()])[:5] == b"%PDF-"


def test_page_contains_all_the_important_details():
    text = pages(build([item(1), item(2)]))[0]
    for expected in ("City Clinic", "Dr. Jane Silva", "REG-777", "Aisha Rahman", "penicillin",
                     "sore throat", "Drug 1", "Drug 2", "500 mg", "every 8 hours", "5 days",
                     "Digitally signed by Dr. Jane Silva", "Scan to verify"):
        assert expected in text, expected


def test_verify_address_is_printed_for_manual_entry():
    assert "http://testserver/verify/11111111-2222-3333-4444-555555555555" in pages(build([item()]))[0].replace("\n", "")


def test_short_prescription_is_one_page():
    assert len(pages(build([item(i) for i in range(3)]))) == 1


def test_long_prescription_continues_on_a_second_page_and_keeps_the_signature_on_the_last():
    result = pages(build([item(i) for i in range(1, 16)]))
    assert len(result) >= 2
    assert "Drug 15" in result[-1] or "Drug 15" in "".join(result)
    assert "Digitally signed by" in result[-1] and "Scan to verify" in result[-1]
    assert "Digitally signed by" not in result[0]


def test_no_medicine_is_lost_when_the_page_breaks():
    text = "".join(pages(build([item(i) for i in range(1, 21)])))
    for i in range(1, 21):
        assert f"Drug {i}" in text


def test_empty_allergy_list_is_fine():
    assert "Allergies" not in pages(build([item()], allergies=()))[0]


def test_missing_instructions_are_fine():
    assert build([item(instructions="")])[:4] == b"%PDF"


# ------------------------------------------------------------------ text wrapping (the "PDF runs off the page" bug)

def test_wrapped_lines_never_exceed_the_width():
    text = ("500 mg every 8 hours for 5 days only if the receiving clinician confirms outpatient treatment is "
            "appropriate because of temperature 40.6C tachycardia diabetes and focal chest findings ") * 3
    lines = pdf._wrap(text, MAX_W)
    assert len(lines) > 3
    assert all(stringWidth(line, "Helvetica", 10) <= MAX_W for line in lines)


def test_wrapping_loses_no_words():
    text = "one two three four five six seven eight nine ten " * 20
    assert " ".join(pdf._wrap(text, 120)).split() == text.split()


def test_a_single_very_long_word_is_split_instead_of_running_off_the_page():
    lines = pdf._wrap("x" * 400, MAX_W)
    assert len(lines) > 1
    assert all(stringWidth(line, "Helvetica", 10) <= MAX_W for line in lines)
    assert "".join(lines) == "x" * 400


def test_long_unbroken_text_in_a_medicine_field_still_builds():
    assert build([item(dose="y" * 500, instructions="z" * 500)])[:4] == b"%PDF"


def test_empty_text_wraps_to_nothing():
    assert pdf._wrap("", MAX_W) == []
