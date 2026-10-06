export interface Patient {
  id: number; name: string; age: number; sex: string; weight_kg: number;
  pregnant: boolean; allergies: string[]; conditions: string[]; current_meds: string[];
}
export interface Assessment { condition: string; likelihood: string; reasoning: string }
export interface Warning { severity: string; message: string }
export interface Medicine {
  name: string; dose: string; frequency: string; duration: string;
  instructions?: string; for_condition?: string; warnings?: Warning[];
}
export interface QA { question: string; answer: string }
export interface AnalyzeResult {
  status: "needs_info" | "ready"; questions: string[]; assessments: Assessment[];
  medicines: Medicine[]; removed: { name: string; reason: string }[];
  red_flags: string[]; provider: string;
}

async function req<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
  return r.json();
}

export interface Visit {
  id: string; issued_at: string; doctor: string; complaint: string;
  assessments: Assessment[]; items: Medicine[]; pdf_url: string;
}
export const getHistory = (id: number) => req<Visit[]>(`/api/patients/${id}/history`);
export const getPatients = () => req<Patient[]>("/api/patients");
export const analyze = (patient_id: number, complaint: string, qa: QA[]) =>
  req<AnalyzeResult>("/api/analyze", { patient_id, complaint, qa });
export const sign = (patient_id: number, complaint: string, assessments: Assessment[], items: Medicine[]) =>
  req<{ id: string; pdf_url: string }>("/api/prescriptions", { patient_id, complaint, assessments, items });
