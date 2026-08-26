"""Password hashing and session tokens for login (auth - not to be confused
with guardrails.ROLE_DOMAIN_TABLE's investigation-domain RBAC, which this
does not replace or touch).

Pure functions only, no DB/FastAPI import - same layering reason
guardrails.py and capabilities.py stay pure: it makes this file trivially
unit-testable and keeps the one security-sensitive crypto surface small and
auditable in one place.

Password hashing uses stdlib hashlib.pbkdf2_hmac (PBKDF2-HMAC-SHA256,
260,000 iterations - OWASP's current minimum recommendation for PBKDF2-
SHA256) rather than bcrypt/argon2, since it needs zero extra dependencies
and the project already avoids adding packages it doesn't need.

Session tokens are signed JWTs (PyJWT, already a transitive dependency of
the MCP stack this project uses, so no new package either) carrying
{sub: username, role, exp}. SESSION_SECRET should be set in the environment
for anything beyond a single local dev process; if unset, a random secret is
generated per-process (existing sessions/logins just don't survive a
restart, which is an acceptable tradeoff for local dev - see
require_api_key() in config.py for the same "warn, don't block local dev"
philosophy).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

PBKDF2_ITERATIONS = 260_000
_HASH_ALGO = "pbkdf2_sha256"

SESSION_ALGORITHM = "HS256"
SESSION_TTL = timedelta(hours=12)

_env_secret = os.environ.get("SESSION_SECRET")
if not _env_secret:
    print(
        "[investigator] Warning: SESSION_SECRET not set in the environment.\n"
        "  Using a random per-process secret - every existing login session "
        "will be invalidated on the next restart. For anything beyond solo "
        "local use, set it explicitly:\n"
        "    macOS/Linux:  export SESSION_SECRET=$(python -c \"import secrets; print(secrets.token_hex(32))\")\n"
        '    PowerShell:   $env:SESSION_SECRET = "<a long random string>"'
    )
_SESSION_SECRET = _env_secret or secrets.token_hex(32)


def hash_password(password: str) -> str:
    """Returns 'pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>'."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{_HASH_ALGO}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time comparison against a hash_password() output. Returns
    False (never raises) for a malformed/foreign hash rather than erroring,
    so a corrupted row denies login instead of crashing it.
    """
    try:
        algo, iterations_str, salt_hex, digest_hex = stored_hash.split("$")
        if algo != _HASH_ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations_str)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def create_session_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "role": role, "iat": now, "exp": now + SESSION_TTL}
    return jwt.encode(payload, _SESSION_SECRET, algorithm=SESSION_ALGORITHM)


def decode_session_token(token: str) -> dict[str, str] | None:
    """Returns {"sub": username, "role": role}, or None for any invalid/
    expired/tampered token - callers treat None as "not authenticated"."""
    try:
        payload = jwt.decode(token, _SESSION_SECRET, algorithms=[SESSION_ALGORITHM])
        return {"sub": str(payload["sub"]), "role": str(payload["role"])}
    except (jwt.PyJWTError, KeyError):
        return None
