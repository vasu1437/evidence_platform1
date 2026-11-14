import hashlib, itertools, json, logging, mimetypes, os, re, secrets, shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import audit, auth, db, extract, index, pipeline, rag
from .config import CORS_ORIGINS, ENABLE_DEMO_TAMPER, MAX_UPLOAD_BYTES, ORIGINALS, WORK

app = FastAPI(title="Evidence Intelligence Platform")
logger = logging.getLogger(__name__)
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
db.init()


def _origin_allowed(origin):
    allowed = {item.strip().rstrip("/") for item in CORS_ORIGINS}
    allowed.update({"http://localhost:5173", "http://127.0.0.1:5173"})
    return origin.rstrip("/") in allowed


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    path_parts = path.strip("/").split("/")
    is_shared_timeline = (
        len(path_parts) == 4
        and path_parts[0:2] == ["api", "shared"]
        and bool(path_parts[2])
        and path_parts[3] == "timeline"
    )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and not _origin_allowed(origin):
            return JSONResponse(status_code=403, content={"detail": "Request origin is not allowed."})

    if path == "/api/health" or path in {
        "/api/auth/login",
        "/api/auth/register",
        "/api/auth/session",
        "/api/auth/logout",
    } or is_shared_timeline:
        return await call_next(request)

    if path.startswith("/api/") or path in {"/docs", "/redoc", "/openapi.json"}:
        try:
            settings = auth.get_settings()
        except RuntimeError as error:
            return JSONResponse(status_code=503, content={"detail": str(error)})
        user = auth.session_user(
            request.cookies.get(auth.COOKIE_NAME),
            settings.session_secret,
        )
        if not user:
            return JSONResponse(status_code=401, content={"detail": "Authentication required."})
        account = db.q("SELECT id,username FROM users WHERE id=? AND username=? COLLATE NOCASE", (user["id"], user["username"]))
        if not account:
            return JSONResponse(status_code=401, content={"detail": "Authentication required."})
        request.state.user_id = account[0]["id"]
    return await call_next(request)


@app.on_event("startup")
def initialize_authentication_state():
    settings = auth.get_settings()
    user_id = auth.initialize_legacy_account(settings)
    if user_id is None:
        return
    legacy_chunks = db.q(
        "SELECT c.id,c.file_id FROM chunks c JOIN files f ON f.id=c.file_id WHERE f.user_id=?",
        (user_id,),
    )
    index.assign_user(legacy_chunks, user_id)


def current_user_id(request: Request):
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, "Authentication required.")
    return user_id


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class ChunkCorrection(BaseModel):
    text: str | None = Field(default=None, max_length=10000)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def public(f):
    meta = json.loads(f.get("meta") or "{}")
    return {"id": f["id"], "name": f["name"], "type": f["type"], "sha256": f["sha256"], "size": f["size"], "uploaded_at": f["uploaded_at"],
            "status": f["status"], "progress": 100 if f["status"] == "ready" else int(f.get("progress") or 0),
            "integrity": f["integrity"], "error": f["error"], "note": meta.get("note", ""), "meta": meta}


def get_file(file_id, user_id):
    r = db.q("SELECT * FROM files WHERE id=? AND user_id=?", (file_id, user_id))
    if not r:
        raise HTTPException(404, "File not found")
    return r[0]


async def _save_upload(upload, path, max_bytes):
    upload_size = getattr(upload, "size", None)
    if max_bytes is not None and upload_size is not None and upload_size > max_bytes:
        return None

    size = 0
    with open(path, "wb") as out:
        while True:
            block = await upload.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            if max_bytes is not None and size > max_bytes:
                return None
            out.write(block)
    return size


def _validated_original_path(file, user_id):
    stored_path = Path(file["path"])
    originals_root = Path(ORIGINALS).resolve()
    try:
        original_path = stored_path.resolve()
        relative_path = original_path.relative_to(originals_root)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(500, "Stored file path is not inside the originals directory.")
    if len(relative_path.parts) > 1 and relative_path.parts[0] != str(user_id):
        raise HTTPException(500, "Stored file path is not inside this account's originals directory.")
    if original_path == originals_root or stored_path.is_symlink():
        raise HTTPException(500, "Stored file path is not a regular original.")
    return original_path


def _delete_file_flags(conn, file_id, user_id):
    flags = conn.execute("SELECT id,sources FROM flags WHERE user_id=?", (user_id,)).fetchall()
    related_flag_ids = []
    for flag in flags:
        sources = json.loads(flag["sources"] or "[]")
        if not isinstance(sources, list):
            raise ValueError(f"Flag {flag['id']} has invalid source data.")
        if any(isinstance(source, dict) and str(source.get("file_id")) == str(file_id) for source in sources):
            related_flag_ids.append(flag["id"])
    if related_flag_ids:
        conn.executemany("DELETE FROM flags WHERE id=? AND user_id=?", [(flag_id, user_id) for flag_id in related_flag_ids])


@app.get("/api/health")
def health():
    return {"ok": True, "demo_tamper": ENABLE_DEMO_TAMPER}


@app.post("/api/auth/login")
def login(payload: LoginPayload, response: Response):
    settings = auth.get_settings()
    rows = db.q("SELECT id,username,password_hash FROM users WHERE username=? COLLATE NOCASE", (payload.username.strip(),))
    if not rows or not auth.verify_password(payload.password, rows[0]["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    user = rows[0]
    token = auth.create_session(user["id"], user["username"], settings.session_secret)
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"authenticated": True, "user_id": user["id"], "username": user["username"]}


@app.post("/api/auth/register")
def register(payload: LoginPayload, response: Response):
    settings = auth.get_settings()
    try:
        user = auth.register_user(payload.username, payload.password)
    except auth.UsernameTakenError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except auth.RegistrationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    token = auth.create_session(user["id"], user["username"], settings.session_secret)
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"authenticated": True, "user_id": user["id"], "username": user["username"]}


@app.get("/api/auth/session")
def session(request: Request):
    settings = auth.get_settings()
    user = auth.session_user(
        request.cookies.get(auth.COOKIE_NAME),
        settings.session_secret,
    )
    if not user:
        return {"authenticated": False, "user_id": None, "username": None}
    rows = db.q("SELECT id,username FROM users WHERE id=? AND username=? COLLATE NOCASE", (user["id"], user["username"]))
    if not rows:
        return {"authenticated": False, "user_id": None, "username": None}
    return {"authenticated": True, "user_id": rows[0]["id"], "username": rows[0]["username"]}


@app.post("/api/auth/logout")
def logout(response: Response):
    settings = auth.get_settings()
    response.delete_cookie(
        key=auth.COOKIE_NAME,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return {"ok": True}


# ---------- files ----------
@app.post("/api/files")
async def upload(bg: BackgroundTasks, user_id: int = Depends(current_user_id), files: list[UploadFile] = File(...)):
    results = []
    originals_dir = ORIGINALS / str(user_id)
    originals_dir.mkdir(parents=True, exist_ok=True)
    for up in files:
        filename = Path(up.filename or "").name
        ftype = extract.file_type(filename)
        if not ftype:
            results.append({"name": filename or "Unnamed file", "error": "Unsupported file type. Use PDF, image, audio, video, or a text chat export."})
            continue
        tmp = originals_dir / f".tmp_{secrets.token_hex(6)}"
        max_bytes = MAX_UPLOAD_BYTES.get(ftype)
        size = await _save_upload(up, tmp, max_bytes)
        if size is None:
            if tmp.exists():
                tmp.unlink()
            if max_bytes is None:
                raise RuntimeError("Upload size checking failed without a configured limit.")
            max_mb = max_bytes // (1024 * 1024)
            results.append({"name": filename, "error": f"Maximum {ftype} file size is {max_mb} MB."})
            continue
        digest = sha256(tmp)
        dup = db.q("SELECT * FROM files WHERE sha256=? AND user_id=?", (digest, user_id))
        if dup:  # same bytes already uploaded: link instead of re-processing
            tmp.unlink()
            db.run("INSERT INTO flags(user_id,kind,note,sources) VALUES(?,?,?,?)",
                   (user_id, "duplicate", f"{up.filename} is identical to {dup[0]['name']}", json.dumps([{"file_id": dup[0]["id"], "file_name": dup[0]["name"], "type": dup[0]["type"], "ref": dup[0]["name"]}])))
            audit.log("Duplicate upload skipped", up.filename, user_id)
            results.append({**public(dup[0]), "duplicate": True})
            continue
        fid = db.run("INSERT INTO files(user_id,name,type,path,sha256,size,uploaded_at) VALUES(?,?,?,?,?,?,?)",
                     (user_id, filename, ftype, "", digest, size, now()))
        final = originals_dir / f"{fid}_{filename}"
        tmp.rename(final)
        os.chmod(final, 0o444)  # originals are read-only
        db.run("UPDATE files SET path=? WHERE id=?", (str(final), fid))
        audit.log("Uploaded file", f"{filename} sha256={digest[:16]}", user_id)
        bg.add_task(pipeline.process, fid)
        results.append(public(get_file(fid, user_id)))
    return results


@app.post("/api/files/{file_id}/replace")
async def replace_file(file_id: int, bg: BackgroundTasks, file: UploadFile = File(...),
                       user_id: int = Depends(current_user_id)):
    current = get_file(file_id, user_id)
    if current["status"] in {"queued", "reading", "analysing"}:
        raise HTTPException(409, "Wait until file processing finishes before replacing it.")

    filename = Path(file.filename or "").name
    ftype = extract.file_type(filename)
    if not ftype:
        raise HTTPException(415, "Unsupported file type. Use PDF, image, audio, video, or a text chat export.")

    originals_dir = ORIGINALS / str(user_id)
    originals_dir.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(8)
    tmp_path = originals_dir / f".tmp_replace_{file_id}_{token}"
    backup_path = None
    new_path = None
    vectors_removed = False
    chunk_rows = []
    try:
        max_bytes = MAX_UPLOAD_BYTES.get(ftype)
        size = await _save_upload(file, tmp_path, max_bytes)
        if size is None:
            if max_bytes is None:
                raise RuntimeError("Upload size checking failed without a configured limit.")
            max_mb = max_bytes // (1024 * 1024)
            raise HTTPException(413, f"Maximum {ftype} file size is {max_mb} MB.")
        digest = sha256(tmp_path)
        if digest == current["sha256"]:
            return {**public(current), "unchanged": True}

        duplicates = db.q("SELECT id,name FROM files WHERE sha256=? AND user_id=? AND id<>?",
                          (digest, user_id, file_id))
        if duplicates:
            raise HTTPException(409, f"This file is already in this case as {duplicates[0]['name']}.")

        original_path = _validated_original_path(current, user_id)
        chunk_rows = db.q("SELECT id,file_id,text,corrected_text FROM chunks WHERE file_id=?", (file_id,))
        if original_path.exists():
            backup_path = original_path.with_name(f".replace-old-{file_id}-{token}")
        new_path = originals_dir / f"{file_id}_{token}_{filename}"

        try:
            if backup_path:
                original_path.replace(backup_path)
            tmp_path.replace(new_path)
            os.chmod(new_path, 0o444)
            if chunk_rows:
                index.remove([row["id"] for row in chunk_rows], user_id)
                vectors_removed = True

            with db.tx() as conn:
                latest = conn.execute("SELECT status FROM files WHERE id=? AND user_id=?",
                                      (file_id, user_id)).fetchone()
                if not latest:
                    raise HTTPException(404, "File not found")
                if latest["status"] in {"queued", "reading", "analysing"}:
                    raise HTTPException(409, "Wait until file processing finishes before replacing it.")

                _delete_file_flags(conn, file_id, user_id)
                conn.execute("DELETE FROM entities WHERE file_id=?", (file_id,))
                conn.execute("DELETE FROM events WHERE file_id=?", (file_id,))
                conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
                conn.execute(
                    """UPDATE files SET name=?,type=?,path=?,sha256=?,size=?,uploaded_at=?,
                       status='queued',progress=0,integrity='saved',error=NULL,meta='{}'
                       WHERE id=? AND user_id=?""",
                    (filename, ftype, str(new_path), digest, size, now(), file_id, user_id),
                )
                conn.execute("DELETE FROM llm_cache WHERE user_id=?", (user_id,))
        except Exception as error:
            rollback_errors = []
            if vectors_removed:
                try:
                    index.add(chunk_rows, user_id)
                except Exception as rollback_error:
                    rollback_errors.append(f"Could not restore search index: {rollback_error}")
            if new_path and new_path.exists():
                try:
                    os.chmod(new_path, 0o666)
                    new_path.unlink()
                except OSError as rollback_error:
                    rollback_errors.append(f"Could not remove replacement original: {rollback_error}")
            if backup_path and backup_path.exists():
                try:
                    backup_path.replace(original_path)
                    os.chmod(original_path, 0o444)
                except OSError as rollback_error:
                    rollback_errors.append(f"Could not restore original file: {rollback_error}")
            if rollback_errors:
                logger.exception("Could not replace file_id=%s for user_id=%s: %s",
                                 file_id, user_id, "; ".join(rollback_errors))
                raise HTTPException(500, "Could not replace file. Please contact support.") from error
            if isinstance(error, HTTPException):
                raise
            logger.exception("Could not replace file_id=%s for user_id=%s", file_id, user_id)
            raise HTTPException(500, "Could not replace file. Please try again or contact support.") from error
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    cleanup_warning = None
    if backup_path and backup_path.exists():
        try:
            os.chmod(backup_path, 0o666)
            backup_path.unlink()
        except OSError as error:
            logger.exception("Replacement committed but old original cleanup failed for file_id=%s user_id=%s",
                             file_id, user_id)
            cleanup_warning = "The new file was saved, but the previous original could not be removed."
    audit.log("Replaced file", f"{current['name']} sha256={current['sha256'][:16]} -> {filename} sha256={digest[:16]}", user_id)
    bg.add_task(pipeline.process, file_id)
    result = public(get_file(file_id, user_id))
    if cleanup_warning:
        result["warning"] = cleanup_warning
    return result


@app.get("/api/files")
def list_files(user_id: int = Depends(current_user_id)):
    return [public(f) for f in db.q("SELECT * FROM files WHERE user_id=? ORDER BY id DESC", (user_id,))]


@app.get("/api/files/{file_id}")
def one_file(file_id: int, user_id: int = Depends(current_user_id)):
    return public(get_file(file_id, user_id))


@app.get("/api/files/{file_id}/chunks")
def chunks(file_id: int, user_id: int = Depends(current_user_id)):
    get_file(file_id, user_id)
    rows = db.q(
        """SELECT id,text,corrected_text,page,msg_no,start_sec,end_sec,speaker,ref,
           COALESCE(corrected_text,text) AS effective_text
           FROM chunks WHERE file_id=? ORDER BY id""",
        (file_id,),
    )
    return rows


@app.put("/api/files/{file_id}/chunks/{chunk_id}")
def correct_chunk(file_id: int, chunk_id: int, body: ChunkCorrection,
                  user_id: int = Depends(current_user_id)):
    file = get_file(file_id, user_id)
    if file["status"] != "ready":
        raise HTTPException(409, "Wait until file processing finishes before correcting its transcript.")
    rows = db.q("SELECT id,text,corrected_text FROM chunks WHERE id=? AND file_id=?", (chunk_id, file_id))
    if not rows:
        raise HTTPException(404, "Transcript segment not found")
    chunk = rows[0]
    corrected = body.text.strip() if body.text is not None else None
    if body.text is not None and not corrected:
        raise HTTPException(422, "Corrected text cannot be empty. Use null to restore the machine transcript.")

    unchanged = corrected == chunk["corrected_text"]
    if not unchanged:
        db.run("UPDATE chunks SET corrected_text=? WHERE id=? AND file_id=?", (corrected, chunk_id, file_id))
    try:
        audit.log(
            "Corrected extracted text",
            f"{file['name']} chunk {chunk_id}: {'restored machine transcript' if corrected is None else 'saved human correction'}",
            user_id,
        )
    except Exception as error:
        logger.exception("Transcript correction saved but audit logging failed for chunk_id=%s user_id=%s",
                         chunk_id, user_id)
        raise HTTPException(500, "The correction was saved, but its audit record could not be saved.") from error

    effective = corrected if corrected is not None else chunk["text"]
    try:
        index.upsert([{"id": chunk_id, "file_id": file_id, "text": effective}], user_id)
    except Exception as error:
        logger.exception("Transcript correction saved but search indexing failed for chunk_id=%s user_id=%s",
                         chunk_id, user_id)
        raise HTTPException(503, "The correction was saved, but search indexing failed. Retry saving this correction.") from error

    return {
        "id": chunk_id, "text": chunk["text"], "corrected_text": corrected,
        "effective_text": effective, "unchanged": unchanged,
    }


@app.post("/api/files/{file_id}/retry")
def retry_file(file_id: int, bg: BackgroundTasks, user_id: int = Depends(current_user_id)):
    file = get_file(file_id, user_id)
    if file["status"] != "failed":
        raise HTTPException(409, "Only failed files can be processed again.")
    original_path = _validated_original_path(file, user_id)
    if not original_path.is_file():
        raise HTTPException(409, "The stored original is unavailable, so it cannot be processed again.")
    if sha256(original_path) != file["sha256"]:
        db.run("UPDATE files SET integrity='altered' WHERE id=? AND user_id=?", (file_id, user_id))
        audit.log("Retry blocked", f"{file['name']}: stored original hash mismatch", user_id)
        raise HTTPException(409, "The stored original has changed. Verify the file before processing it again.")

    chunk_rows = db.q("SELECT id,file_id,text,corrected_text FROM chunks WHERE file_id=?", (file_id,))
    audit.log("Retried failed processing", file["name"], user_id)
    vectors_removed = False
    try:
        if chunk_rows:
            index.remove([row["id"] for row in chunk_rows], user_id)
            vectors_removed = True
        with db.tx() as conn:
            current = conn.execute("SELECT status FROM files WHERE id=? AND user_id=?", (file_id, user_id)).fetchone()
            if not current:
                raise HTTPException(404, "File not found")
            if current["status"] != "failed":
                raise HTTPException(409, "Only failed files can be processed again.")
            _delete_file_flags(conn, file_id, user_id)
            conn.execute("DELETE FROM entities WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM events WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
            conn.execute(
                "UPDATE files SET status='queued',progress=0,integrity='saved',error=NULL,meta='{}' WHERE id=? AND user_id=?",
                (file_id, user_id),
            )
    except Exception as error:
        if vectors_removed:
            try:
                index.add(chunk_rows, user_id)
            except Exception:
                logger.exception("Could not restore search index after retry preparation failed for file_id=%s user_id=%s",
                                 file_id, user_id)
        logger.exception("Could not prepare failed file for retry file_id=%s user_id=%s", file_id, user_id)
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(500, "Could not safely prepare the file for processing again.") from error

    bg.add_task(pipeline.process, file_id)
    return public(get_file(file_id, user_id))


@app.get("/api/files/{file_id}/original")
def original(file_id: int, user_id: int = Depends(current_user_id)):
    f = get_file(file_id, user_id)
    return FileResponse(f["path"], media_type=mimetypes.guess_type(f["name"])[0] or "application/octet-stream")  # supports Range requests


@app.delete("/api/files/{file_id}")
def delete_file(file_id: int, user_id: int = Depends(current_user_id)):
    f = get_file(file_id, user_id)
    if f["status"] in {"queued", "reading", "analysing"}:
        raise HTTPException(409, "Wait until file processing finishes before removing it.")

    original_path = _validated_original_path(f, user_id)
    work_path = WORK / str(user_id) / f"video_{original_path.stem}" if f["type"] == "video" else None

    chunk_rows = db.q("SELECT id,file_id,text,corrected_text FROM chunks WHERE file_id=?", (file_id,))
    staged_path = None
    vectors_removed = False
    try:
        if original_path.exists():
            staged_path = original_path.with_name(f".delete-{file_id}-{secrets.token_hex(8)}")
            original_path.replace(staged_path)
        if chunk_rows:
            index.remove([row["id"] for row in chunk_rows], user_id)
            vectors_removed = True

        with db.tx() as conn:
            current = conn.execute("SELECT status FROM files WHERE id=? AND user_id=?", (file_id, user_id)).fetchone()
            if not current:
                raise HTTPException(404, "File not found")
            if current["status"] in {"queued", "reading", "analysing"}:
                raise HTTPException(409, "Wait until file processing finishes before removing it.")

            _delete_file_flags(conn, file_id, user_id)
            conn.execute("DELETE FROM entities WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM events WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
            conn.execute("DELETE FROM files WHERE id=? AND user_id=?", (file_id, user_id))
            conn.execute("DELETE FROM llm_cache WHERE user_id=?", (user_id,))
    except Exception as error:
        rollback_errors = []
        if vectors_removed:
            try:
                index.add(chunk_rows, user_id)
            except Exception as rollback_error:
                rollback_errors.append(f"Could not restore search index: {rollback_error}")
        if staged_path and staged_path.exists():
            try:
                staged_path.replace(original_path)
            except OSError as rollback_error:
                rollback_errors.append(f"Could not restore original file: {rollback_error}")
        if rollback_errors:
            logger.exception("Could not remove file_id=%s for user_id=%s: %s", file_id, user_id, "; ".join(rollback_errors))
            raise HTTPException(500, "Could not remove file. Please try again or contact support.") from error
        if isinstance(error, HTTPException):
            raise
        logger.exception("Could not remove file_id=%s for user_id=%s", file_id, user_id)
        raise HTTPException(500, "Could not remove file. Please try again or contact support.") from error

    work_cleanup_error = None
    if work_path and work_path.exists():
        try:
            shutil.rmtree(work_path)
        except OSError as error:
            logger.exception("File record was removed, but video work cleanup failed for file_id=%s user_id=%s",
                             file_id, user_id)
            work_cleanup_error = error
    original_cleanup_error = None
    if staged_path and staged_path.exists():
        try:
            os.chmod(staged_path, 0o666)
            staged_path.unlink()
        except OSError as error:
            logger.exception("File record was removed, but original cleanup failed for file_id=%s user_id=%s", file_id, user_id)
            original_cleanup_error = error
    if original_cleanup_error:
        raise HTTPException(
            500,
            "The file was removed from this case, but its original file could not be deleted. Contact support if this persists.",
        ) from original_cleanup_error
    if work_cleanup_error:
        raise HTTPException(
            500,
            "The file was removed from this case, but its temporary processing files could not be deleted. Contact support if this persists.",
        ) from work_cleanup_error
    try:
        audit.log("Deleted file", f"{f['name']} sha256={f['sha256'][:16]}", user_id)
    except Exception as error:
        logger.exception("File was removed, but audit logging failed for file_id=%s user_id=%s", file_id, user_id)
        raise HTTPException(
            500,
            "The file was removed from this case, but its audit record could not be saved. Contact support if this persists.",
        ) from error
    return {"ok": True, "id": file_id, "name": f["name"]}


@app.post("/api/files/{file_id}/verify")
def verify(file_id: int, user_id: int = Depends(current_user_id)):
    f = get_file(file_id, user_id)
    actual = sha256(f["path"])
    ok = actual == f["sha256"]
    db.run("UPDATE files SET integrity=? WHERE id=?", ("intact" if ok else "altered", file_id))
    audit.log("Verified file", f"{f['name']}: {'matches original' if ok else 'MISMATCH'}", user_id)
    return {"ok": ok, "expected": f["sha256"], "actual": actual, "checked_at": now()}


@app.post("/api/files/{file_id}/tamper-demo")
def tamper(file_id: int, user_id: int = Depends(current_user_id)):
    """Demo only: changes the stored copy so Verify fails. Disable with ENABLE_DEMO_TAMPER=false."""
    if not ENABLE_DEMO_TAMPER:
        raise HTTPException(403, "Disabled")
    f = get_file(file_id, user_id)
    os.chmod(f["path"], 0o644)
    with open(f["path"], "ab") as fh:
        fh.write(b"\nEDITED")
    os.chmod(f["path"], 0o444)
    audit.log("Stored copy changed (demo)", f["name"], user_id)
    return {"ok": True}


# ---------- timeline, flags, graph ----------
def build_timeline(user_id):
    evs = db.q("SELECT e.*, f.name AS file_name FROM events e JOIN files f ON f.id=e.file_id WHERE f.user_id=? ORDER BY e.date IS NULL, e.date, e.id", (user_id,))
    events = [{"id": e["id"], "date": e["date"], "description": e["description"], "sources": pipeline.event_sources(json.loads(e["chunk_ids"]))} for e in evs]
    flags = [{"id": f["id"], "kind": f["kind"], "note": f["note"], "sources": json.loads(f["sources"])} for f in db.q("SELECT * FROM flags WHERE user_id=? ORDER BY id DESC", (user_id,))]
    return {"events": events, "flags": flags}


@app.get("/api/timeline")
def timeline(user_id: int = Depends(current_user_id)):
    return build_timeline(user_id)


def _merge_roots(entities, merges):
    parents = {key: key for key in entities}

    def find(key):
        if parents[key] != key:
            parents[key] = find(parents[key])
        return parents[key]

    for merge in merges:
        source, target = merge["source_key"], merge["target_key"]
        if source not in parents or target not in parents:
            continue
        if entities[source]["kind"] != entities[target]["kind"]:
            continue
        source_root, target_root = find(source), find(target)
        if source_root != target_root:
            parents[source_root] = target_root
    return {key: find(key) for key in parents}


@app.get("/api/graph")
def graph(user_id: int = Depends(current_user_id)):
    files = db.q("SELECT id,name,type,status,size,uploaded_at FROM files WHERE user_id=? ORDER BY id DESC", (user_id,))
    file_by_id = {f["id"]: f for f in files}
    chunks_by_file = {}
    chunks_by_id = {}
    for chunk in db.q(
        """SELECT c.id,c.file_id,COALESCE(c.corrected_text,c.text) AS text,
           c.page,c.msg_no,c.start_sec,c.end_sec,c.ref
           FROM chunks c JOIN files f ON f.id=c.file_id WHERE f.user_id=? ORDER BY c.id""",
        (user_id,),
    ):
        chunks_by_file.setdefault(chunk["file_id"], []).append(chunk)
        chunks_by_id[chunk["id"]] = chunk

    flags_by_file = {}
    for flag in db.q("SELECT id,kind,note,sources FROM flags WHERE user_id=? ORDER BY id", (user_id,)):
        for source in json.loads(flag["sources"] or "[]"):
            file_id = source.get("file_id")
            if file_id in file_by_id:
                flags_by_file.setdefault(file_id, []).append({
                    "id": flag["id"], "kind": flag["kind"], "note": flag["note"],
                })

    entities = {}
    entity_sources = {}
    for row in db.q("SELECT e.file_id,e.name,e.kind FROM entities e JOIN files f ON f.id=e.file_id WHERE f.user_id=? ORDER BY e.id", (user_id,)):
        file_id = row["file_id"]
        if file_id not in file_by_id or file_by_id[file_id]["status"] != "ready":
            continue
        label = str(row["name"] or "").strip()
        if not label:
            continue
        kind = str(row["kind"] or "person").strip().lower()
        scope = "" if kind in {"phone", "email", "account"} else f":file:{file_id}"
        entity_id = f"entity:{kind}:{label.casefold()}{scope}"
        entity = entities.setdefault(entity_id, {
            "id": entity_id, "label": label, "kind": kind, "node_type": "entity",
            "files": set(), "candidate_files": set(), "labels": set(),
        })
        entity["candidate_files"].add(file_id)
        entity["labels"].add(label)
        needle = re.compile(rf"(?<!\w){re.escape(label)}(?!\w)", re.IGNORECASE)
        sources = [
            {
                "chunk_id": chunk["id"], "file_id": file_id, "file_name": file_by_id[file_id]["name"],
                "ref": chunk["ref"], "page": chunk["page"], "msg_no": chunk["msg_no"],
                "start_sec": chunk["start_sec"], "end_sec": chunk["end_sec"],
            }
            for chunk in chunks_by_file.get(file_id, [])
            if needle.search(str(chunk["text"] or ""))
        ]
        if sources:
            entity["files"].add(file_id)
            entity_sources.setdefault(entity_id, {}).setdefault(file_id, {}).update(
                (source["chunk_id"], source) for source in sources
            )

    merge_rows = db.q("SELECT source_key,target_key FROM entity_merges WHERE user_id=? AND active=1 ORDER BY id", (user_id,))
    roots = _merge_roots(entities, merge_rows)
    merged_entities = {}
    by_file = {}
    for entity_id, entity in entities.items():
        root = roots[entity_id]
        canonical = entities.get(root, entity)
        merged = merged_entities.setdefault(root, {
            "id": root, "label": canonical["label"], "kind": canonical["kind"],
            "node_type": "entity", "files": set(), "candidate_files": set(),
            "labels": set(), "sources_by_file": {},
        })
        merged["files"].update(entity["files"])
        merged["candidate_files"].update(entity["candidate_files"])
        merged["labels"].update(entity["labels"])
        for file_id, sources in entity_sources.get(entity_id, {}).items():
            merged["sources_by_file"].setdefault(file_id, {}).update(sources)
            by_file.setdefault(file_id, set()).add(root)

    nodes = [{
        "id": f"file:{f['id']}", "label": f["name"], "kind": "document", "node_type": "document",
        "file_id": f["id"], "file_type": f["type"], "status": f["status"], "size": f["size"],
        "uploaded_at": f["uploaded_at"], "files": [f["id"]],
        "flags": flags_by_file.get(f["id"], []),
    } for f in files]
    links = []
    for entity in merged_entities.values():
        file_ids = sorted(entity["files"])
        aliases = sorted(label for label in entity["labels"] if label != entity["label"])
        source_refs = []
        for file_id in file_ids:
            source_refs.extend(entity["sources_by_file"].get(file_id, {}).values())
        nodes.append({
            "id": entity["id"], "label": entity["label"], "kind": entity["kind"],
            "node_type": "entity", "verified": bool(file_ids),
            "files": file_ids,
            "file_names": [file_by_id[fid]["name"] for fid in file_ids],
            "source_count": len(file_ids),
            "unmatched_file_ids": sorted(entity["candidate_files"] - entity["files"]),
            "aliases": aliases,
            "sources": source_refs,
        })
        for file_id in file_ids:
            refs = list(entity["sources_by_file"].get(file_id, {}).values())
            links.append({
                "a": entity["id"], "b": f"file:{file_id}", "type": "document_entity",
                "count": len(refs), "label": "exact text found in source", "file_id": file_id,
                "file_name": file_by_id[file_id]["name"], "sources": refs,
            })

    cooccurrences = Counter()
    cooccurrence_sources = {}
    for file_id, entity_ids in by_file.items():
        for a, b in itertools.combinations(sorted(entity_ids), 2):
            key = (a, b)
            cooccurrences[key] += 1
            cooccurrence_sources.setdefault(key, []).append(file_id)
    for (a, b), count in cooccurrences.items():
        file_ids = cooccurrence_sources[(a, b)]
        source_refs = []
        for file_id in file_ids:
            source_refs.extend(merged_entities[a]["sources_by_file"].get(file_id, {}).values())
            source_refs.extend(merged_entities[b]["sources_by_file"].get(file_id, {}).values())
        links.append({
            "a": a, "b": b, "type": "co_occurs", "count": count,
            "label": f"co-appears in {count} evidence item{'s' if count != 1 else ''}",
            "file_ids": file_ids,
            "file_names": [file_by_id[fid]["name"] for fid in file_ids],
            "sources": source_refs,
        })

    events = []
    for row in db.q("SELECT e.*,f.name AS file_name FROM events e JOIN files f ON f.id=e.file_id WHERE f.user_id=? ORDER BY e.id", (user_id,)):
        file_id = row["file_id"]
        source_refs = []
        for chunk_id in json.loads(row["chunk_ids"] or "[]"):
            chunk = chunks_by_id.get(chunk_id)
            if chunk and chunk["file_id"] == file_id:
                source_refs.append({
                    "chunk_id": chunk_id, "file_id": file_id, "file_name": row["file_name"],
                    "type": file_by_id[file_id]["type"], "ref": chunk["ref"], "start_sec": chunk["start_sec"],
                    "page": chunk["page"], "msg_no": chunk["msg_no"],
                })
        event_id = f"event:{row['id']}"
        event_node = {
            "id": event_id, "label": row["description"], "kind": "event", "node_type": "event",
            "date": row["date"], "file_id": file_id, "file_name": row["file_name"],
            "sources": source_refs, "verified": bool(source_refs),
        }
        nodes.append(event_node)
        events.append(event_node)
        if source_refs:
            links.append({
                "a": event_id, "b": f"file:{file_id}", "type": "document_event",
                "count": len(source_refs), "label": "supported by source text",
                "file_id": file_id, "file_name": row["file_name"], "sources": source_refs,
            })

    return {
        "nodes": nodes,
        "links": links,
        "stats": {
            "documents": len(files),
            "entities": len(merged_entities),
            "events": len(events),
            "flagged_documents": sum(1 for f in files if flags_by_file.get(f["id"])),
            "unverified_entities": sum(1 for entity in merged_entities.values() if not entity["files"]),
            "document_links": sum(1 for link in links if link["type"] == "document_entity"),
            "co_occurrences": sum(1 for link in links if link["type"] == "co_occurs"),
        },
    }


class GraphPathRequest(BaseModel):
    source_id: str
    target_id: str


@app.post("/api/graph/path")
def graph_path(body: GraphPathRequest, user_id: int = Depends(current_user_id)):
    data = graph(user_id)
    nodes = {node["id"]: node for node in data["nodes"]}
    source, target = nodes.get(body.source_id), nodes.get(body.target_id)
    if not source or not target:
        raise HTTPException(404, "Entity not found")
    if source["node_type"] != "entity" or target["node_type"] != "entity":
        raise HTTPException(422, "Connection paths can only be searched between entities")
    if source["kind"] not in {"person", "place"} or target["kind"] not in {"person", "place"}:
        raise HTTPException(422, "Connection paths are limited to people and places")
    if not source["verified"] or not target["verified"]:
        return {"connected": False, "nodes": [], "links": [], "context_events": [], "message": "An entity has no exact text match in its source evidence."}
    if source["id"] == target["id"]:
        return {"connected": True, "nodes": [source], "links": [], "context_events": [], "message": "Both selections resolve to the same exact-match entity node."}

    adjacency = {}
    graph_links = [
        link for link in data["links"]
        if link["type"] == "co_occurs"
        and nodes[link["a"]]["kind"] != nodes[link["b"]]["kind"]
        and nodes[link["a"]]["kind"] in {"person", "place"}
        and nodes[link["b"]]["kind"] in {"person", "place"}
    ]
    for link in graph_links:
        adjacency.setdefault(link["a"], []).append((link["b"], link))
        adjacency.setdefault(link["b"], []).append((link["a"], link))
    queue = [source["id"]]
    previous = {source["id"]: None}
    for current in queue:
        if current == target["id"]:
            break
        for neighbor, link in adjacency.get(current, []):
            if neighbor not in previous:
                previous[neighbor] = (current, link)
                queue.append(neighbor)
    if target["id"] not in previous:
        return {"connected": False, "nodes": [], "links": [], "context_events": [], "message": "No exact, source-backed path connects these entities. They remain unlinked."}

    path_ids, path_links = [target["id"]], []
    cursor = target["id"]
    while cursor != source["id"]:
        prior, link = previous[cursor]
        path_links.append(link)
        path_ids.append(prior)
        cursor = prior
    path_ids.reverse()
    path_links.reverse()
    file_ids = {file_id for link in path_links for file_id in link.get("file_ids", [])}
    context_events = [event for event in data["nodes"] if event["node_type"] == "event" and event["verified"] and event["file_id"] in file_ids]
    return {
        "connected": True, "nodes": [nodes[node_id] for node_id in path_ids],
        "links": path_links, "context_events": context_events,
        "message": "The path follows people and places that co-appear in source evidence; this does not prove a real-world relationship.",
    }


class EntityMergeRequest(BaseModel):
    source_id: str
    target_id: str


@app.get("/api/entities/merges")
def entity_merge_history(user_id: int = Depends(current_user_id)):
    return db.q("SELECT id,source_key,target_key,source_label,target_label,kind,active,created_at,undone_at FROM entity_merges WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,))


@app.post("/api/entities/merges")
def merge_entities(body: EntityMergeRequest, user_id: int = Depends(current_user_id)):
    if body.source_id == body.target_id:
        raise HTTPException(422, "Choose two different entities")
    data = graph(user_id)
    entities_by_id = {node["id"]: node for node in data["nodes"] if node["node_type"] == "entity"}
    source, target = entities_by_id.get(body.source_id), entities_by_id.get(body.target_id)
    if not source or not target:
        raise HTTPException(404, "Entity not found")
    if source["kind"] != target["kind"]:
        raise HTTPException(422, "Entities of different types cannot be merged")
    if not source["verified"] or not target["verified"]:
        raise HTTPException(422, "Only entities with exact source-text matches can be merged")
    merge_rows = db.q("SELECT source_key,target_key FROM entity_merges WHERE user_id=? AND active=1 ORDER BY id", (user_id,))
    roots = _merge_roots(entities_by_id, merge_rows)
    if roots[body.source_id] == roots[body.target_id]:
        raise HTTPException(409, "These entities are already in the same reviewed group")
    merge_id = db.run(
        "INSERT INTO entity_merges(user_id,source_key,target_key,source_label,target_label,kind,active,created_at) VALUES(?,?,?,?,?,?,1,?)",
        (user_id, body.source_id, body.target_id, source["label"], target["label"], source["kind"], now()),
    )
    audit.log("Merged entity identities", f"{source['label']} into {target['label']} ({source['kind']})", user_id)
    return {"id": merge_id, "source_id": body.source_id, "target_id": body.target_id, "active": True}


@app.post("/api/entities/merges/{merge_id}/undo")
def undo_entity_merge(merge_id: int, user_id: int = Depends(current_user_id)):
    rows = db.q("SELECT * FROM entity_merges WHERE id=? AND user_id=?", (merge_id, user_id))
    if not rows:
        raise HTTPException(404, "Merge record not found")
    merge = rows[0]
    if not merge["active"]:
        raise HTTPException(409, "This merge has already been undone")
    db.run("UPDATE entity_merges SET active=0,undone_at=? WHERE id=? AND user_id=?", (now(), merge_id, user_id))
    audit.log("Undid entity identity merge", f"{merge['source_label']} separate from {merge['target_label']} ({merge['kind']})", user_id)
    return {"id": merge_id, "active": False}


# ---------- ask / summary ----------
class Ask(BaseModel):
    question: str


@app.post("/api/ask")
def ask(body: Ask, user_id: int = Depends(current_user_id)):
    result = rag.ask(body.question, user_id)
    audit.log("Asked a question", body.question[:120], user_id)
    return result


@app.get("/api/summary")
def summary(user_id: int = Depends(current_user_id)):
    return rag.summary(user_id)


# ---------- audit & sharing ----------
@app.get("/api/audit")
def audit_list(user_id: int = Depends(current_user_id)):
    return db.q("SELECT id,actor,action,detail,hash,at FROM audit WHERE user_id=? ORDER BY id DESC LIMIT 200", (user_id,))


@app.post("/api/audit/verify")
def audit_verify(user_id: int = Depends(current_user_id)):
    return audit.verify(user_id)


@app.post("/api/share")
def share(user_id: int = Depends(current_user_id)):
    token = secrets.token_urlsafe(16)
    db.run("INSERT INTO shares(token,user_id,expires_at) VALUES(?,?,?)",
           (token, user_id, (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()))
    audit.log("Created share link", token[:6] + "...", user_id)
    return {"token": token, "expires_in_days": 7}


@app.get("/api/shared/{token}/timeline")
def shared_timeline(token: str):
    r = db.q("SELECT * FROM shares WHERE token=?", (token,))
    if not r or datetime.fromisoformat(r[0]["expires_at"]) < datetime.now(timezone.utc):
        raise HTTPException(404, "This link is invalid or has expired.")
    user_id = r[0]["user_id"]
    audit.log("Share link opened", token[:6] + "...", user_id, actor="viewer")
    return build_timeline(user_id)


if (FRONTEND_DIST / "index.html").is_file():
    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not found")
        root = FRONTEND_DIST.resolve()
        requested = (root / path).resolve()
        try:
            requested.relative_to(root)
        except ValueError:
            raise HTTPException(404, "Not found")
        if requested.is_file():
            return FileResponse(requested)
        return FileResponse(root / "index.html")
