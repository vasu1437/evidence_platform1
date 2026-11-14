// All backend calls live here. Change BASE if your API runs somewhere else.
const BASE = "/api";

async function json(res, endpoint) {
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    if (res.status === 401 && !endpoint.startsWith("/auth/")) {
      window.dispatchEvent(new Event("casefile-auth-expired"));
    }
    throw new Error(msg);
  }
  return res.json();
}
const get = (p) => fetch(BASE + p, { credentials: "same-origin" }).then((res) => json(res, p));
const post = (p, body) =>
  fetch(BASE + p, { method: "POST", credentials: "same-origin", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined }).then((res) => json(res, p));
const put = (p, body) =>
  fetch(BASE + p, { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((res) => json(res, p));
const del = (p) => fetch(BASE + p, { method: "DELETE", credentials: "same-origin" }).then((res) => json(res, p));

export const api = {
  authSession: () => get("/auth/session"),
  authLogin: (credentials) => post("/auth/login", credentials),
  authRegister: (credentials) => post("/auth/register", credentials),
  authLogout: () => post("/auth/logout"),
  files: () => get("/files"),
  file: (id) => get(`/files/${id}`),
  chunks: (id) => get(`/files/${id}/chunks`),
  originalUrl: (id) => `${BASE}/files/${id}/original`,
  upload: (list, onProgress) => new Promise((resolve, reject) => {
    const form = new FormData();
    [...list].forEach((f) => form.append("files", f));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", BASE + "/files");
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onload = () => {
      let payload;
      try {
        payload = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error("The server returned an invalid upload response."));
        return;
      }
      if (xhr.status === 401) {
        window.dispatchEvent(new Event("casefile-auth-expired"));
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(payload.detail || xhr.statusText || "Upload failed."));
        return;
      }
      resolve(payload);
    };
    xhr.onerror = () => reject(new Error("Network error while uploading the file."));
    xhr.onabort = () => reject(new Error("File upload was cancelled."));
    xhr.send(form);
  }),
  replaceFile: (id, file) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/files/${id}/replace`, { method: "POST", credentials: "same-origin", body: form })
      .then((res) => json(res, `/files/${id}/replace`));
  },
  retryFile: (id) => post(`/files/${id}/retry`),
  correctChunk: (fileId, chunkId, text) => put(`/files/${fileId}/chunks/${chunkId}`, { text }),
  verify: (id) => post(`/files/${id}/verify`),
  tamper: (id) => post(`/files/${id}/tamper-demo`),
  deleteFile: (id) => del(`/files/${id}`),
  timeline: () => get("/timeline"),
  graph: () => get("/graph"),
  graphPath: (body) => post("/graph/path", body),
  entityMerges: () => get("/entities/merges"),
  mergeEntities: (body) => post("/entities/merges", body),
  undoEntityMerge: (id) => post(`/entities/merges/${id}/undo`),
  ask: (question) => post("/ask", { question }),
  summary: () => get("/summary"),
  audit: () => get("/audit"),
  auditVerify: () => post("/audit/verify"),
  share: () => post("/share"),
};

export const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
export const TYPE_LABEL = { chat: "Chat", image: "Image", pdf: "PDF", audio: "Audio", video: "Video" };

// Turns a source from the API into the target the viewer understands.
export const toTarget = (s) => ({ fileId: s.file_id, chunkId: s.chunk_id, start: s.start_sec, page: s.page, msg: s.msg_no });
