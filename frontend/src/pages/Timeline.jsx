import { useEffect, useState } from "react";
import { api, mmss, toTarget } from "../api.js";

const chipLabel = (s) => (s.start_sec != null ? `${s.file_name} at ${mmss(s.start_sec)}` : s.ref);

export default function Timeline({ open }) {
  const [data, setData] = useState({ events: [], flags: [] });
  const [sel, setSel] = useState(null);
  const [summary, setSummary] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.timeline().then(setData).catch((e) => setErr(e.message)); }, []);

  const dated = data.events.filter((e) => e.date);
  const undated = data.events.filter((e) => !e.date);
  const Event = ({ e, und }) => (
    <div className={"ev" + (und ? " und" : "") + (sel === e.id ? " sel" : "")}>
      <div className="dt">{e.date || "No date found"}</div>
      <div>{e.description}</div>
      {e.sources.map((s) => (
        <button key={s.chunk_id} className="chip" onClick={() => { setSel(e.id); open(toTarget(s)); }}>{chipLabel(s)}</button>
      ))}
    </div>
  );

  return (
    <>
      <h2>Timeline</h2>
      <p className="sub">Select a source under any event to open the evidence behind it. Audio and video jump to the exact moment.</p>
      {err && <p className="flag">{err}</p>}
      {data.flags.map((f) => (
        <div className="flag" key={f.id}>
          <b>{f.kind === "duplicate" ? "Duplicate file." : "Two sources disagree."}</b> {f.note}
          <div>{f.sources.map((s, i) => <button key={i} className="chip" onClick={() => s.chunk_id ? open(toTarget(s)) : open({ fileId: s.file_id })}>{chipLabel(s)}</button>)}</div>
        </div>
      ))}
      {data.events.length === 0 && <p className="sub">No events yet. Upload evidence and wait until it is ready.</p>}
      <div className="tl">{dated.map((e) => <Event key={e.id} e={e} />)}</div>
      {undated.length > 0 && (<><h3 className="vt">Events with no date</h3><div className="tl">{undated.map((e) => <Event key={e.id} e={e} und />)}</div></>)}
      <div style={{ marginTop: 24 }}>
        <button className="btn" onClick={() => api.summary().then(setSummary).catch((e) => setErr(e.message))}>Write incident summary</button>
        {summary && (
          <div className="card" style={{ marginTop: 12 }}>
            <p>{summary.overview}</p>
            <ul>{(summary.key_points || []).map((k, i) => <li key={i}>{k.text}</li>)}</ul>
          </div>
        )}
      </div>
    </>
  );
}
