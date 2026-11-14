# Backend

FastAPI service. See the root README for setup.

    python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    cp .env.example .env                                  # add your free Gemini and Groq keys
    uvicorn app.main:app --reload --port 8000

System programs needed: Tesseract OCR and ffmpeg. API docs: http://localhost:8000/docs
