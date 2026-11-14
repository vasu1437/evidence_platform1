import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api.js";
import Login from "./components/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Evidence from "./pages/Evidence.jsx";
import Timeline from "./pages/Timeline.jsx";
import Ask from "./pages/Ask.jsx";
import People from "./pages/People.jsx";
import Audit from "./pages/Audit.jsx";
import Report from "./pages/Report.jsx";
import Viewer from "./components/Viewer.jsx";

const NAV = [["/dashboard", "Dashboard"], ["/evidence", "Evidence"], ["/timeline", "Timeline"], ["/ask", "Ask"], ["/people", "Evidence graph"], ["/report", "Case report"], ["/audit", "Audit log"]];
const ICONS = {
  Dashboard: ["M3 3h8v8H3z", "M14 3h7v5h-7z", "M14 12h7v9h-7z", "M3 14h8v7H3z"],
  Evidence: ["M7 3.75h7.5L19 8v12.25H7a2 2 0 0 1-2-2v-12.5a2 2 0 0 1 2-2Z", "M14 4v4h4", "M9 12h6", "M9 16h6"],
  Timeline: ["M5 6h14", "M5 12h14", "M5 18h14", "M8 6v.01", "M14 12v.01", "M10 18v.01"],
  Ask: ["M20 11.5a7.5 7.5 0 0 1-7.5 7.5H6l-3 2v-5.5a7.5 7.5 0 1 1 17-4Z", "M8 10h8", "M8 14h5"],
  "Evidence graph": ["M7 7.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M17 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "M7 21.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z", "m9 6 5.5 3.5M9 17l5.5-3.5"],
  "Case report": ["M7 3.75h7.5L19 8v12.25H7a2 2 0 0 1-2-2v-12.5a2 2 0 0 1 2-2Z", "M14 4v4h4", "M9 12h6", "M9 16h6"],
  "Audit log": ["M8 6h11", "M8 12h11", "M8 18h11", "m4.5 6 .75.75L6.5 5.5", "m4.5 12 .75.75 1.25-1.25", "m4.5 18 .75.75 1.25-1.25"],
};

export default function App() {
  const [target, setTarget] = useState(null); // what the evidence panel is showing
  const [session, setSession] = useState(null);
  const [sessionError, setSessionError] = useState("");
  const [logoutError, setLogoutError] = useState("");
  const location = useLocation();
  const open = (t) => setTarget(t);
  const pageTitle = NAV.find(([to]) => to === location.pathname)?.[1] || "Dashboard";

  const refreshSession = async () => {
    setSessionError("");
    try {
      setSession(await api.authSession());
    } catch (error) {
      setSession({ authenticated: false });
      setSessionError(`Unable to check your sign-in: ${error.message}`);
    }
  };

  useEffect(() => {
    refreshSession();
  }, []);

  useEffect(() => {
    const expireSession = () => {
      setTarget(null);
      setSession({ authenticated: false });
      setSessionError("Your session has expired. Sign in again to continue.");
    };
    window.addEventListener("casefile-auth-expired", expireSession);
    return () => window.removeEventListener("casefile-auth-expired", expireSession);
  }, []);

  const signIn = async (credentials) => {
    const nextSession = await api.authLogin(credentials);
    setSession(nextSession);
    setSessionError("");
  };

  const signUp = async (credentials) => {
    const nextSession = await api.authRegister(credentials);
    setSession(nextSession);
    setSessionError("");
  };

  const signOut = async () => {
    setLogoutError("");
    try {
      await api.authLogout();
      setTarget(null);
      setSession({ authenticated: false });
    } catch (error) {
      setLogoutError(`Could not sign out: ${error.message}`);
    }
  };

  if (!session) {
    return <div className="auth-loading" role="status">Checking secure session…</div>;
  }
  if (!session.authenticated) {
    return <Login error={sessionError} onLogin={signIn} onRegister={signUp} onRetry={refreshSession} />;
  }

  return (
    <div className={"app" + (target ? " with-panel" : "")}>
      <aside className="side">
        <NavLink to="/dashboard" className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M5 4.5h9l5 5v10H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z" /><path d="M14 5v5h5M7.5 14h8M7.5 17h5" /></svg>
          </span>
          <span><b>CASEFILE</b><small>Evidence intelligence</small></span>
        </NavLink>
        <div className="case-switcher">
          <span className="case-switcher-icon">C</span>
          <span><small>ACTIVE WORKSPACE</small><b>Current investigation</b></span>
          <i aria-hidden="true" />
        </div>
        <nav className="nav-list" aria-label="Case sections">
          <span className="nav-heading">WORKSPACE</span>
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={to} title={label} aria-label={label} className={({ isActive }) => "nav" + (isActive ? " on" : "")}>
              <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">{ICONS[label].map((path) => <path key={path} d={path} />)}</svg>
              <span>{label}</span>
              {location.pathname === to && <i className="nav-active-mark" aria-hidden="true" />}
            </NavLink>
          ))}
        </nav>
        <div className="side-footer">
          <span className="footer-orbit" aria-hidden="true"><i /></span>
          <span><b>Evidence, connected</b><small>Every claim leads to a source.</small></span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-crumb"><span>CASE WORKSPACE</span><i>/</i><b>{pageTitle}</b></div>
          <div className="topbar-actions">
            {logoutError && <span className="auth-inline-error" role="alert">{logoutError}</span>}
            <span className="topbar-status"><i aria-hidden="true" /> Signed in as {session.username}</span>
            <button type="button" className="sign-out-button" onClick={signOut}>Sign out</button>
          </div>
        </header>
        <main className={"main" + (location.pathname === "/people" ? " network-main" : "")}>
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/evidence" element={<Evidence open={open} />} />
            <Route path="/timeline" element={<Timeline open={open} />} />
            <Route path="/ask" element={<Ask open={open} />} />
            <Route path="/people" element={<People open={open} userId={session.user_id} />} />
            <Route path="/report" element={<Report />} />
            <Route path="/audit" element={<Audit />} />
          </Routes>
        </main>
      </div>
      {target && <Viewer target={target} onClose={() => setTarget(null)} />}
    </div>
  );
}
