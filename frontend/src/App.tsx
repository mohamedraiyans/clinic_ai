import { useEffect, useState } from "react";
import * as api from "./api";
import type { AnalyzeResult, Medicine, Patient, QA, Visit } from "./api";

const LIKELIHOOD: Record<string, string> = {
  most_likely: "Most likely", possible: "Possible", less_likely: "Less likely",
};

export default function App() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [complaint, setComplaint] = useState("");
  const [qa, setQa] = useState<QA[]>([]);
  const [answers, setAnswers] = useState<string[]>([]);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [items, setItems] = useState<Medicine[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pdfUrl, setPdfUrl] = useState("");

  const [visits, setVisits] = useState<Visit[]>([]);

  useEffect(() => { api.getPatients().then(setPatients).catch((e) => setError(e.message)); }, []);

  const loadHistory = (id: number) =>
    api.getHistory(id).then(setVisits).catch((e) => setError(e.message));
  useEffect(() => { setVisits([]); if (patient) loadHistory(patient.id); }, [patient?.id]);

  const reset = () => { setQa([]); setAnswers([]); setResult(null); setItems([]); setPdfUrl(""); setError(""); };

  const run = async (history: QA[]) => {
    if (!patient) return;
    setBusy(true); setError("");
    try {
      const r = await api.analyze(patient.id, complaint, history);
      setResult(r); setQa(history); setAnswers(r.questions.map(() => ""));
      setItems(r.medicines);
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  const submitAnswers = () => {
    if (!result) return;
    run([...qa, ...result.questions.map((q, i) => ({ question: q, answer: answers[i] || "(not available)" }))]);
  };

  const editItem = (i: number, k: keyof Medicine, v: string) =>
    setItems(items.map((it, j) => (j === i ? { ...it, [k]: v } : it)));

  const approve = async () => {
    if (!patient || !result) return;
    setBusy(true); setError("");
    try {
      const r = await api.sign(patient.id, complaint, result.assessments, items);
      setPdfUrl(r.pdf_url);
      loadHistory(patient.id);
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="app">
      <aside>
        <h2>Patients</h2>
        {patients.map((p) => (
          <button key={p.id} className={"pt" + (patient?.id === p.id ? " on" : "")}
            onClick={() => { setPatient(p); setComplaint(""); reset(); }}>
            <b>{p.name}</b><span>{p.age}y · {p.sex}{p.pregnant ? " · pregnant" : ""}</span>
          </button>
        ))}
      </aside>

      <main>
        <h1>Clinic AI <small>doctor assistant · AI drafts, doctor decides</small></h1>
        {error && <div className="err">{error}</div>}
        {!patient ? <p>Select a patient.</p> : (
          <>
            <section className="card">
              <b>{patient.name}</b> · {patient.age}y · {patient.weight_kg} kg
              <div className="tags">
                <span className="tag red">Allergies: {patient.allergies.join(", ") || "none"}</span>
                <span className="tag">Conditions: {patient.conditions.join(", ") || "none"}</span>
                <span className="tag">Current meds: {patient.current_meds.join(", ") || "none"}</span>
              </div>
            </section>

            <section className="card">
              <details>
                <summary><b>Visit history</b> ({visits.length})</summary>
                {visits.length === 0 && <p className="muted">No previous visits.</p>}
                {visits.map((v) => (
                  <div key={v.id} className="visit">
                    <div className="row">
                      <b>{v.issued_at}</b>
                      <a href={v.pdf_url} target="_blank" rel="noreferrer">PDF</a>
                    </div>
                    <div>{v.complaint}</div>
                    {v.assessments[0] && <small>Dx: {v.assessments.map((a) => a.condition).join(", ")}</small>}
                    <div className="tags">
                      {v.items.map((m, i) => <span key={i} className="tag">{m.name} {m.dose}</span>)}
                    </div>
                  </div>
                ))}
              </details>
            </section>

            <section className="card">
              <label>Complaint &amp; examination findings</label>
              <textarea rows={4} value={complaint} onChange={(e) => setComplaint(e.target.value)}
                placeholder="e.g. 3 days of sore throat, fever 38.5, white patches on tonsils, no cough" />
              <button className="primary" disabled={busy || !complaint.trim()}
                onClick={() => { setPdfUrl(""); run([]); }}>
                {busy ? "Thinking…" : "Analyze"}
              </button>
            </section>

            {result?.status === "needs_info" && (
              <section className="card ask">
                <b>The AI needs more information:</b>
                {result.questions.map((q, i) => (
                  <div key={i}>
                    <label>{q}</label>
                    <input value={answers[i] ?? ""} onChange={(e) => setAnswers(answers.map((a, j) => (j === i ? e.target.value : a)))} />
                  </div>
                ))}
                <button className="primary" disabled={busy} onClick={submitAnswers}>Submit answers</button>
              </section>
            )}

            {result?.status === "ready" && (
              <>
                {result.red_flags.length > 0 && <div className="err">⚠ {result.red_flags.join(" · ")}</div>}
                <section className="card">
                  <h3>Possible conditions</h3>
                  {result.assessments.map((a, i) => (
                    <div key={i} className="assess">
                      <b>{a.condition}</b> <span className={"tag " + a.likelihood}>{LIKELIHOOD[a.likelihood] ?? a.likelihood}</span>
                      <p>{a.reasoning}</p>
                    </div>
                  ))}
                </section>

                <section className="card">
                  <h3>Suggested prescription <small>(edit freely)</small></h3>
                  {items.map((m, i) => (
                    <div key={i} className="med">
                      <div className="row"><b>{m.name}</b>
                        <button className="link" disabled={!!pdfUrl} onClick={() => setItems(items.filter((_, j) => j !== i))}>remove</button></div>
                      {m.for_condition && <small>for {m.for_condition}</small>}
                      {m.warnings?.map((w, k) => <div key={k} className="warn">⚠ {w.message}</div>)}
                      <div className="grid">
                        <textarea rows={2} value={m.dose} onChange={(e) => editItem(i, "dose", e.target.value)} placeholder="dose" />
                        <textarea rows={2} value={m.frequency} onChange={(e) => editItem(i, "frequency", e.target.value)} placeholder="frequency" />
                        <textarea rows={2} value={m.duration} onChange={(e) => editItem(i, "duration", e.target.value)} placeholder="duration" />
                      </div>
                      <textarea rows={2} value={m.instructions ?? ""} onChange={(e) => editItem(i, "instructions", e.target.value)} placeholder="instructions" />
                    </div>
                  ))}
                  {result.removed.map((r, i) => (
                    <div key={i} className="removed">Blocked: {r.name}. {r.reason}</div>
                  ))}
                  <button className="primary" disabled={busy || items.length === 0 || !!pdfUrl} onClick={approve}>
                    {pdfUrl ? "Signed" : "Approve & sign"}
                  </button>
                  {pdfUrl && (
                    <div className="ok">
                      Prescription signed. <a href={pdfUrl} target="_blank" rel="noreferrer">Open PDF</a>
                    </div>
                  )}
                </section>
              </>
            )}
            {result && <small className="muted">Model provider: {result.provider}</small>}
          </>
        )}
      </main>
    </div>
  );
}
