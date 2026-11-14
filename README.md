# Digital Evidence Intelligence Platform (PS 3.2)

Upload chats, screenshots, PDFs, audio, and video. The app reads everything, builds a dated timeline where every event links to its exact source, answers questions with citations (and refuses when the evidence does not support an answer), and proves nothing was altered using SHA-256 fingerprints and a hash-chained audit log.

```
evidence-platform/
  backend/    FastAPI, SQLite, Chroma, faster-whisper, Tesseract, LiteLLM
  frontend/   React + Vite (plain CSS)
```

## What you need installed

- Python 3.10+ and Node 18+
- **Tesseract OCR** (screenshots, scanned PDFs, video frames): Windows installer from UB Mannheim, `brew install tesseract`, or `sudo apt install tesseract-ocr`
- **ffmpeg** (video and audio): `winget install ffmpeg`, `brew install ffmpeg`, or `sudo apt install ffmpeg`
- Free API keys: Gemini (aistudio.google.com) and Groq (console.groq.com)

## Run it

Backend (terminal 1):

    cd backend
    python -m venv .venv
    .venv\Scripts\Activate.ps1                            # Windows PowerShell
    pip install -r requirements.txt
    Copy-Item .env.example .env                           # only if backend/.env does not already exist
    uvicorn app.main:app --reload --port 8000

Frontend (terminal 2):

    cd frontend
    npm install
    npm run dev                                           # http://localhost:5173

First run downloads the speech model (faster-whisper) and the embedding model (Chroma), so it needs internet once.

## Sign-in and route protection

No initial account or sample evidence is required. Users create an account from the login screen; each new account starts with an empty case and can upload its own evidence. Passwords are stored as PBKDF2 hashes. File records and originals, extracted evidence, vector search, flags, identity reviews, audit history, cached analysis, and share links are scoped to the signed-in user. A share link exposes only its owner's timeline.

The browser session uses an expiring HttpOnly, SameSite cookie. If `AUTH_SESSION_SECRET` is not set, the app generates one and stores it in its database. All `/api` endpoints are protected except health, authentication, and the share-token timeline endpoint. Registration is open to users who can reach the app; usernames must be 1-64 letters, numbers, dots, underscores, @, or hyphens, and passwords must contain at least 12 characters. Accounts currently have no email verification or password-reset flow.

For an older installation, setting both `AUTH_USERNAME` and `AUTH_PASSWORD_HASH` preserves the old owner login and assigns unowned legacy case data to that owner. Leave both unset for registration-only startup and empty new accounts; setting only one is an error. Legacy data is never deleted by this migration.

Keep the app on localhost unless you have configured HTTPS and appropriate `CORS_ORIGINS`; set `AUTH_COOKIE_SECURE=true` when serving it over HTTPS. The backend refuses to start if the initial owner and session settings are missing or invalid.

## How it works

1. **Upload:** the original is saved read-only, hashed (SHA-256), and logged. Identical files are skipped as duplicates.
2. **Extract:** PDF (pymupdf4llm, OCR for scanned pages), image (OCR), chat (WhatsApp-style parser, dates read as DD/MM), audio (Whisper), video (ffmpeg: speech plus on-screen text from sampled frames).
3. **Analyse:** one cached LLM call per batch returns events, dates, and people. Undated events are never guessed.
4. **Index:** chunks go into Chroma (meaning search) and BM25 (keyword search).
5. **Ask:** gate agent, hybrid retrieval, sufficiency check, cited answer, verifier agent.
6. **Flags:** duplicates and contradictions between sources appear on the Timeline.
7. **Evidence graph:** shows verified people and places only. A connection means a person and place appeared in the same evidence item; it does not imply a real-world relationship. Files are available from entity details, not shown as graph nodes or links. Search, type filters, focus mode, source-backed paths, saved views, and CSV/JSON exports are available.
8. **Case report:** print or save a report as PDF with the file register, hash and audit checks, cited events, flags, and evidence-network summary. Source excerpts are included where available.
9. **Dashboard:** view per-account file-processing totals, recent successful and failed files, and the processing success rate (ready files divided by completed files). This rate does not measure AI answer accuracy.

On the Evidence page, **Replace** overwrites a file record after confirmation: its old original and extracted records are removed, the new original is hashed and audited, and it is reprocessed. Keep a separate copy of the old original if you need to preserve it. Replace is disabled while processing. **Remove** permanently deletes a file and its extracted records; related flags, indexed chunks, cached analysis, the stored original, and any leftover per-video processing files are cleared. The audit history is retained.
The Evidence page shows upload and processing progress, supports downloading the original, and enforces per-file limits of 20 MB for images, 50 MB for audio, and 120 MB for video. Failed files can be retried. Video duration is not capped; on-screen text extraction samples up to 60 frames.
In the evidence viewer, machine-read transcript/OCR text can be corrected by a human. Corrections are stored separately from both the original file and machine text, then used for search and answers. The audit log records corrections; timeline and graph analysis are not rerun automatically.
The Evidence page also supports pasting a complete conversation as a new text evidence record. Include timestamps and speaker names when available; pasted content is not verified against an original source.

## Deploy on Render (single Docker service)

The root `Dockerfile` builds the React frontend, installs the Python backend plus FFmpeg and English Tesseract OCR, and serves the frontend and API from one Render web service. The API health check is `/api/health`.

1. Push the outer project folder (the one containing `backend/`, `frontend/`, and this README) to a private GitHub repository. Do not commit `.env` files or `backend/storage/`; `.dockerignore` also excludes them from Docker builds. Existing local case data is not copied into a new deployment.
2. In Render, choose **New → Web Service**, connect the repository, and select the **Docker** runtime with the root `Dockerfile`.
3. Add a persistent disk mounted at `/var/data`, then set `STORAGE_DIR=/var/data`. SQLite, Chroma, uploaded originals, and processing files are stored there. Render's default filesystem is ephemeral; persistent disks require a paid service. Keep this app at one instance while using its local SQLite/Chroma storage.
4. Add these environment variables in Render (never put secret values in Git):
   - `GEMINI_API_KEY` and `GROQ_API_KEY`
   - `AUTH_SESSION_SECRET` (at least 32 characters; keep it stable between deploys)
   - `AUTH_COOKIE_SECURE=true`
   - `CORS_ORIGINS=https://<your-service>.onrender.com` (replace with your actual Render URL)
   - `ENABLE_DEMO_TAMPER=false`
   - `STORAGE_DIR=/var/data`
5. Deploy, open the Render URL, register an account, and test with non-sensitive sample files before uploading real evidence.

Registration is open in this application. Restrict access before using sensitive evidence, and review the configured AI providers' data policies. To generate a session secret locally, run `python -c "import secrets; print(secrets.token_urlsafe(48))"` and paste the result directly into Render's environment settings.

## Demo script

1. Upload a fake case (chat export, screenshot, PDF, voice note, short screen recording).
2. Open Timeline, click a source chip, and watch the evidence open at the right spot.
3. On Evidence, press **Simulate edit** then **Verify** to show tamper detection. Check the Audit log.
4. On Ask, ask a real question (cited answer), then an unrelated one (refused).

## Where to change things

| Want to change | File |
|---|---|
| Models and limits | `backend/.env` |
| What gets extracted from each file type | `backend/app/extract.py` |
| Prompts for events, answers, gate, verifier | `backend/app/pipeline.py`, `backend/app/rag.py` |
| Refusal wording | `REFUSALS` in `backend/app/rag.py` |
| API calls from the UI | `frontend/src/api.js` |
| Colors and layout | `frontend/src/styles.css` |

## Known limits

- One isolated case per registered account. The share endpoints exist (`/api/share`, `/api/shared/{token}/timeline`) but there is no share page in the UI yet.
- Audio is transcribed in its original language; there is no translation step yet.
- Free model names change. If a call fails, update the model names in `.env`.
- This code was written and syntax-checked but not run end to end with real keys, Tesseract, or ffmpeg. Expect to fix small issues on first run.
- Turn off the demo edit button for real use with `ENABLE_DEMO_TAMPER=false`.
