"""
Redis-backed user store.

Key schema
----------
user:{user_id}          JSON user record  — TTL: 90 days
user:google:{google_id} user_id string    — TTL: 90 days
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from api.services.redis_client import get_redis_client
from api.settings import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_key(user_id: str) -> str:
    return f"user:{user_id}"


def _google_key(google_id: str) -> str:
    return f"user:google:{google_id}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_user(google_id: str, email: str, name: str, picture: str) -> dict[str, Any]:
    """Create a new user record in Redis. Returns the record."""
    user_id = str(uuid.uuid4())
    now = _now_iso()
    record: dict[str, Any] = {
        "user_id": user_id,
        "google_id": google_id,
        "email": email,
        "name": name,
        "picture": picture,
        "created_at": now,
        "last_login": now,
    }
    r = get_redis_client()
    pipe = r.pipeline()
    pipe.set(_user_key(user_id), json.dumps(record), ex=settings.user_ttl)
    pipe.set(_google_key(google_id), user_id, ex=settings.user_ttl)
    pipe.execute()
    return record


def get_user_by_google_id(google_id: str) -> dict[str, Any] | None:
    """Return a user record looked up by Google sub, or None if not found."""
    r = get_redis_client()
    user_id = r.get(_google_key(google_id))
    if user_id is None:
        return None
    return get_user(user_id)


def get_user(user_id: str) -> dict[str, Any] | None:
    """Return a user record by internal user_id, or None if not found."""
    r = get_redis_client()
    raw = r.get(_user_key(user_id))
    if raw is None:
        return None
    return json.loads(raw)


def update_user(user_id: str, **fields: Any) -> None:
    """Merge fields into the existing user record and reset TTLs."""
    r = get_redis_client()
    raw = r.get(_user_key(user_id))
    if raw is None:
        return
    record: dict[str, Any] = json.loads(raw)
    record.update(fields)
    google_id = record.get("google_id", "")
    pipe = r.pipeline()
    pipe.set(_user_key(user_id), json.dumps(record), ex=settings.user_ttl)
    if google_id:
        pipe.set(_google_key(google_id), user_id, ex=settings.user_ttl)
    pipe.execute()
