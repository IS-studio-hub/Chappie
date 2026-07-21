"""Persist search jobs and results per user in MongoDB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.models import SearchResponse, SearchStatus
from app.services.db import get_db

COLLECTION = "search_jobs"
ACTIVE_STATUSES = ("pending", "running")
# Don't write progress to Mongo more often than this
PROGRESS_WRITE_MIN_SECONDS = 1.5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slim_result_payload(result: SearchResponse | dict | None) -> dict | None:
    """Store results without heavy logo SVG blobs."""
    if result is None:
        return None
    data = result.model_dump() if isinstance(result, SearchResponse) else dict(result)
    businesses = []
    for b in data.get("businesses") or []:
        row = dict(b)
        book = row.get("brand_book")
        if isinstance(book, dict) and book.get("logo_svg"):
            book = {**book, "logo_svg": None}
            row["brand_book"] = book
        businesses.append(row)
    data["businesses"] = businesses
    return data


def status_from_doc(doc: dict | None) -> SearchStatus | None:
    if not doc:
        return None
    result = None
    raw_result = doc.get("result")
    if raw_result:
        try:
            result = SearchResponse(**raw_result)
        except Exception:
            result = None
    return SearchStatus(
        job_id=doc["job_id"],
        status=doc.get("status") or "pending",
        progress=int(doc.get("progress") or 0),
        total=int(doc.get("total") or 0),
        message=doc.get("message") or "",
        result=result,
        error=doc.get("error"),
    )


async def create_search_job(
    *,
    job_id: str,
    user_id: str,
    request_payload: dict[str, Any],
    plan_id: str | None = None,
) -> None:
    db = get_db()
    now = _now()
    await db[COLLECTION].insert_one(
        {
            "job_id": job_id,
            "user_id": user_id,
            "plan_id": plan_id,
            "status": "pending",
            "progress": 0,
            "total": 0,
            "message": "Starting search...",
            "error": None,
            "request": request_payload,
            "result": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "last_progress_write_at": now,
        }
    )


async def sync_search_job(
    job: SearchStatus,
    *,
    force: bool = False,
) -> None:
    """Upsert progress/result for an in-memory job into Mongo."""
    db = get_db()
    now = _now()
    existing = await db[COLLECTION].find_one(
        {"job_id": job.job_id},
        {"last_progress_write_at": 1, "status": 1},
    )
    if existing and not force and job.status in ACTIVE_STATUSES:
        last = existing.get("last_progress_write_at")
        if isinstance(last, datetime):
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() < PROGRESS_WRITE_MIN_SECONDS:
                # Still allow status flips
                if existing.get("status") == job.status:
                    return

    update: dict[str, Any] = {
        "status": job.status,
        "progress": job.progress,
        "total": job.total,
        "message": job.message or "",
        "error": job.error,
        "updated_at": now,
        "last_progress_write_at": now,
    }
    if job.status in ("completed", "failed"):
        update["completed_at"] = now
    if job.result is not None:
        update["result"] = _slim_result_payload(job.result)

    await db[COLLECTION].update_one(
        {"job_id": job.job_id},
        {"$set": update},
        upsert=False,
    )


async def get_search_job(job_id: str, user_id: str) -> SearchStatus | None:
    """Load a search job; user_id is required so jobs cannot leak across accounts."""
    if not user_id:
        return None
    doc = await get_db()[COLLECTION].find_one({"job_id": job_id, "user_id": user_id})
    return status_from_doc(doc)


async def get_active_search_job(user_id: str) -> SearchStatus | None:
    doc = await get_db()[COLLECTION].find_one(
        {"user_id": user_id, "status": {"$in": list(ACTIVE_STATUSES)}},
        sort=[("updated_at", -1)],
    )
    if not doc:
        return None
    # If a job looks abandoned (server died mid-run), close it out
    updated = doc.get("updated_at")
    if isinstance(updated, datetime):
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if (_now() - updated).total_seconds() > 45 * 60:
            await get_db()[COLLECTION].update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "status": "failed",
                        "error": "Search stalled — please run the search again.",
                        "message": "Search interrupted — please try again.",
                        "updated_at": _now(),
                        "completed_at": _now(),
                    }
                },
            )
            return None
    return status_from_doc(doc)


async def get_latest_completed_search(user_id: str) -> SearchStatus | None:
    doc = await get_db()[COLLECTION].find_one(
        {"user_id": user_id, "status": "completed", "result": {"$ne": None}},
        sort=[("completed_at", -1), ("updated_at", -1)],
    )
    return status_from_doc(doc)


async def fail_stale_running_jobs(*, older_than_hours: float = 6.0) -> int:
    """Mark abandoned running jobs as failed after a server restart / hang."""
    cutoff = _now() - timedelta(hours=older_than_hours)
    result = await get_db()[COLLECTION].update_many(
        {
            "status": {"$in": list(ACTIVE_STATUSES)},
            "updated_at": {"$lt": cutoff},
        },
        {
            "$set": {
                "status": "failed",
                "error": "Search timed out or the server restarted. Please run the search again.",
                "message": "Search interrupted — please try again.",
                "updated_at": _now(),
                "completed_at": _now(),
            }
        },
    )
    return int(result.modified_count or 0)


async def ensure_search_job_indexes() -> None:
    db = get_db()
    await db[COLLECTION].create_index("job_id", unique=True)
    await db[COLLECTION].create_index([("user_id", 1), ("updated_at", -1)])
    await db[COLLECTION].create_index([("user_id", 1), ("status", 1), ("updated_at", -1)])
    await db[COLLECTION].create_index([("user_id", 1), ("completed_at", -1)])
