"""One place for LLM calls: JSON output, caching (saves tokens), retry, fallback model."""
import json, re, hashlib
import litellm
from . import db
from .config import MAIN_MODEL, FALLBACK_MODEL

litellm.drop_params = True


def _parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except Exception:
        return json.loads(re.search(r"\{.*\}", text, re.S).group(0))


def ask_json(system, user, user_id, model=None):
    model = model or MAIN_MODEL
    key = hashlib.sha256(f"{model}|{system}|{user}".encode()).hexdigest()
    hit = db.q("SELECT value FROM llm_cache WHERE user_id=? AND key=?", (user_id, key))
    if hit:
        return json.loads(hit[0]["value"])
    last = None
    for m in (model, FALLBACK_MODEL):
        try:
            r = litellm.completion(
                model=m, temperature=0, num_retries=2, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            out = _parse(r.choices[0].message.content)
            db.run("INSERT OR REPLACE INTO llm_cache(user_id,key,value) VALUES(?,?,?)", (user_id, key, json.dumps(out)))
            return out
        except Exception as e:  # rate limit, bad JSON, etc: try the fallback model
            last = e
    raise RuntimeError(f"LLM call failed: {last}")
