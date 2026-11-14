import { useState } from "react";
import { api, toTarget } from "../api.js";

export default function Ask({ open }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState([]);

  const submit = async (e) => {
    e.preventDefault();
    if (!q.trim() || busy) return;
    setBusy(true);
    try {
      const r = await api.ask(q);
      setItems([{ q, r }, ...items]);
      setQ("");
    } catch (err) { setItems([{ q, r: { refused: true, answer: err.message, citations: [], warnings: [] } }, ...items]); }
    setBusy(false);
  };

  return (
    <>
      <h2>Ask about this case</h2>
      <p className="sub">Answers use only your evidence and always show where they came from. If the evidence doesn't cover it, you'll be told.</p>
      <form className="inp" onSubmit={submit}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="When was the first threat made?" aria-label="Question" />
        <button className="btn pri" disabled={busy}>{busy ? "Checking…" : "Ask"}</button>
      </form>
      {items.map((it, i) => (
        <div key={i} className={"card" + (it.r.refused ? " ref" : "")}>
          <p className="q">{it.q}</p>
          {it.r.answer}
          <div>{it.r.citations.map((c) => <button key={c.chunk_id} className="chip" onClick={() => open(toTarget(c))}>{c.ref}</button>)}</div>
          {it.r.warnings?.length > 0 && <p className="mt">Check these parts against the sources: {it.r.warnings.join("; ")}</p>}
        </div>
      ))}
    </>
  );
}
