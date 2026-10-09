"""The patient-safety core: allergy, pregnancy, age and interaction rules (app/safety.py).

These are plain functions, so no database or HTTP is involved. They use the real seeded
formulary, so a change to a medicine's definition is tested too.
"""

import pytest

from app.models import Medicine, Patient
from app.safety import check
from app.seed import MEDICINES


def med(name: str) -> Medicine:
    n, cls, dose, allergy_class, interacts, preg_safe, min_age = next(m for m in MEDICINES if m[0] == name)
    return Medicine(name=n, drug_class=cls, typical_dose=dose, allergy_class=allergy_class,
                    interacts_with=interacts, pregnancy_safe=preg_safe, min_age=min_age)


def patient(**overrides) -> Patient:
    data = {"name": "T", "age": 30, "sex": "female", "weight_kg": 60, "pregnant": False,
            "allergies": [], "conditions": [], "current_meds": []}
    return Patient(**{**data, **overrides})


def severities(warnings):
    return sorted(w["severity"] for w in warnings)


# ------------------------------------------------------------------ allergies

def test_allergy_to_drug_class_blocks_member_of_that_class():
    w = check(med("Amoxicillin"), patient(allergies=["penicillin"]))
    assert severities(w) == ["block"]
    assert "penicillin" in w[0]["message"].lower()


def test_allergy_to_the_drug_name_blocks_it():
    assert severities(check(med("Amoxicillin"), patient(allergies=["Amoxicillin"]))) == ["block"]


def test_allergy_match_ignores_case():
    assert severities(check(med("Amoxicillin"), patient(allergies=["PENICILLIN"]))) == ["block"]


def test_nsaid_allergy_blocks_every_nsaid():
    for name in ("Ibuprofen", "Diclofenac", "Aspirin"):
        assert "block" in severities(check(med(name), patient(allergies=["nsaid"]))), name


def test_unrelated_allergy_does_not_block():
    assert check(med("Amoxicillin"), patient(allergies=["latex", "peanuts"])) == []


def test_drug_without_allergy_class_is_never_blocked_by_class_allergies():
    assert check(med("Paracetamol"), patient(allergies=["penicillin", "nsaid", "macrolide"])) == []


# ------------------------------------------------------------------ pregnancy

@pytest.mark.parametrize("name", ["Ibuprofen", "Diclofenac", "Aspirin", "Ciprofloxacin", "Loperamide"])
def test_pregnancy_blocks_unsafe_medicines(name):
    w = check(med(name), patient(pregnant=True))
    assert "block" in severities(w)
    assert any("pregnan" in x["message"].lower() for x in w)


@pytest.mark.parametrize("name", ["Paracetamol", "Amoxicillin", "Cetirizine", "Ondansetron"])
def test_pregnancy_allows_safe_medicines(name):
    assert check(med(name), patient(pregnant=True)) == []


def test_unsafe_in_pregnancy_medicine_is_fine_when_not_pregnant():
    assert check(med("Ibuprofen"), patient(pregnant=False)) == []


# ------------------------------------------------------------------ age

def test_below_minimum_age_is_blocked():
    assert severities(check(med("Ibuprofen"), patient(age=5))) == ["block"]  # minimum is 6


def test_exactly_minimum_age_is_allowed():
    assert check(med("Ibuprofen"), patient(age=6)) == []


def test_adult_only_medicine_blocked_for_a_child():
    assert "block" in severities(check(med("Ciprofloxacin"), patient(age=7)))  # minimum is 18


def test_paracetamol_has_no_age_limit():
    assert check(med("Paracetamol"), patient(age=0)) == []


# ------------------------------------------------------------------ interactions

def test_nsaid_with_warfarin_is_a_warning_not_a_block():
    w = check(med("Ibuprofen"), patient(current_meds=["warfarin"]))
    assert severities(w) == ["warn"]
    assert "warfarin" in w[0]["message"].lower()


def test_interaction_match_ignores_case():
    assert severities(check(med("Ibuprofen"), patient(current_meds=["Warfarin"]))) == ["warn"]


def test_antibiotic_with_warfarin_warns():
    assert severities(check(med("Amoxicillin"), patient(current_meds=["warfarin"]))) == ["warn"]


def test_no_interaction_without_the_other_medicine():
    assert check(med("Ibuprofen"), patient(current_meds=["metformin"])) == []


def test_two_nsaids_together_warn():
    assert "warn" in severities(check(med("Ibuprofen"), patient(current_meds=["aspirin"])))


# ------------------------------------------------------------------ combinations

def test_several_problems_are_all_reported():
    w = check(med("Ibuprofen"), patient(pregnant=True, allergies=["nsaid"], current_meds=["warfarin"]))
    assert severities(w) == ["block", "block", "warn"]


def test_an_otherwise_fine_patient_gets_no_warnings():
    assert check(med("Paracetamol"), patient()) == []


# ------------------------------------------------------------------ the formulary itself

def test_formulary_names_are_unique():
    names = [m[0] for m in MEDICINES]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("row", MEDICINES, ids=[m[0] for m in MEDICINES])
def test_every_medicine_has_sane_data(row):
    name, drug_class, dose, allergy_class, interacts, preg_safe, min_age = row
    assert name and drug_class and dose
    assert isinstance(interacts, list) and isinstance(preg_safe, bool)
    assert 0 <= min_age <= 18
