import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).resolve().parent.parent
STORAGE = Path(os.getenv("STORAGE_DIR", BASE / "storage"))
ORIGINALS, WORK, CHROMA = STORAGE / "originals", STORAGE / "work", STORAGE / "chroma"
DB = STORAGE / "app.db"
for p in (ORIGINALS, WORK, CHROMA):
    p.mkdir(parents=True, exist_ok=True)

MAIN_MODEL = os.getenv("MAIN_MODEL", "gemini/gemini-3.5-flash-lite")
SMALL_MODEL = os.getenv("SMALL_MODEL", "groq/openai/gpt-oss-20b")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "groq/openai/gpt-oss-120b")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
FRAME_EVERY_SEC = int(os.getenv("FRAME_EVERY_SEC", "4"))
MAX_FRAMES = int(os.getenv("MAX_FRAMES", "60"))
MAX_UPLOAD_BYTES = {
    "image": 20 * 1024 * 1024,
    "audio": 50 * 1024 * 1024,
    "video": 120 * 1024 * 1024,
}
SUFFICIENCY_MAX_DISTANCE = float(os.getenv("SUFFICIENCY_MAX_DISTANCE", "0.8"))
ENABLE_DEMO_TAMPER = os.getenv("ENABLE_DEMO_TAMPER", "true").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
