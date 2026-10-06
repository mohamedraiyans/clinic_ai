from sqlalchemy.orm import Session

from .models import Doctor, Medicine, Patient

# (name, class, typical dose, allergy_class, interacts_with, pregnancy_safe, min_age)
MEDICINES = [
    ("Paracetamol", "analgesic", "Adult 500-1000 mg every 6h, max 4 g/day; child 15 mg/kg", "", [], True, 0),
    ("Ibuprofen", "nsaid", "Adult 400 mg every 8h with food; child 5-10 mg/kg", "nsaid", ["warfarin", "aspirin", "nsaid"], False, 6),
    ("Diclofenac", "nsaid", "Adult 50 mg every 8h with food", "nsaid", ["warfarin", "aspirin", "nsaid"], False, 14),
    ("Aspirin", "nsaid", "Adult 300-600 mg every 6h", "nsaid", ["warfarin", "ibuprofen", "nsaid"], False, 16),
    ("Amoxicillin", "penicillin", "Adult 500 mg every 8h; child 25 mg/kg/day divided", "penicillin", ["warfarin"], True, 0),
    ("Azithromycin", "macrolide", "Adult 500 mg day 1 then 250 mg days 2-5", "macrolide", ["warfarin"], True, 0),
    ("Ciprofloxacin", "fluoroquinolone", "Adult 500 mg every 12h", "fluoroquinolone", ["warfarin"], False, 18),
    ("Cetirizine", "antihistamine", "Adult 10 mg once daily; child 5 mg", "", [], True, 2),
    ("Loratadine", "antihistamine", "Adult 10 mg once daily", "", [], True, 2),
    ("Omeprazole", "ppi", "Adult 20 mg once daily before breakfast", "", [], True, 12),
    ("Oral Rehydration Salts", "rehydration", "1 sachet in 1 L water, sip after each loose stool", "", [], True, 0),
    ("Loperamide", "antidiarrheal", "Adult 4 mg then 2 mg after each loose stool, max 16 mg/day", "", [], False, 12),
    ("Ondansetron", "antiemetic", "Adult 4 mg every 8h; child 0.15 mg/kg", "", [], True, 2),
    ("Salbutamol Inhaler", "bronchodilator", "1-2 puffs every 4-6h as needed", "", [], True, 4),
    ("Guaifenesin Syrup", "expectorant", "Adult 10 mL every 6h", "", [], True, 6),
    ("Dextromethorphan Syrup", "antitussive", "Adult 10 mL every 6-8h", "", [], False, 12),
    ("Amlodipine", "ccb", "Adult 5-10 mg once daily", "", [], True, 18),
]


def seed(db: Session) -> None:
    if db.query(Doctor).count() == 0:
        db.add(Doctor(name="Dr. Demo Doctor", reg_no="REG-000123", clinic="City Family Clinic"))
    if db.query(Medicine).count() == 0:
        for n, c, d, a, i, p, m in MEDICINES:
            db.add(Medicine(name=n, drug_class=c, typical_dose=d, allergy_class=a,
                            interacts_with=i, pregnancy_safe=p, min_age=m))
    if db.query(Patient).count() == 0:
        db.add_all([
            Patient(name="Aisha Rahman", age=34, sex="female", weight_kg=62, pregnant=False,
                    allergies=["penicillin"], conditions=["hypertension"], current_meds=["Amlodipine"]),
            Patient(name="John Perera", age=58, sex="male", weight_kg=82, pregnant=False,
                    allergies=[], conditions=["type 2 diabetes", "atrial fibrillation"], current_meds=["warfarin", "metformin"]),
            Patient(name="Maya Silva", age=7, sex="female", weight_kg=22, pregnant=False,
                    allergies=[], conditions=[], current_meds=[]),
            Patient(name="Sara Khan", age=28, sex="female", weight_kg=58, pregnant=True,
                    allergies=[], conditions=[], current_meds=[]),
        ])
    db.commit()
