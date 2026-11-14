import { useEffect, useState } from "react";
import { api } from "../api.js";

export default function Audit() {
  const [rows, setRows] = useState([]);
  const [chk, setChk] = useState(null);
  const [link, setLink] = useState("");
  useEffect(() => { api.audit().then(setRows).catch(() => {}); }, [chk]);
  return (
    <>
      <h2>Audit log</h2>
      <p className="sub">Every action is recorded. Each entry includes a code linked to the one before it, so changes to the log are detectable.</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn pri" onClick={() => api.auditVerify().then(setChk)}>Check the log is intact</button>
        <button className="btn" onClick={() => api.share().then((s) => setLink(`Share code (valid ${s.expires_in_days} days): ${s.token}`))}>Create share link</button>
      </div>
      {chk && <p className={"flag " + (chk.ok ? "okbox" : "")}>{chk.ok ? "The log is intact. No entry was changed or removed." : `The log was changed. It breaks at entry ${chk.broken_at}.`}</p>}
      {link && <p className="flag okbox">{link}</p>}
      <table>
        <thead><tr><th>Time</th><th>Who</th><th>Action</th><th>Detail</th><th>Link code</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id}><td>{r.at.replace("T", " ").slice(0, 16)}</td><td>{r.actor}</td><td>{r.action}</td><td>{r.detail}</td><td><code>{r.hash.slice(0, 8)}</code></td></tr>
        ))}</tbody>
      </table>
    </>
  );
}
