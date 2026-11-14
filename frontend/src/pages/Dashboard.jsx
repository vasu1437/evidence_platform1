import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";

const PROCESSING_STATUSES = new Set(["queued", "reading", "analysing"]);

function dateLabel(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString();
}

function OutcomePanel({ title, files, emptyMessage, tone }) {
  return (
    <section className={`dashboard-panel ${tone}`}>
      <header className="dashboard-panel-header">
        <h3>{title}</h3>
        <span>{files.length}</span>
      </header>
      {files.length ? (
        <>
          <ul className="dashboard-file-list">
            {files.slice(0, 5).map((file) => (
              <li key={file.id}>
                <i aria-hidden="true" />
                <div>
                  <b>{file.name}</b>
                  <small>{dateLabel(file.uploaded_at)}</small>
                </div>
              </li>
            ))}
          </ul>
          {files.length > 5 && <Link className="dashboard-list-link" to="/evidence">View all evidence <span aria-hidden="true">→</span></Link>}
        </>
      ) : <p className="dashboard-empty-list">{emptyMessage}</p>}
    </section>
  );
}

export default function Dashboard() {
  const [files, setFiles] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setFiles(await api.files());
    } catch (loadError) {
      setError(`Could not load dashboard data. ${loadError.message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const ready = files?.filter((file) => file.status === "ready") || [];
  const failed = files?.filter((file) => file.status === "failed") || [];
  const inProgress = files?.filter((file) => PROCESSING_STATUSES.has(file.status)) || [];
  const completed = ready.length + failed.length;
  const successRate = completed ? Math.round((ready.length / completed) * 100) : null;

  return (
    <section className="dashboard-page">
      <header className="dashboard-heading">
        <div>
          <p className="dashboard-eyebrow">WORKSPACE OVERVIEW</p>
          <h2>Dashboard</h2>
          <p className="sub">Track how evidence files are processing in this workspace.</p>
        </div>
        <div className="dashboard-actions">
          <button type="button" className="btn dashboard-refresh" onClick={load} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <Link className="btn pri dashboard-upload" to="/evidence">Upload evidence <span aria-hidden="true">→</span></Link>
        </div>
      </header>

      {error && (
        <div className="dashboard-error flag" role="alert">
          <span>{error}</span>
          <button type="button" className="btn" onClick={load} disabled={loading}>Retry</button>
        </div>
      )}
      {loading && files === null && <p className="sub" role="status">Loading dashboard data…</p>}

      {files && (
        <>
          <section className="dashboard-rate-card" aria-label="File processing success rate">
            <div className="dashboard-rate-copy">
              <p className="dashboard-eyebrow">PROCESSING SUCCESS RATE</p>
              <strong>{successRate === null ? "—" : `${successRate}%`}</strong>
              <p>{completed ? `${ready.length} of ${completed} completed ${completed === 1 ? "file" : "files"} processed successfully.` : "Calculated when a file finishes processing."}</p>
            </div>
            <div
              className="dashboard-rate-track"
              role="progressbar"
              aria-label="File processing success rate"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={successRate ?? 0}
            >
              <span style={{ width: `${successRate ?? 0}%` }} />
            </div>
            <small>Ready files ÷ (ready + failed). This measures file processing, not AI answer accuracy.</small>
          </section>

          <section className="dashboard-stats" aria-label="File processing totals">
            <article className="dashboard-stat">
              <span className="dashboard-stat-label">Total files</span>
              <strong>{files.length}</strong>
              <small>Evidence uploaded</small>
            </article>
            <article className="dashboard-stat success">
              <span className="dashboard-stat-label">Processed</span>
              <strong>{ready.length}</strong>
              <small>Ready to use</small>
            </article>
            <article className="dashboard-stat failure">
              <span className="dashboard-stat-label">Failed</span>
              <strong>{failed.length}</strong>
              <small>Need attention</small>
            </article>
            <article className="dashboard-stat pending">
              <span className="dashboard-stat-label">In progress</span>
              <strong>{inProgress.length}</strong>
              <small>Still processing</small>
            </article>
          </section>

          {files.length === 0 ? (
            <section className="dashboard-empty">
              <span className="dashboard-empty-mark" aria-hidden="true">＋</span>
              <div>
                <h3>No evidence yet</h3>
                <p>Upload files to see processing results and success rates here.</p>
              </div>
              <Link className="dashboard-list-link" to="/evidence">Go to Evidence <span aria-hidden="true">→</span></Link>
            </section>
          ) : (
            <div className="dashboard-outcomes">
              <OutcomePanel
                title="Successfully processed"
                files={ready}
                emptyMessage="No files have finished processing successfully yet."
                tone="success"
              />
              <OutcomePanel
                title="Failed processing"
                files={failed}
                emptyMessage="No failed files. Processing is healthy so far."
                tone="failure"
              />
            </div>
          )}

          <p className="dashboard-note">The success rate uses completed file statuses only. It is not a measure of AI answer correctness.</p>
        </>
      )}
    </section>
  );
}
