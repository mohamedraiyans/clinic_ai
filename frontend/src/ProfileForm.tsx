import { useState } from "react";
import * as api from "./api";
import type { Doctor } from "./api";

interface Props {
  doctor: Doctor;
  required?: boolean; // first login: can't be dismissed until saved
  onSaved: (d: Doctor) => void;
  onCancel?: () => void;
}

export default function ProfileForm({ doctor, required, onSaved, onCancel }: Props) {
  const [name, setName] = useState(doctor.name);
  const [regNo, setRegNo] = useState(doctor.reg_no);
  const [clinic, setClinic] = useState(doctor.clinic);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true); setError("");
    try { onSaved(await api.updateMe({ name, reg_no: regNo, clinic })); }
    catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="overlay">
      <div className="card modal">
        <h3>{required ? "Complete your doctor profile" : "Doctor profile"}</h3>
        <p className="muted">These details are printed on every prescription you sign.</p>
        {error && <div className="err">{error}</div>}
        <label>Name (as printed, e.g. Dr. Jane Silva)</label>
        <input value={name} onChange={(e) => setName(e.target.value)} />
        <label>Medical registration number</label>
        <input value={regNo} onChange={(e) => setRegNo(e.target.value)} />
        <label>Clinic name</label>
        <input value={clinic} onChange={(e) => setClinic(e.target.value)} />
        <div className="actions">
          {!required && <button className="secondary" onClick={onCancel}>Cancel</button>}
          <button className="primary" disabled={busy || !name || !regNo || !clinic} onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}
