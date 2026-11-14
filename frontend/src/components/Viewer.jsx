import { useEffect, useRef, useState } from "react";
import { api, mmss } from "../api.js";
import { Kind, Seal } from "./Badge.jsx";

// Side panel that opens the original evidence at the cited spot.
export default function Viewer({ target, onClose }) {
  const [file, setFile] = useState(null);
  const [chunks, setChunks] = useState([]);
  const [check, setCheck] = useState(null);
  const [editingChunkId, setEditingChunkId] = useState(null);
  const [draftText, setDraftText] = useState("");
  const [savingChunkId, setSavingChunkId] = useState(null);
  const [correctionError, setCorrectionError] = useState("");
  const [correctionNotice, setCorrectionNotice] = useState("");
  const media = useRef(null);

  useEffect(() => {
    setCheck(null);
    setEditingChunkId(null);
    setCorrectionError("");
    setCorrectionNotice("");
    api.file(target.fileId).then(setFile);
    api.chunks(target.fileId).then(setChunks);
  }, [target.fileId]);

  useEffect(() => {
    const m = media.current;
    if (!m || target.start == null) return;
    const seek = () => { m.currentTime = target.start; };
    if (m.readyState > 0) seek(); else m.addEventListener("loadedmetadata", seek, { once: true });
  }, [file, target.start]);

  const isHit = (c) =>
    c.id === target.chunkId ||
    (target.start != null && c.start_sec != null && target.start >= c.start_sec && target.start <= (c.end_sec ?? c.start_sec)) ||
    (target.page != null && c.page === target.page);

  const seek = (c) => { if (media.current && c.start_sec != null) { media.current.currentTime = c.start_sec; media.current.play(); } };
  const verify = async () => { setCheck(await api.verify(file.id)); setFile(await api.file(file.id)); };
  const editChunk = (chunk, event) => {
    event.stopPropagation();
    setEditingChunkId(chunk.id);
    setDraftText(chunk.effective_text ?? chunk.text);
    setCorrectionError("");
  };
  const saveChunkCorrection = async (chunk, text) => {
    if (savingChunkId !== null) return;
    setSavingChunkId(chunk.id);
    setCorrectionError("");
    try {
      const updated = await api.correctChunk(file.id, chunk.id, text);
      setChunks((current) => current.map((item) => item.id === chunk.id ? { ...item, ...updated } : item));
      setEditingChunkId(null);
      setCorrectionNotice("Correction saved. Search and answers now use the corrected text.");
    } catch (error) {
      setCorrectionError(error.message);
    } finally {
      setSavingChunkId(null);
    }
  };

  if (!file) return <aside className="panel"><p className="sub">Loading…</p></aside>;
  const url = api.originalUrl(file.id);

  return (
    <aside className="panel" aria-label="Evidence viewer">
      <div className="vh">
        <div><div className="nm">{file.name}</div><div className="mt">{file.note}</div></div>
        <button className="btn" onClick={onClose} aria-label="Close viewer">Close</button>
      </div>
      <div className="vb">
        <div className="vrow"><Kind type={file.type} /><Seal file={file} />
          <button className="btn" onClick={verify}>Verify</button></div>
        {check && <p className={"flag " + (check.ok ? "okbox" : "")}>{check.ok ? "The file matches the original fingerprint." : "This file no longer matches its original fingerprint."}</p>}

        {file.type === "audio" && <audio ref={media} src={url} controls preload="metadata" style={{ width: "100%" }} />}
        {file.type === "video" && <video ref={media} src={url} controls preload="metadata" style={{ width: "100%", borderRadius: 6 }} />}
        {file.type === "image" && <img src={url} alt={file.name} style={{ width: "100%", borderRadius: 6 }} />}
        {file.type === "pdf" && <iframe title={file.name} src={`${url}#page=${target.page || 1}`} style={{ width: "100%", height: 360, border: "1px solid var(--line)", borderRadius: 6 }} />}

        <h3 className="vt">{file.type === "audio" || file.type === "video" ? "Transcript and on-screen text" : "Text read from this file"}</h3>
        <p className="transcript-note">Correct machine-read text if needed. Corrections are stored separately and used in search and answers; the original file and machine text remain unchanged. Timeline and graph analysis are not regenerated automatically.</p>
        {correctionNotice && <p className="transcript-notice" role="status">{correctionNotice}</p>}
        <div className="segs">
          {chunks.length === 0 && <p className="mt">Nothing was read from this file yet.</p>}
          {chunks.map((c) => (
            <div key={c.id} ref={(el) => isHit(c) && el && el.scrollIntoView({ block: "nearest" })}
                 className={"seg" + (isHit(c) ? " hit" : "")} onClick={() => seek(c)}>
              <span className="seg-time">{c.start_sec != null ? mmss(c.start_sec) : c.page ? `p.${c.page}` : c.msg_no ? `#${c.msg_no}` : ""}</span>
              {editingChunkId === c.id ? (
                <div className="seg-editor" onClick={(event) => event.stopPropagation()}>
                  <label>
                    Corrected text
                    <textarea rows={4} maxLength={10000} value={draftText} onChange={(event) => setDraftText(event.target.value)} />
                  </label>
                  <details className="machine-text-details">
                    <summary>Show original machine text</summary>
                    <p>{c.text}</p>
                  </details>
                  {correctionError && <p className="transcript-error" role="alert">{correctionError}</p>}
                  <div className="seg-editor-actions">
                    <button className="btn pri" disabled={savingChunkId === c.id || !draftText.trim()}
                      onClick={() => saveChunkCorrection(c, draftText)}>{savingChunkId === c.id ? "Saving…" : "Save correction"}</button>
                    <button className="btn" disabled={savingChunkId === c.id}
                      onClick={() => setEditingChunkId(null)}>Cancel</button>
                    {c.corrected_text != null && <button className="btn" disabled={savingChunkId === c.id}
                      onClick={() => saveChunkCorrection(c, null)}>Restore machine text</button>}
                  </div>
                </div>
              ) : (
                <>
                  <span className="seg-text">{c.effective_text ?? c.text}</span>
                  <div className="seg-actions">
                    {c.corrected_text != null && <span className="correction-badge">Human corrected</span>}
                    <button className="btn seg-edit" onClick={(event) => editChunk(c, event)}>
                      {c.corrected_text != null ? "Edit correction" : "Correct text"}
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
        <a className="btn pri" href={url} target="_blank" rel="noreferrer">Open full file</a>
      </div>
    </aside>
  );
}
