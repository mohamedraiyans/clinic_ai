import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import * as api from "./api";
import type { AgentStep } from "./api";

interface Msg { role: "user" | "assistant"; text: string; steps?: AgentStep[]; provider?: string; stopped?: boolean }

const IDEAS = [
  "What is this patient taking now?",
  "Which medicines does the clinic not block for this patient?",
  "What did we prescribe last time?",
];

/** Show **bold** from the model as bold, and drop backticks. No HTML is ever injected. */
function plain(text: string): ReactNode[] {
  return text.replace(/`/g, "").split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <b key={i}>{part}</b> : part));
}

function describe(step: AgentStep): { label: string; badge?: { text: string; kind: string } } {
  const r = step.result as { error?: string; blocked?: boolean; found?: boolean; warnings?: unknown[] };
  if (r.error) return { label: `${step.tool}: could not run`, badge: { text: "Error", kind: "bad" } };
  switch (step.tool) {
    case "get_patient_summary": return { label: "Read the patient summary" };
    case "list_recent_visits": return { label: "Read recent visits" };
    case "list_safe_medicines": return { label: "Listed the medicines the rules do not block" };
    case "check_medicine_safety": {
      const name = String(step.args.medicine_name ?? "");
      if (r.found === false) return { label: `Checked ${name}`, badge: { text: "Not in formulary", kind: "warn" } };
      if (r.blocked) return { label: `Checked ${name}`, badge: { text: "Blocked", kind: "bad" } };
      if (r.warnings && r.warnings.length) return { label: `Checked ${name}`, badge: { text: "Warning", kind: "warn" } };
      return { label: `Checked ${name}`, badge: { text: "Not blocked", kind: "ok" } };
    }
    default: return { label: step.tool };
  }
}

interface Props { patientId: number; onAuthLost: () => void }

/** Chat box where the doctor asks the read-only assistant about the selected patient.
 *  Mount it with key={patientId} so a new patient starts a new conversation. */
export default function AskPanel({ patientId, onAuthLost }: Props) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [msgs, busy]);

  const send = async (q: string) => {
    const question = q.trim();
    if (!question || busy) return;
    const history = msgs.map((m) => ({ role: m.role, content: m.text }));
    setMsgs([...msgs, { role: "user", text: question }]);
    setText(""); setBusy(true); setError("");
    try {
      const r = await api.askAgent(patientId, question, history);
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, steps: r.steps, provider: r.provider, stopped: r.stopped }]);
    } catch (e) {
      if (e instanceof api.AuthError) onAuthLost(); else setError((e as Error).message);
    }
    setBusy(false);
  };

  return (
    <section className="card ask-panel">
      <details open={msgs.length > 0 || undefined}>
        <summary><b>Ask the assistant</b> <small className="muted">about this patient</small></summary>
        <div className="chat" aria-live="polite">
          {msgs.length === 0 && (
            <div className="ideas">
              {IDEAS.map((q) => <button key={q} className="idea" disabled={busy} onClick={() => send(q)}>{q}</button>)}
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={"bubble " + m.role}>
              <div className="text">{plain(m.text)}</div>
              {m.steps && m.steps.length > 0 && (
                <ul className="steps">
                  {m.steps.map((s, k) => {
                    const d = describe(s);
                    return (
                      <li key={k}>
                        <details>
                          <summary>{d.label}{d.badge && <span className={"badge " + d.badge.kind}>{d.badge.text}</span>}</summary>
                          <pre>{JSON.stringify(s.result, null, 2)}</pre>
                        </details>
                      </li>
                    );
                  })}
                </ul>
              )}
              {m.stopped && <small className="muted">The assistant stopped early. Try a narrower question.</small>}
            </div>
          ))}
          {busy && <div className="bubble assistant"><span className="muted">Looking it up…</span></div>}
          <div ref={end} />
        </div>
        {error && <div className="err">{error}</div>}
        <form className="ask-row" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input id="ask-input" value={text} maxLength={1000} disabled={busy}
            placeholder="e.g. Is amoxicillin safe for this patient?" onChange={(e) => setText(e.target.value)} />
          <button className="primary" disabled={busy || !text.trim()}>Ask</button>
        </form>
        <p className="muted ask-note">
          <small>
            Read-only. It looks things up in this patient's record and the clinic's rules. It does not diagnose or
            prescribe, the rules are simple (allergy, pregnancy, age, a few interactions), and the doctor decides.
            Do not type the patient's name.
          </small>
        </p>
      </details>
    </section>
  );
}
