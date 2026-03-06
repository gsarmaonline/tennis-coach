"""
Redis-backed job state store.

Each job is stored as a JSON blob under key ``job:{job_id}``.

Schema
------
{
    "job_id":          str,
    "status":          "pending" | "running" | "completed" | "failed",
    "progress":        int (0-100),
    "message":         str,
    "created_at":      ISO-8601 datetime str,
    "updated_at":      ISO-8601 datetime str,
    # Set only when completed:
    "input_s3_key":       str,
    "frame_data":         list,   # per-frame landmarks + angles for canvas rendering
    "fps":                float,
    "total_source_frames": int,
    "metrics":            dict,
    "coaching_report":    dict,
    # Set only when failed:
    "error":           str,
}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from api.services.redis_client import get_redis_client
from api.settings import settings


def _key(job_id: str) -> str:
    return f"job:{job_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_job(
    job_id: str,
    user_id: str = "",
    original_filename: str = "",
    activity: str = "tennis",
) -> dict[str, Any]:
    """Insert a new job record with status=pending. Returns the record."""
    now = _now_iso()
    record: dict[str, Any] = {
        "job_id": job_id,
        "user_id": user_id,
        "original_filename": original_filename,
        "activity": activity,
        "status": "pending",
        "progress": 0,
        "message": "Queued",
        "created_at": now,
        "updated_at": now,
    }
    r = get_redis_client()
    r.set(_key(job_id), json.dumps(record), ex=settings.job_ttl)
    return record


def update_job(job_id: str, **fields: Any) -> None:
    """
    Merge *fields* into the existing job record and refresh TTL.

    Common fields: status, progress, message, input_s3_key,
                   frame_data, fps, total_source_frames,
                   metrics, coaching_report, error.
    """
    r = get_redis_client()
    raw = r.get(_key(job_id))
    if raw is None:
        return
    record: dict[str, Any] = json.loads(raw)
    record.update(fields)
    record["updated_at"] = _now_iso()
    r.set(_key(job_id), json.dumps(record), ex=settings.job_ttl)


def get_job(job_id: str) -> dict[str, Any] | None:
    """Return the job record, or None if not found."""
    r = get_redis_client()
    raw = r.get(_key(job_id))
    if raw is None:
        return None
    return json.loads(raw)


def get_video_cache(user_id: str, file_hash: str) -> str | None:
    """Return existing s3_key for this user+hash combo, or None."""
    r = get_redis_client()
    return r.get(f"vidcache:{user_id}:{file_hash}")


def set_video_cache(user_id: str, file_hash: str, s3_key: str, ttl: int = 604800) -> None:
    """Store user+hash → s3_key mapping with TTL (default 7 days)."""
    r = get_redis_client()
    r.set(f"vidcache:{user_id}:{file_hash}", s3_key, ex=ttl)
