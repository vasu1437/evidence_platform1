"""Vector index (Chroma, local embeddings) + keyword index (BM25) + hybrid retrieval."""
import re
import chromadb
from rank_bm25 import BM25Plus
from . import db
from .config import CHROMA

_client = None


def collection():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(CHROMA))
    return _client.get_or_create_collection("chunks", metadata={"hnsw:space": "cosine"})


def add(chunk_rows, user_id):
    if chunk_rows:
        collection().add(ids=[str(c["id"]) for c in chunk_rows],
                         documents=[c["corrected_text"] if c.get("corrected_text") is not None else c["text"] for c in chunk_rows],
                         metadatas=[{"file_id": c["file_id"], "user_id": user_id} for c in chunk_rows])


def upsert(chunk_rows, user_id):
    if chunk_rows:
        collection().upsert(ids=[str(c["id"]) for c in chunk_rows], documents=[c["text"] for c in chunk_rows],
                            metadatas=[{"file_id": c["file_id"], "user_id": user_id} for c in chunk_rows])


def remove(chunk_ids, user_id):
    if chunk_ids:
        collection().delete(ids=[str(chunk_id) for chunk_id in chunk_ids], where={"user_id": user_id})


def assign_user(chunk_rows, user_id):
    if not chunk_rows:
        return
    for offset in range(0, len(chunk_rows), 500):
        batch = chunk_rows[offset:offset + 500]
        rows_by_id = {str(row["id"]): row for row in batch}
        existing_ids = collection().get(ids=list(rows_by_id), include=["metadatas"])["ids"]
        if existing_ids:
            collection().update(
                ids=existing_ids,
                metadatas=[{"file_id": rows_by_id[chunk_id]["file_id"], "user_id": user_id} for chunk_id in existing_ids],
            )


def tok(s):
    return re.findall(r"\w+", s.lower())


def retrieve(question, user_id, k=8):
    """Returns (top chunks, best vector distance, keyword overlap 0..1)."""
    rows = db.q("""SELECT c.*, COALESCE(c.corrected_text,c.text) AS effective_text,
                   f.name AS file_name, f.type AS file_type
                   FROM chunks c JOIN files f ON f.id=c.file_id
                   WHERE f.status='ready' AND f.user_id=?""", (user_id,))
    if not rows:
        return [], None, 0
    for row in rows:
        row["text"] = row["effective_text"]
    by_id = {r["id"]: r for r in rows}
    bm = BM25Plus([tok(r["text"]) for r in rows])
    scores = bm.get_scores(tok(question))
    kw_rank = [rows[i]["id"] for i in sorted(range(len(rows)), key=lambda i: -scores[i])[:k * 2]]
    res = collection().query(query_texts=[question], n_results=min(k * 2, len(rows)), where={"user_id": user_id})
    vec_rank = [int(x) for x in res["ids"][0] if int(x) in by_id]
    best_dist = min(res["distances"][0]) if res["distances"][0] else None
    fused = {}
    for rank_list in (kw_rank, vec_rank):  # reciprocal rank fusion
        for pos, cid in enumerate(rank_list):
            fused[cid] = fused.get(cid, 0) + 1 / (60 + pos)
    top = [by_id[c] for c in sorted(fused, key=fused.get, reverse=True)[:k]]
    qt = {t for t in tok(question) if len(t) > 3}
    overlap = max((len(qt & set(tok(r["text"]))) / len(qt) for r in top), default=0) if qt else 0
    return top, best_dist, overlap
