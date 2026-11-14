"""Turn each file type into text chunks with a citation label (ref) and timestamps/pages."""
import difflib, io, re, shutil, subprocess
from pathlib import Path
from .config import (WORK, WHISPER_MODEL, FRAME_EVERY_SEC, MAX_FRAMES)

EXT = {
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "video": {".mp4", ".mov", ".mkv", ".webm", ".avi"},
    "chat": {".txt", ".md", ".csv"},
}


def file_type(name):
    ext = Path(name).suffix.lower()
    for t, exts in EXT.items():
        if ext in exts:
            return t
    return None


def mmss(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _split(text, size=900, overlap=100):
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    return RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap).split_text(text)


def ocr(img):
    """Returns (text, average confidence 0-100). Needs the Tesseract program installed."""
    import pytesseract
    from PIL import ImageOps
    img = ImageOps.autocontrast(img.convert("L"))
    if img.width < 1000:
        img = img.resize((img.width * 2, img.height * 2))
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = [(w, float(c)) for w, c in zip(d["text"], d["conf"]) if w.strip() and float(c) >= 0]
    text = " ".join(w for w, _ in words)
    conf = sum(c for _, c in words) / len(words) if words else 0
    return text, round(conf)


# ---------- PDF ----------
def pdf(path, name):
    import fitz, pymupdf4llm
    from PIL import Image
    pages = pymupdf4llm.to_markdown(str(path), page_chunks=True)
    doc, chunks, ocr_pages = fitz.open(str(path)), [], 0
    for i, p in enumerate(pages, 1):
        text = p["text"].strip()
        if len(text) < 20:  # scanned page: fall back to OCR
            pix = doc[i - 1].get_pixmap(dpi=200)
            text, _ = ocr(Image.open(io.BytesIO(pix.tobytes("png"))))
            ocr_pages += 1
        for part in _split(text):
            chunks.append(dict(text=part, page=i, ref=f"{name} p.{i}"))
    return chunks, {"pages": len(pages), "ocr_pages": ocr_pages, "note": f"{len(pages)} pages"}


# ---------- Image ----------
def image(path, name):
    from PIL import Image
    text, conf = ocr(Image.open(path))
    chunks = [dict(text=t, ref=name) for t in _split(text)] if text.strip() else []
    return chunks, {"ocr_confidence": conf, "note": f"Text read with {conf}% confidence"}


# ---------- Chat (WhatsApp style; plain text falls back to a normal split) ----------
LINE = re.compile(r"^\[?(\d{1,2})[/.](\d{1,2})[/.](\d{2,4}),?\s+(\d{1,2}:\d{2})(?::\d{2})?\s*([APap][Mm])?\]?\s*[-\u2013]?\s*([^:]{1,60}?):\s(.*)$")


def chat(path, name):
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    msgs = []
    for ln in lines:
        m = LINE.match(ln.strip("\u200e\ufeff "))
        if m:
            d, mo, y, tm, ap, who, body = m.groups()  # dates read as DD/MM (assumption, recorded in meta)
            y = int(y) + (2000 if int(y) < 100 else 0)
            msgs.append([f"{y:04d}-{int(mo):02d}-{int(d):02d} {tm}{(' ' + ap) if ap else ''}", who.strip(), body])
        elif msgs and ln.strip():
            msgs[-1][2] += " " + ln.strip()
    if len(msgs) < 3:
        return [dict(text=t, ref=name) for t in _split("\n".join(lines))], {"note": "Plain text"}
    chunks = []
    for i in range(0, len(msgs), 5):
        grp = msgs[i:i + 5]
        text = "\n".join(f"[{t}] {w}: {b}" for t, w, b in grp)
        chunks.append(dict(text=text, msg_no=i + 1, speaker=grp[0][1], ref=f"{name} #{i + 1}-{i + len(grp)}"))
    return chunks, {"messages": len(msgs), "date_format": "DD/MM assumed", "note": f"{len(msgs)} messages"}


# ---------- Audio / video ----------
_model = None


def _whisper():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def transcribe(path, name, label="Speech", progress_callback=None):
    segs, info = _whisper().transcribe(str(path), vad_filter=True)
    chunks, cur = [], None
    duration = getattr(info, "duration", None)
    for s in segs:
        t = s.text.strip()
        if not t:
            continue
        if cur and s.end - cur["start_sec"] < 25:  # merge short segments into ~25 second chunks
            cur["text"] += " " + t
            cur["end_sec"] = s.end
        else:
            cur = dict(text=t, start_sec=s.start, end_sec=s.end)
            chunks.append(cur)
        if progress_callback and duration:
            progress_callback(10 + int(35 * min(s.end / duration, 1)))
    for c in chunks:
        c["ref"] = f"{name} @ {mmss(c['start_sec'])}"
        c["speaker"] = label
    if progress_callback:
        progress_callback(45)
    return chunks, info.language


def audio(path, name, progress_callback=None):
    if progress_callback:
        progress_callback(10)
        chunks, lang = transcribe(path, name, progress_callback=progress_callback)
        progress_callback(55)
    else:
        chunks, lang = transcribe(path, name)
    dur = mmss(chunks[-1]["end_sec"]) if chunks else "0:00"
    return chunks, {"language": lang, "note": f"{dur} · language: {lang}"}


def video(path, name, user_id, progress_callback=None):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed. Install it and restart the server.")
    work = WORK / str(user_id) / f"video_{Path(path).stem}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        if progress_callback:
            progress_callback(10)
        wav = work / "audio.wav"
        chunks, lang = [], None
        r = subprocess.run(["ffmpeg", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                           capture_output=True)
        if progress_callback:
            progress_callback(20)
        if r.returncode == 0 and wav.exists() and wav.stat().st_size > 1000:  # silent videos have no audio track
            if progress_callback:
                chunks, lang = transcribe(wav, name, progress_callback=progress_callback)
            else:
                chunks, lang = transcribe(wav, name)
        subprocess.run(["ffmpeg", "-y", "-i", str(path), "-vf", f"fps=1/{FRAME_EVERY_SEC},scale=960:-1",
                        "-frames:v", str(MAX_FRAMES), str(work / "f_%04d.jpg")], capture_output=True)
        if progress_callback:
            progress_callback(48)
        from PIL import Image
        last, frames_read = "", 0
        frames = sorted(work.glob("f_*.jpg"))
        for i, fp in enumerate(frames):
            with Image.open(fp) as frame:
                text, _ = ocr(frame)
            if len(text) < 8 or difflib.SequenceMatcher(None, last, text).ratio() > 0.9:  # skip empty and near-identical frames
                continue
            last, frames_read = text, frames_read + 1
            sec = i * FRAME_EVERY_SEC
            chunks.append(dict(text=f"[on-screen text] {text}", start_sec=sec, end_sec=sec + FRAME_EVERY_SEC,
                               speaker="On screen", ref=f"{name} @ {mmss(sec)}"))
            if progress_callback and frames:
                progress_callback(48 + int(7 * (i + 1) / len(frames)))
        chunks.sort(key=lambda c: c["start_sec"])
        if progress_callback:
            progress_callback(55)
        return chunks, {"language": lang, "frames_read": frames_read, "note": f"Speech and {frames_read} on-screen frames read"}
    finally:
        if work.exists():
            shutil.rmtree(work)


def extract(path, ftype, name, user_id, progress_callback=None):
    if progress_callback:
        progress_callback(10)
    if ftype == "video":
        return video(path, name, user_id, progress_callback=progress_callback)
    if ftype == "audio":
        return audio(path, name, progress_callback=progress_callback)
    result = {"pdf": pdf, "image": image, "chat": chat}[ftype](path, name)
    if progress_callback:
        progress_callback(55)
    return result
