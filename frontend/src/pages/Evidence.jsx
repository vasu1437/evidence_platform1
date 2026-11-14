import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { Kind, Seal } from "../components/Badge.jsx";

const STATUS = { queued: "Waiting to process", reading: "Extracting content…", analysing: "Analyzing evidence…", ready: "", failed: "Could not process" };
const FILE_LIMITS_MB = { image: 20, audio: 50, video: 120 };
const MEDIA_EXTENSIONS = {
  image: [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"],
  audio: [".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"],
  video: [".mp4", ".mov", ".mkv", ".webm", ".avi"],
};

function mediaType(fileName) {
  const extension = `.${fileName.split(".").pop().toLowerCase()}`;
  return Object.keys(MEDIA_EXTENSIONS).find((type) => MEDIA_EXTENSIONS[type].includes(extension));
}

function conversationFileName(title) {
  const safeTitle = (title.trim() || "Pasted conversation")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "")
    .replace(/\.txt$/i, "")
    .slice(0, 120);
  return `${safeTitle || "Pasted conversation"}.txt`;
}

export default function Evidence({ open }) {
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState([]);
  const [toast, setToast] = useState(null);
  const [demo, setDemo] = useState(false);
  const [removingId, setRemovingId] = useState(null);
  const [retryingId, setRetryingId] = useState(null);
  const [replacingId, setReplacingId] = useState(null);
  const [conversationName, setConversationName] = useState("");
  const [conversationText, setConversationText] = useState("");
  const [savingConversation, setSavingConversation] = useState(false);
  const input = useRef(null);
  const replaceInput = useRef(null);
  const replaceTarget = useRef(null);
  const toastSequence = useRef(0);
  const uploadSequence = useRef(0);

  const notify = (text, tone = "info") => setToast({ id: ++toastSequence.current, text, tone });
  const load = (announceError = false) => api.files().then(setFiles).catch((error) => {
    if (announceError) notify(error.message, "error");
  });
  useEffect(() => {
    if (!toast) return undefined;
    const timer = setTimeout(() => setToast(null), toast.tone === "error" ? 6500 : 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => { load(true); fetch("/api/health").then((r) => r.json()).then((h) => setDemo(h.demo_tamper)).catch(() => {}); }, []);
  useEffect(() => {  // poll while anything is still processing
    if (!files.some((f) => ["queued", "reading", "analysing"].includes(f.status))) return;
    const t = setInterval(() => load(), 1500);
    return () => clearInterval(t);
  }, [files]);

  const upload = async (list, { quiet = false } = {}) => {
    const selected = Array.from(list || []);
    if (!selected.length) return [];
    const results = [];

    for (const file of selected) {
      const type = mediaType(file.name);
      const limitMb = FILE_LIMITS_MB[type];
      if (limitMb && file.size > limitMb * 1024 * 1024) {
        results.push({ name: file.name, error: `Maximum ${type} file size is ${limitMb} MB.` });
        continue;
      }

      const uploadId = ++uploadSequence.current;
      setUploading((current) => [...current, { id: uploadId, name: file.name, progress: 0 }]);
      try {
        const response = await api.upload([file], (progress) => {
          setUploading((current) => current.map((item) => item.id === uploadId ? { ...item, progress } : item));
        });
        const result = response?.[0];
        if (result) {
          results.push(result);
          if (result.id) {
            setFiles((current) => [result, ...current.filter((item) => item.id !== result.id)]);
          }
        } else {
          results.push({ name: file.name, error: "The server did not return an upload result." });
        }
      } catch (error) {
        results.push({ name: file.name, error: error.message });
      } finally {
        setUploading((current) => current.filter((item) => item.id !== uploadId));
      }
    }

    const problems = results.filter((result) => result.error);
    const duplicates = results.filter((result) => result.duplicate);
    const added = results.filter((result) => result.id && !result.duplicate);
    if (!quiet) {
      if (problems.length) notify(problems.map((result) => `${result.name}: ${result.error}`).join(" "), "error");
      else if (added.length && duplicates.length) notify(`${added.length} file(s) added; ${duplicates.map((result) => result.name).join(", ")} already existed.`);
      else if (added.length) notify(`${added.length} file(s) uploaded. Processing started.`, "success");
      else if (duplicates.length) notify(`${duplicates.map((result) => result.name).join(", ")} already existed. No duplicate was added.`);
    }
    load();
    return results;
  };
  const saveConversation = async (event) => {
    event.preventDefault();
    const text = conversationText;
    if (!text.trim() || savingConversation) return;

    setSavingConversation(true);
    try {
      const name = conversationFileName(conversationName);
      const file = new File([text], name, { type: "text/plain" });
      const result = await upload([file], { quiet: true });
      if (result?.length && result.every((item) => !item.error)) {
        setConversationName("");
        setConversationText("");
        notify(result.some((item) => item.duplicate)
          ? `${name} is already in this case; no duplicate was added.`
          : `${name} was saved. Processing started.`, result.some((item) => item.duplicate) ? "info" : "success");
      } else if (result?.length) {
        notify(result.find((item) => item.error)?.error || "Could not save this conversation.", "error");
      }
    } catch (error) {
      notify(`Could not save the conversation. ${error.message}`, "error");
    } finally {
      setSavingConversation(false);
    }
  };
  const verify = async (f) => {
    try {
      await api.verify(f.id);
      notify(`${f.name} fingerprint verified.`, "success");
      load();
    } catch (error) {
      notify(`Could not verify ${f.name}. ${error.message}`, "error");
    }
  };
  const retry = async (f) => {
    setRetryingId(f.id);
    try {
      const result = await api.retryFile(f.id);
      setFiles((current) => current.map((file) => file.id === f.id ? result : file));
      notify(`${f.name} is being processed again.`, "success");
    } catch (error) {
      notify(`Could not retry ${f.name}. ${error.message}`, "error");
    } finally {
      setRetryingId(null);
    }
  };
  const tamper = async (f) => {
    try {
      await api.tamper(f.id);
      notify(`Stored copy of ${f.name} was changed for the demo. Press Verify to see it flagged.`);
    } catch (error) {
      notify(`Could not simulate an edit. ${error.message}`, "error");
    }
  };
  const remove = async (f) => {
    if (!window.confirm(`Permanently remove "${f.name}" and its extracted evidence from this case?`)) return;
    setRemovingId(f.id);
    try {
      await api.deleteFile(f.id);
      setFiles((current) => current.filter((file) => file.id !== f.id));
      notify(`${f.name} was removed from this case.`, "success");
    } catch (e) {
      notify(e.message.startsWith("The file was removed from this case,")
        ? `${f.name}: ${e.message}`
        : `Could not remove ${f.name}. ${e.message}`, "error");
      load();
    } finally {
      setRemovingId(null);
    }
  };
  const replace = async (target, replacement) => {
    if (!window.confirm(`Replace "${target.name}" with "${replacement.name}"? The previous original and its extracted evidence will be permanently removed.`)) return;
    setReplacingId(target.id);
    try {
      const result = await api.replaceFile(target.id, replacement);
      if (result.unchanged) {
        notify(`${target.name} already contains these exact contents; no changes were made.`);
      } else {
        setFiles((current) => current.map((file) => file.id === target.id ? result : file));
        notify(result.warning || `${target.name} was replaced with ${result.name} and is being reprocessed.`, "success");
      }
      load();
    } catch (error) {
      notify(`Could not replace ${target.name}. ${error.message}`, "error");
    } finally {
      setReplacingId(null);
    }
  };
  const altered = files.filter((f) => f.integrity === "altered");

  return (
    <>
      <h2>Evidence</h2>
      <p className="sub">Originals are stored read-only. Replacing a file permanently removes its previous original and extracted evidence, then processes the new file.</p>
      <div className="drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); upload(e.dataTransfer.files); }}>
        Drop chats, screenshots, PDFs, audio, or video here, or{" "}
        <button className="link" onClick={() => input.current.click()}>choose files</button>
        <input ref={input} type="file" multiple hidden onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
        <input ref={replaceInput} type="file" hidden onChange={(e) => {
          const replacement = e.target.files?.[0];
          const target = replaceTarget.current;
          e.target.value = "";
          replaceTarget.current = null;
          if (target && replacement) replace(target, replacement);
        }} />
      </div>
      <p className="upload-limits">Per file: images up to 20 MB · audio up to 50 MB · video up to 120 MB. Video duration has no cap.</p>
      {uploading.map((item) => (
        <div className="upload-progress-card" key={item.id}>
          <div className="upload-progress-heading">
            <span><b>UPLOADING</b><strong>{item.name}</strong></span>
            <b>{item.progress}%</b>
          </div>
          <div className="progress-track" role="progressbar" aria-label={`Uploading ${item.name}`}
            aria-valuemin="0" aria-valuemax="100" aria-valuenow={item.progress}>
            <span style={{ width: `${item.progress}%` }} />
          </div>
        </div>
      ))}
      <details className="conversation-entry">
        <summary>
          <span className="conversation-entry-mark" aria-hidden="true">✎</span>
          <span className="conversation-entry-copy"><b>Enter a conversation manually</b><small>Paste the full conversation and add it to this case.</small></span>
          <span className="conversation-entry-toggle" aria-hidden="true">＋</span>
        </summary>
        <form className="conversation-entry-form" onSubmit={saveConversation}>
          <label>
            Conversation name <span>(optional)</span>
            <input
              value={conversationName}
              onChange={(event) => setConversationName(event.target.value)}
              maxLength={120}
              placeholder="For example, Interview notes"
            />
          </label>
          <label>
            Entire conversation
            <textarea
              required
              rows={9}
              value={conversationText}
              onChange={(event) => setConversationText(event.target.value)}
              placeholder={"Paste the complete conversation here…\nInclude dates, times, and speaker names when available."}
            />
          </label>
          <p className="conversation-entry-note">This creates a text evidence record from what you paste; it does not verify the original conversation’s authenticity. WhatsApp-style timestamps and speaker names help preserve message structure.</p>
          <div className="conversation-entry-actions">
            <button type="submit" className="btn pri" disabled={savingConversation || !conversationText.trim()}>
              {savingConversation ? "Saving conversation…" : "Save and process"}
            </button>
          </div>
        </form>
      </details>
      {files.length === 0 && <p className="sub">No evidence yet. Drop your first file above.</p>}
      {files.map((f) => (
        <div className="row" key={f.id}>
          <Kind type={f.type} />
          <div>
            <div className="nm">{f.name}</div>
            <div className="mt">{STATUS[f.status] || f.note}{f.status === "failed" ? `: ${f.error}` : ""}{f.status === "ready" ? ` · added ${f.uploaded_at.slice(0, 10)}` : ""}</div>
            {["queued", "reading", "analysing", "ready"].includes(f.status) && (
              <div className="file-progress">
                <div className="file-progress-label">
                  <span>{f.status === "ready" ? "Processing complete" : STATUS[f.status]}</span>
                  <b>{f.status === "ready" ? 100 : f.progress || 0}%</b>
                </div>
                <div className="progress-track" role="progressbar" aria-label={`Processing ${f.name}`}
                  aria-valuemin="0" aria-valuemax="100" aria-valuenow={f.status === "ready" ? 100 : f.progress || 0}>
                  <span style={{ width: `${f.status === "ready" ? 100 : Math.min(100, Math.max(0, Number(f.progress) || 0))}%` }} />
                </div>
              </div>
            )}
          </div>
          <div className="actions">
            <Seal file={f} />
            <button className="btn pri" disabled={f.status !== "ready" || replacingId === f.id} onClick={() => open({ fileId: f.id })}>View</button>
            <a className="btn" href={api.originalUrl(f.id)} download={f.name}>Download</a>
            <button className="btn" disabled={replacingId === f.id || removingId === f.id} onClick={() => verify(f)}>Verify</button>
            {f.status === "failed" && <button className="btn pri" disabled={retryingId !== null || replacingId === f.id || removingId === f.id}
              onClick={() => retry(f)}>{retryingId === f.id ? "Retrying…" : "Retry processing"}</button>}
            <button className="btn" disabled={replacingId !== null || removingId !== null || ["queued", "reading", "analysing"].includes(f.status)}
              title={["queued", "reading", "analysing"].includes(f.status) ? "Wait until processing finishes to replace this file." : "Permanently replace this original and its extracted evidence."}
              onClick={() => { replaceTarget.current = f; replaceInput.current?.click(); }}>
              {replacingId === f.id ? "Replacing…" : "Replace"}
            </button>
            {demo && <button className="btn" title="Demo only" onClick={() => tamper(f)}>Simulate edit</button>}
            <button className="btn danger" disabled={removingId !== null || replacingId === f.id || ["queued", "reading", "analysing"].includes(f.status)}
              title={["queued", "reading", "analysing"].includes(f.status) ? "Wait until processing finishes to remove this file." : "Permanently remove this file and its extracted evidence."}
              onClick={() => remove(f)}>{removingId === f.id ? "Removing…" : "Remove"}</button>
          </div>
        </div>
      ))}
      {altered.map((f) => (
        <div className="flag" key={f.id}><b>{f.name} was changed after upload.</b> Its fingerprint no longer matches the original. Check the audit log before sharing this case.</div>
      ))}
      {toast && (
        <div className={`toast-notification toast-${toast.tone}`} role={toast.tone === "error" ? "alert" : "status"}>
          <span className="toast-icon" aria-hidden="true">{toast.tone === "error" ? "!" : toast.tone === "success" ? "✓" : "i"}</span>
          <p>{toast.text}</p>
          <button type="button" className="toast-close" aria-label="Dismiss notification" onClick={() => setToast(null)}>×</button>
        </div>
      )}
    </>
  );
}
