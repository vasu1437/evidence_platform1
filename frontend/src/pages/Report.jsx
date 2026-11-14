import { useEffect, useState } from "react";
import { api } from "../api.js";

const formatBytes = (size = 0) => {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
};

const excerpt = (text = "") => {
  const clean = text.trim();
  return clean.length > 420 ? `${clean.slice(0, 420).trimEnd()}…` : clean;
};

export default function Report() {
  const [report, setReport] = useState(null);
  const [chunksById, setChunksById] = useState(new Map());
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [files, timeline, audit, auditCheck, graph] = await Promise.all([
          api.files(), api.timeline(), api.audit(), api.auditVerify(), api.graph(),
        ]);
        const sources = [...timeline.events, ...timeline.flags].flatMap((row) => row.sources || []);
        const fileIds = [...new Set(sources.filter((source) => source.chunk_id != null).map((source) => source.file_id))];
        const chunkGroups = await Promise.all(fileIds.map(async (fileId) => [fileId, await api.chunks(fileId)]));
        if (!active) return;
        const lookup = new Map();
        chunkGroups.forEach(([, chunks]) => chunks.forEach((chunk) => lookup.set(chunk.id, chunk.text)));
        setChunksById(lookup);
        setReport({ files, timeline, audit, auditCheck, graph, generatedAt: new Date().toLocaleString() });
      } catch (e) {
        if (active) setError(e.message);
      }
    };
    load();
    return () => { active = false; };
  }, []);

  if (error) return <><h2>Case report</h2><p className="flag">{error}</p></>;
  if (!report) return <><h2>Case report</h2><p className="sub">Preparing source citations and checking the audit log…</p></>;

  const { files, timeline, audit, auditCheck, graph, generatedAt } = report;
  const events = timeline.events || [];
  const flags = timeline.flags || [];
  const auditOk = auditCheck.ok;
  const sourceLabel = (source) => [source.file_name, source.ref].filter(Boolean).join(" · ") || "Source reference unavailable";

  return (
    <article className="case-report">
      <div className="report-actions">
        <button className="btn pri" onClick={() => window.print()}>Print / Save as PDF</button>
      </div>
      <header className="report-header">
        <p className="report-kicker">EVIDENCE INTELLIGENCE PLATFORM</p>
        <h2>Evidence case report</h2>
        <p>Generated {generatedAt}</p>
        <p className="report-note">This report summarizes machine-extracted events and links each one to its source. It is an investigative aid, not a finding of fact. Review the cited original evidence before drawing conclusions.</p>
      </header>

      <section className="report-section">
        <h3>Case integrity</h3>
        <p className={auditOk ? "report-status good" : "report-status bad"}>
          Audit log: {auditOk ? "intact" : `verification failed at entry ${auditCheck.broken_at ?? "unknown"}`}
        </p>
        <p>{files.length} evidence file{files.length === 1 ? "" : "s"} · {events.length} extracted event{events.length === 1 ? "" : "s"} · {flags.length} flag{flags.length === 1 ? "" : "s"}</p>
      </section>

      <section className="report-section">
        <h3>Evidence register</h3>
        {files.length ? (
          <table>
            <thead><tr><th>File</th><th>Type / status</th><th>Integrity</th><th>Size</th><th>SHA-256</th></tr></thead>
            <tbody>{files.map((file) => (
              <tr key={file.id}>
                <td>{file.name}<br /><small>Added {file.uploaded_at?.slice(0, 10) || "date unavailable"}</small></td>
                <td>{file.type} / {file.status}</td>
                <td>{file.integrity === "saved" ? "Not yet verified" : file.integrity || "Unknown"}</td>
                <td>{formatBytes(file.size)}</td>
                <td className="report-hash">{file.sha256}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : <p>No evidence files are currently in this case.</p>}
      </section>

      <section className="report-section">
        <h3>Extracted events and source support</h3>
        {events.length ? events.map((event) => (
          <article className="report-event" key={event.id}>
            <p className="report-date">{event.date || "Date not established"}</p>
            <p>{event.description}</p>
            {event.sources?.length ? event.sources.map((source, i) => (
              <div className="report-citation" key={`${event.id}-${source.chunk_id}-${i}`}>
                <b>{sourceLabel(source)}</b>
                {chunksById.has(source.chunk_id) && <blockquote>{excerpt(chunksById.get(source.chunk_id))}</blockquote>}
              </div>
            )) : <p className="report-muted">No source citation is available for this event.</p>}
          </article>
        )) : <p>No events have been extracted yet.</p>}
      </section>

      <section className="report-section">
        <h3>Flags for review</h3>
        {flags.length ? flags.map((flag) => (
          <article className="report-event" key={flag.id}>
            <p><b>{flag.kind === "duplicate" ? "Possible duplicate" : flag.kind === "contradiction" ? "Possible contradiction" : flag.kind}</b> — {flag.note}</p>
            {(flag.sources || []).map((source, i) => (
              <div className="report-citation" key={`${flag.id}-${source.chunk_id || source.file_id}-${i}`}>
                <b>{sourceLabel(source)}</b>
                {source.chunk_id != null && chunksById.has(source.chunk_id) && <blockquote>{excerpt(chunksById.get(source.chunk_id))}</blockquote>}
              </div>
            ))}
          </article>
        )) : <p>No flags are currently recorded.</p>}
      </section>

      <section className="report-section">
        <h3>Evidence network summary</h3>
        <p>{graph.stats?.entities ?? 0} entities · {graph.stats?.document_links ?? 0} exact source links · {graph.stats?.unverified_entities ?? 0} unverified entities remain unlinked</p>
        <p className="report-muted">A source link means exact entity text was found in the cited document. Co-appearance alone does not establish a real-world relationship.</p>
      </section>

      <section className="report-section">
        <h3>Recent audit history</h3>
        <p className="report-muted">Latest {audit.length} entries returned by the audit log. The complete log remains available in the application.</p>
        {audit.length ? (
          <table>
            <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Detail</th><th>Hash</th></tr></thead>
            <tbody>{audit.map((entry) => (
              <tr key={entry.id}>
                <td>{entry.at.replace("T", " ").slice(0, 16)}</td>
                <td>{entry.actor}</td>
                <td>{entry.action}</td>
                <td>{entry.detail}</td>
                <td><code>{entry.hash}</code></td>
              </tr>
            ))}</tbody>
          </table>
        ) : <p>No audit entries are recorded.</p>}
      </section>
    </article>
  );
}
