import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass

COOKIE_NAME = "evidence_session"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 600_000
PASSWORD_PREFIX = "pbkdf2_sha256"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


class RegistrationError(ValueError):
    pass


class UsernameTakenError(RegistrationError):
    pass


@dataclass(frozen=True)
class AuthSettings:
    session_secret: bytes
    cookie_secure: bool
    legacy_username: str | None
    legacy_password_hash: str | None


def _b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value):
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded, altchars=b"-_", validate=True)


def _parse_password_hash(encoded):
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$")
        rounds = int(rounds_text)
        salt = _b64decode(salt_text)
        digest = _b64decode(digest_text)
    except (AttributeError, ValueError, TypeError) as error:
        raise ValueError("Invalid password hash format.") from error
    if algorithm != PASSWORD_PREFIX or not 100_000 <= rounds <= 2_000_000 or len(salt) < 16 or len(digest) != 32:
        raise ValueError("Invalid password hash parameters.")
    return rounds, salt, digest


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"{PASSWORD_PREFIX}${PASSWORD_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password, encoded):
    try:
        rounds, salt, expected = _parse_password_hash(encoded)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def get_settings():
    legacy_username = os.getenv("AUTH_USERNAME", "").strip() or None
    legacy_password_hash = os.getenv("AUTH_PASSWORD_HASH", "").strip() or None
    if bool(legacy_username) != bool(legacy_password_hash):
        raise RuntimeError("Set both AUTH_USERNAME and AUTH_PASSWORD_HASH to migrate a legacy owner, or leave both unset.")
    if legacy_username and not USERNAME_PATTERN.fullmatch(legacy_username):
        raise RuntimeError("AUTH_USERNAME must be 1-64 letters, numbers, dots, underscores, @, or hyphens.")
    if legacy_password_hash:
        try:
            _parse_password_hash(legacy_password_hash)
        except ValueError as error:
            raise RuntimeError("AUTH_PASSWORD_HASH is invalid.") from error

    session_secret = os.getenv("AUTH_SESSION_SECRET", "").strip()
    cookie_secure_text = os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower()
    if session_secret and len(session_secret.encode("utf-8")) < 32:
        raise RuntimeError("AUTH_SESSION_SECRET must contain at least 32 characters.")
    if cookie_secure_text not in {"true", "false", "1", "0", "yes", "no"}:
        raise RuntimeError("AUTH_COOKIE_SECURE must be true or false.")
    if not session_secret:
        from . import db

        with db.tx() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT value FROM app_settings WHERE key='session_secret'").fetchone()
            if row:
                session_secret = row["value"]
            else:
                session_secret = secrets.token_urlsafe(48)
                connection.execute(
                    "INSERT INTO app_settings(key,value) VALUES('session_secret',?)",
                    (session_secret,),
                )
    return AuthSettings(
        session_secret=session_secret.encode("utf-8"),
        cookie_secure=cookie_secure_text in {"true", "1", "yes"},
        legacy_username=legacy_username,
        legacy_password_hash=legacy_password_hash,
    )


def initialize_legacy_account(settings):
    from . import db

    username = settings.legacy_username
    password_hash = settings.legacy_password_hash
    if not username or not password_hash:
        return None

    with db.tx() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id FROM users WHERE username=? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if existing:
            user_id = existing["id"]
        else:
            user_id = connection.execute(
                "INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                (username, password_hash, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            ).lastrowid
        for table in ("files", "flags", "audit", "shares", "entity_merges", "llm_cache"):
            connection.execute(f"UPDATE {table} SET user_id=? WHERE user_id IS NULL", (user_id,))
    return user_id


def register_user(username, password):
    from . import db

    username = username.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise RegistrationError("Use 1-64 letters, numbers, dots, underscores, @, or hyphens for the username.")
    if len(password) < 12:
        raise RegistrationError("The password must be at least 12 characters.")
    encoded = hash_password(password)
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with db.tx() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user_id = connection.execute(
                "INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)",
                (username, encoded, created_at),
            ).lastrowid
    except sqlite3.IntegrityError as error:
        if "users.username" in str(error).lower() or "unique" in str(error).lower():
            raise UsernameTakenError("That username is already registered.") from error
        raise
    return {"id": user_id, "username": username}


def create_session(user_id, username, secret):
    expires_at = int(time.time()) + SESSION_SECONDS
    payload = json.dumps(
        {"user_id": user_id, "username": username, "expires_at": expires_at, "nonce": secrets.token_urlsafe(16)},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_payload = _b64encode(payload)
    signature = hmac.new(secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_b64encode(signature)}"


def session_user(token, secret):
    if not token or len(token) > 4096:
        return None
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        signature = _b64decode(encoded_signature)
        expected_signature = hmac.new(secret, encoded_payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            return None
        payload = json.loads(_b64decode(encoded_payload))
        user_id = payload["user_id"]
        username = payload["username"]
        expires_at = payload["expires_at"]
        if (
            not isinstance(user_id, int)
            or isinstance(user_id, bool)
            or user_id < 1
            or not isinstance(username, str)
            or not isinstance(expires_at, int)
            or expires_at <= int(time.time())
        ):
            return None
        return {"id": user_id, "username": username}
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        return None
