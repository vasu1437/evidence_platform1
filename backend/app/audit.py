"""Hash-chained audit log: each entry stores the hash of the previous one, so edits are detectable."""
import hashlib, json, threading
from datetime import datetime, timezone
from . import db

_lock = threading.Lock()


def _hash(prev, actor, action, detail, at):
    return hashlib.sha256((prev + json.dumps([actor, action, detail, at])).encode()).hexdigest()


def log(action, detail="", user_id=None, actor="owner"):
    with _lock:
        if user_id is None:
            raise ValueError("Audit entries require an owner.")
        last = db.q("SELECT hash FROM audit WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
        prev = last[0]["hash"] if last else "GENESIS"
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.run("INSERT INTO audit(user_id,actor,action,detail,prev_hash,hash,at) VALUES(?,?,?,?,?,?,?)",
               (user_id, actor, action, detail, prev, _hash(prev, actor, action, detail, at), at))


def verify(user_id):
    prev = "GENESIS"
    for r in db.q("SELECT * FROM audit WHERE user_id=? ORDER BY id", (user_id,)):
        if r["prev_hash"] != prev or _hash(prev, r["actor"], r["action"], r["detail"], r["at"]) != r["hash"]:
            return {"ok": False, "broken_at": r["id"]}
        prev = r["hash"]
    return {"ok": True, "broken_at": None}
