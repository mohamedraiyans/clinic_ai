import { useState } from "react";
import * as api from "./api";
import type { Patient } from "./api";

interface Props {
  patient?: Patient; // present = edit, absent = add
  onSaved: (p: Patient) => void;
  onCancel: () => void;
  onAuthLost: () => void;
}

const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export default function PatientForm({ patient, onSaved, onCancel, onAuthLost }: Props) {
  const [name, setName] = useState(patient?.name ?? "");
  const [age, setAge] = useState(patient ? String(patient.age) : "");
  const [sex, setSex] = useState(patient?.sex ?? "female");
  const [weight, setWeight] = useState(patient ? String(patient.weight_kg) : "");
  const [pregnant, setPregnant] = useState(patient?.pregnant ?? false);
  const [allergies, setAllergies] = useState(patient?.allergies.join(", ") ?? "");
  const [conditions, setConditions] = useState(patient?.conditions.join(", ") ?? "");
  const [meds, setMeds] = useState(patient?.current_meds.join(", ") ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const valid = name.trim() && age !== "" && Number(weight) > 0;

  const save = async () => {
    setBusy(true); setError("");
    const data = {
      name, age: Number(age), sex, weight_kg: Number(weight), pregnant: sex === "female" && pregnant,
      allergies: split(allergies), conditions: split(conditions), current_meds: split(meds),
    };
    try {
      onSaved(patient ? await api.updatePatient(patient.id, data) : await api.createPatient(data));
    } catch (e) {
      if (e instanceof api.AuthError) onAuthLost(); else setError((e as Error).message);
    }
    setBusy(false);
  };

  return (
    <div className="overlay">
      <div className="card modal">
        <h3>{patient ? "Edit patient" : "Add patient"}</h3>
        {error && <div className="err">{error}</div>}
        <label>Full name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <div className="grid">
          <div><label>Age</label>
            <input type="number" min={0} max={130} value={age} onChange={(e) => setAge(e.target.value)} /></div>
          <div><label>Sex</label>
            <select value={sex} onChange={(e) => setSex(e.target.value)}>
              <option value="female">Female</option><option value="male">Male</option><option value="other">Other</option>
            </select></div>
          <div><label>Weight (kg)</label>
            <input type="number" min={0} step="0.1" value={weight} onChange={(e) => setWeight(e.target.value)} /></div>
        </div>
        {sex === "female" && (
          <label className="check"><input type="checkbox" checked={pregnant} onChange={(e) => setPregnant(e.target.checked)} /> Pregnant</label>
        )}
        <label>Allergies <small>(comma separated, drug names or classes, e.g. penicillin, nsaid)</small></label>
        <input value={allergies} onChange={(e) => setAllergies(e.target.value)} />
        <label>Conditions <small>(comma separated)</small></label>
        <input value={conditions} onChange={(e) => setConditions(e.target.value)} />
        <label>Current medicines <small>(comma separated)</small></label>
        <input value={meds} onChange={(e) => setMeds(e.target.value)} />
        <p className="muted"><small>Allergies and current medicines drive the safety checks. Every change is recorded in the audit log.</small></p>
        <div className="actions">
          <button className="secondary" onClick={onCancel}>Cancel</button>
          <button className="primary" disabled={busy || !valid} onClick={save}>{patient ? "Save changes" : "Add patient"}</button>
        </div>
      </div>
    </div>
  );
}
