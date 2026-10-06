import { useEffect, useState } from "react";
import * as api from "./api";
import type { Doctor } from "./api";

const ERRORS: Record<string, string> = {
  not_allowed: "This Google account is not authorised as a doctor for this clinic.",
  cancelled: "Sign-in was cancelled.",
  bad_state: "Sign-in session expired. Please try again.",
  google_failed: "Google sign-in failed. Please try again.",
  unverified: "Your Google email is not verified.",
};

export default function Login({ onLogin }: { onLogin: (d: Doctor) => void }) {
  const [cfg, setCfg] = useState<api.AuthConfig | null>(null);
  const [email, setEmail] = useState("");
  const [error, setError] = useState(() => {
    const code = new URLSearchParams(window.location.search).get("error");
    return code ? ERRORS[code] ?? "Sign-in failed." : "";
  });

  useEffect(() => {
    api.getConfig().then(setCfg).catch((e) => setError(e.message));
    if (window.location.search) window.history.replaceState({}, "", "/");
  }, []);

  const dev = async () => {
    setError("");
    try { onLogin(await api.devLogin(email)); } catch (e) { setError((e as Error).message); }
  };

  return (
    <div className="login">
      <div className="card login-card">
        <h1>Clinic AI</h1>
        <p className="muted">Doctor sign-in. AI drafts, the doctor decides.</p>
        {error && <div className="err">{error}</div>}
        {cfg?.google && <a className="primary google" href="/api/auth/google/login">Sign in with Google</a>}
        {cfg && !cfg.google && !cfg.dev_login && (
          <div className="err">Google login is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.</div>
        )}
        {cfg?.dev_login && (
          <div className="devbox">
            <small className="muted">Local testing login (DEV_LOGIN=1). Disabled in production.</small>
            <input placeholder="doctor email" value={email} onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && email && dev()} />
            <button className="primary" disabled={!email} onClick={dev}>Continue</button>
          </div>
        )}
      </div>
    </div>
  );
}
