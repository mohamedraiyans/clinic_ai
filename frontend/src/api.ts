export interface Patient {
  id: number; name: string; age: number; sex: string; weight_kg: number;
  pregnant: boolean; allergies: string[]; conditions: string[]; current_meds: string[];
}
export type PatientInput = Omit<Patient, "id">;
export interface Doctor {
  id: number; name: string; email: string; picture: string | null;
  reg_no: string; clinic: string; profile_complete: boolean;
}
export interface AuthConfig { google: boolean; dev_login: boolean }
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
export interface Visit {
  id: string; issued_at: string; doctor: string; complaint: string;
  assessments: Assessment[]; items: Medicine[]; pdf_url: string;
}

/** Thrown when the session is missing or expired, so the UI can go back to the login screen. */
export class AuthError extends Error {}

function errorText(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) // FastAPI validation errors
    return detail.map((d) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
  return fallback;
}

async function req<T>(url: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) throw new AuthError("Please sign in");
  if (!r.ok) throw new Error(errorText((await r.json().catch(() => ({}))).detail, r.statusText));
  return r.json();
}

export const getConfig = () => req<AuthConfig>("/api/auth/config");
export const getMe = () => req<Doctor>("/api/auth/me");
export const updateMe = (p: { name: string; reg_no: string; clinic: string }) => req<Doctor>("/api/auth/me", "PUT", p);
export const devLogin = (email: string) => req<Doctor>("/api/auth/dev-login", "POST", { email });
export const logout = () => req<{ ok: boolean }>("/api/auth/logout", "POST");

export const getPatients = () => req<Patient[]>("/api/patients");
export const createPatient = (p: PatientInput) => req<Patient>("/api/patients", "POST", p);
export const updatePatient = (id: number, p: PatientInput) => req<Patient>(`/api/patients/${id}`, "PUT", p);
export const getHistory = (id: number) => req<Visit[]>(`/api/patients/${id}/history`);

export const analyze = (patient_id: number, complaint: string, qa: QA[]) =>
  req<AnalyzeResult>("/api/analyze", "POST", { patient_id, complaint, qa });
export const sign = (patient_id: number, complaint: string, assessments: Assessment[], items: Medicine[]) =>
  req<{ id: string; pdf_url: string }>("/api/prescriptions", "POST", { patient_id, complaint, assessments, items });
