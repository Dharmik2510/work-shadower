import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { KeyRound } from "lucide-react";
import { api } from "../api";
import { useAuth } from "../auth/AuthContext";
import { beginOidcLogin } from "../auth/oidc";
import { Avatar, AVATARS } from "../components/Avatar";
import { Spinner } from "../components/bits";

export default function Login() {
  const { user, config, loading, configError, signIn } = useAuth();
  const [params] = useSearchParams();
  const next = params.get("next") || "/";
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [team, setTeam] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (user) return <Navigate to={next} replace />;

  async function devSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.devLogin({ email: email.trim(), name: name.trim(), team: team.trim() || undefined });
      await signIn(r.token, r.user);
      nav(next, { replace: true });
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function sso() {
    if (!config?.oidc) return;
    setBusy(true);
    setError(null);
    try {
      await beginOidcLogin(config.oidc.issuer, config.oidc.client_id, next);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login__panel">
        <div className="login__crew" role="img" aria-label="The five Work Shadower dots">
          {AVATARS.map((a, i) => (
            <Avatar key={a.kind} kind={a.kind} size={i === 2 ? 64 : 46} awake={i === 2} idle={i === 2} style={{ ["--i" as string]: i }} />
          ))}
        </div>
        <h1>Sign in to Work Shadower</h1>
        <p className="login__lede">Learn how colleagues get things done, or teach the dot one of your own workflows.</p>

        {loading && <Spinner label="Loading sign-in options" />}
        {configError && (
          <div className="note note--bad" role="alert">
            <p>Can't reach the Work Shadower server. {configError}</p>
          </div>
        )}

        {config?.auth_mode === "oidc" && (
          <div className="login__sso">
            <button className="btn btn--primary btn--lg" onClick={sso} disabled={busy || !config.oidc}>
              <KeyRound size={18} aria-hidden />
              {busy ? "Opening sign-in…" : "Sign in with SSO"}
            </button>
            <p className="hint">You'll sign in with your company account, then come straight back here.</p>
          </div>
        )}

        {config?.auth_mode === "dev" && (
          <form className="form login__form" onSubmit={devSubmit}>
            <label className="field">
              <span>Work email</span>
              <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
            </label>
            <label className="field">
              <span>Name</span>
              <input required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="How colleagues see you" />
            </label>
            <label className="field">
              <span>
                Team <em>optional</em>
              </span>
              <input value={team} onChange={(e) => setTeam(e.target.value)} placeholder="e.g. Auto Claims" />
            </label>
            <button className="btn btn--primary btn--lg" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <p className="hint">Development sign-in: no password is checked. Production uses your company SSO.</p>
          </form>
        )}

        {error && (
          <div className="note note--bad" role="alert">
            <p>{error}</p>
          </div>
        )}
      </div>
    </div>
  );
}
