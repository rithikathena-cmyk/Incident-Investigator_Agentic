"""Pure database queries for login accounts (auth). Same layering as
quality_repository.py etc - this is the only module the server talks to the
users table through.
"""

from __future__ import annotations

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import security
from app.database.connection import get_session
from app.database.models import User

VALID_ROLES = frozenset({"admin", "user"})

# Demo-only default accounts, created once (idempotent) if the users table
# is empty - so login works out of the box without a separate manual step,
# the same "unblock local dev, don't hide the tradeoff" spirit as
# config.require_api_key(). Override via env for anything beyond a laptop
# demo; these are printed to the console at seed time either way so they're
# never a silent surprise.
DEFAULT_ACCOUNTS = (
    ("admin", os.environ.get("SEED_ADMIN_PASSWORD") or "admin123", "admin"),
    ("user", os.environ.get("SEED_USER_PASSWORD") or "user123", "user"),
)


def get_user_by_username(username: str) -> User | None:
    with get_session() as session:
        return session.execute(select(User).where(User.username == username)).scalar_one_or_none()


def create_user(session: Session, username: str, password: str, role: str) -> User:
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role '{role}' - must be one of {sorted(VALID_ROLES)}.")
    user = User(username=username, password_hash=security.hash_password(password), role=role)
    session.add(user)
    return user


def ensure_default_users() -> list[str]:
    """Create the default admin/user demo accounts if the users table is
    completely empty. Returns the usernames actually created (empty list if
    accounts already existed - so callers can decide whether to print
    credentials). Safe to call on every server startup.
    """
    with get_session() as session:
        if session.execute(select(User.id).limit(1)).first() is not None:
            return []
        created = []
        for username, password, role in DEFAULT_ACCOUNTS:
            create_user(session, username, password, role)
            created.append(username)
        session.commit()
        return created
