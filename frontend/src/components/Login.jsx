import { useState } from "react";

export default function Login({ error, onLogin, onRegister, onRetry }) {
  const [registering, setRegistering] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    setFormError("");
    if (registering && password !== confirmation) {
      setFormError("The passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await (registering ? onRegister : onLogin)({ username, password });
    } catch (reason) {
      setFormError(reason.message || (registering ? "Registration failed." : "Sign-in failed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-screen">
      <div className="auth-glow auth-glow-one" aria-hidden="true" />
      <div className="auth-glow auth-glow-two" aria-hidden="true" />
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand">
          <span className="auth-brand-mark" aria-hidden="true">C</span>
          <span><b>CASEFILE</b><small>Evidence intelligence</small></span>
        </div>
        <div className="auth-eyebrow"><i /> PRIVATE CASE WORKSPACE</div>
        <h1 id="auth-title">{registering ? "Create your account" : "Welcome back"}</h1>
        <p className="auth-description">
          {registering
            ? "Create a private workspace. Your evidence stays separate from other accounts."
            : "Sign in to continue to your evidence workspace."}
        </p>
        {(formError || error) && (
          <div className="auth-error" role="alert">
            <span>{formError || error}</span>
            {error && !formError && <button type="button" onClick={onRetry}>Retry</button>}
          </div>
        )}
        <form className="auth-form" onSubmit={submit}>
          <label htmlFor="auth-username">Username</label>
          <input
            id="auth-username"
            autoComplete="username"
            autoFocus
            required
            maxLength={64}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
          <label htmlFor="auth-password">Password</label>
          <input
            id="auth-password"
            type="password"
            autoComplete={registering ? "new-password" : "current-password"}
            required
            maxLength={1024}
            minLength={registering ? 12 : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {registering && (
            <>
              <label htmlFor="auth-confirmation">Confirm password</label>
              <input
                id="auth-confirmation"
                type="password"
                autoComplete="new-password"
                required
                maxLength={1024}
                minLength={12}
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
              />
            </>
          )}
          <button type="submit" className="auth-submit" disabled={busy}>
            {busy ? (registering ? "Creating account…" : "Signing in…") : (registering ? "Create account" : "Sign in")}
            {!busy && <span aria-hidden="true">→</span>}
          </button>
        </form>
        <button
          type="button"
          className="auth-mode-toggle"
          onClick={() => {
            setRegistering(!registering);
            setPassword("");
            setConfirmation("");
            setFormError("");
          }}
        >
          {registering ? "Already registered? Sign in" : "New here? Create an account"}
        </button>
        <p className="auth-session-note">
          <span aria-hidden="true">◷</span>
          {registering ? "Use a password with at least 12 characters." : "Sessions expire automatically after 8 hours."}
        </p>
      </section>
      <div className="auth-visual" aria-hidden="true">
        <div className="auth-visual-ring auth-visual-ring-one" />
        <div className="auth-visual-ring auth-visual-ring-two" />
        <div className="auth-visual-center"><span>✦</span></div>
        <i className="auth-node auth-node-one" />
        <i className="auth-node auth-node-two" />
        <i className="auth-node auth-node-three" />
      </div>
    </main>
  );
}
