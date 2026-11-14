import sqlite3
from contextlib import contextmanager
from .config import DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
  password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id), name TEXT, type TEXT, path TEXT, sha256 TEXT, size INTEGER,
  uploaded_at TEXT, status TEXT DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0,
  integrity TEXT DEFAULT 'saved', error TEXT, meta TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER, text TEXT, page INTEGER, msg_no INTEGER,
  start_sec REAL, end_sec REAL, speaker TEXT, ref TEXT, corrected_text TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER, date TEXT, description TEXT, chunk_ids TEXT);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, file_id INTEGER, name TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS entity_merges(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id), source_key TEXT NOT NULL, target_key TEXT NOT NULL,
  source_label TEXT NOT NULL, target_label TEXT NOT NULL, kind TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, undone_at TEXT);
CREATE TABLE IF NOT EXISTS flags(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id), kind TEXT, note TEXT, sources TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id), actor TEXT, action TEXT, detail TEXT, prev_hash TEXT, hash TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS shares(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires_at TEXT);
CREATE TABLE IF NOT EXISTS llm_cache(
  user_id INTEGER REFERENCES users(id), key TEXT NOT NULL, value TEXT,
  PRIMARY KEY(user_id,key));
"""


@contextmanager
def tx():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with tx() as c:
        c.executescript(SCHEMA)
        for table in ("files", "entity_merges", "flags", "audit", "shares"):
            columns = {row["name"] for row in c.execute(f"PRAGMA table_info({table})")}
            if "user_id" not in columns:
                c.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES users(id)")

        file_columns = {row["name"] for row in c.execute("PRAGMA table_info(files)")}
        if "progress" not in file_columns:
            c.execute("ALTER TABLE files ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")

        chunk_columns = {row["name"] for row in c.execute("PRAGMA table_info(chunks)")}
        if "corrected_text" not in chunk_columns:
            c.execute("ALTER TABLE chunks ADD COLUMN corrected_text TEXT")

        cache_columns = {row["name"] for row in c.execute("PRAGMA table_info(llm_cache)")}
        if "user_id" not in cache_columns:
            c.execute("ALTER TABLE llm_cache RENAME TO llm_cache_legacy")
            c.execute("""CREATE TABLE llm_cache(
                user_id INTEGER REFERENCES users(id), key TEXT NOT NULL, value TEXT,
                PRIMARY KEY(user_id,key))""")
            c.execute("INSERT INTO llm_cache(user_id,key,value) SELECT NULL,key,value FROM llm_cache_legacy")
            c.execute("DROP TABLE llm_cache_legacy")

        c.executescript("""
            CREATE INDEX IF NOT EXISTS files_user_id ON files(user_id);
            CREATE INDEX IF NOT EXISTS flags_user_id ON flags(user_id);
            CREATE INDEX IF NOT EXISTS audit_user_id ON audit(user_id);
            CREATE INDEX IF NOT EXISTS shares_user_id ON shares(user_id);
            CREATE INDEX IF NOT EXISTS entity_merges_user_id ON entity_merges(user_id);
        """)


def q(sql, args=()):
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def run(sql, args=()):
    with tx() as c:
        return c.execute(sql, args).lastrowid
