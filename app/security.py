"""Password hashing (PBKDF2), JWT tokens, role checks, bootstrap of the first admin."""
from __future__ import annotations
import hashlib, hmac, logging, os, secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from .config import settings
from .db import db, now

log = logging.getLogger("olist.security")
_ITER = 200_000
_secret: Optional[str] = None


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def jwt_secret() -> str:
    global _secret
    if _secret is None:
        usable = not settings._weak(settings.jwt_secret, 32)          # placeholders / short values are never used as a signing key
        _secret = settings.jwt_secret if usable else secrets.token_urlsafe(48)
        if not usable:
            log.warning("JWT_SECRET not set: using a random secret (tokens are invalidated on restart). Set JWT_SECRET in production.")
    return _secret


def create_token(username: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_ttl_minutes)
    return jwt.encode({"sub": username, "role": role, "exp": exp}, jwt_secret(), algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, jwt_secret(), algorithms=["HS256"])


def bootstrap_users() -> None:
    with db() as con:
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            usable = not settings._weak(settings.admin_password, 8)       # "change-me-now" from .env.example is NOT accepted as a password
            pw = settings.admin_password if usable else secrets.token_urlsafe(12)
            con.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)", (settings.admin_username, hash_password(pw), "admin", now()))
            if not usable:
                log.warning("=== First start: created admin user '%s' with generated password: %s  (set ADMIN_PASSWORD to choose your own) ===", settings.admin_username, pw)
        if settings.seed_demo_users:
            for u, p, r in (("operator", "operator123", "operator"), ("viewer", "viewer123", "viewer")):
                if not con.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
                    con.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)", (u, hash_password(p), r, now()))
                    log.warning("DEMO user created: %s / %s (disable SEED_DEMO_USERS in production)", u, p)
