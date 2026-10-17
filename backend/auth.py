"""Lightweight sign-in for the hackathon: signed bearer tokens, two roles (lead, member), invite codes.

Identity is name/email based (no passwords), as in the design notes. Lead-only routes are enforced
when AUTH_REQUIRED=1; otherwise roles shape the UI and the token is informational.
"""
import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException
from sqlmodel import Session, select

from models import Project

SECRET_FILE = Path(__file__).resolve().parent / ".auth_secret"
TOKEN_TTL = 60 * 60 * 24 * 14  # two weeks


def _secret() -> bytes:
    env = os.getenv("AUTH_SECRET", "").strip()
    if env:
        return env.encode()
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().strip().encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(payload: dict) -> str:
    body = _b64(json.dumps({**payload, "iat": int(time.time())}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        return payload if time.time() - payload.get("iat", 0) < TOKEN_TTL else None
    except Exception:
        return None


def auth_required() -> bool:
    return os.getenv("AUTH_REQUIRED", "").strip().lower() in ("1", "true", "yes")


def current_auth(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    """FastAPI dependency: the decoded token, or None when signed out."""
    if authorization and authorization.lower().startswith("bearer "):
        return read_token(authorization[7:].strip())
    return None


def require_lead(authorization: Optional[str] = Header(default=None)) -> Optional[dict]:
    """FastAPI dependency for Team Lead actions.

    A signed-in member is always refused. Anonymous calls (scripts, tests) are refused only when AUTH_REQUIRED=1.
    """
    payload = current_auth(authorization)
    if payload and payload.get("role") != "lead":
        raise HTTPException(403, "Only the Team Lead can do this")
    if auth_required() and not payload:
        raise HTTPException(401, "Team Lead sign-in required")
    return payload


PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    try:
        algo, iterations, salt, digest = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(candidate, digest)
    except ValueError:
        return False


def new_invite_code(session: Session, name: str) -> str:
    """Readable, unique code from the project name, e.g. 'SMART-4821'."""
    words = re.findall(r"[A-Za-z]+", name) or ["TEAM"]
    stem = max(words, key=len)[:8].upper() if len(words[0]) < 3 else words[0][:8].upper()
    for _ in range(50):
        code = f"{stem}-{random.randint(1000, 9999)}"
        if not session.exec(select(Project).where(Project.invite_code == code)).first():
            return code
    return f"{stem}-{secrets.token_hex(3).upper()}"


def normalize_invite(code_or_link: str) -> str:
    """Accept 'SMART-2026' or a full invite link containing ?invite=SMART-2026."""
    m = re.search(r"[?&]invite=([A-Za-z0-9-]+)", code_or_link or "")
    return (m.group(1) if m else (code_or_link or "")).strip().upper()
