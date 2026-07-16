"""Outreach pipeline: track sent → opened → replied → booked → closed."""

from __future__ import annotations

import html
import secrets
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.config import settings
from app.services.db import get_db
from app.services.learning_loop import deal_dimensions_from_business

PIPELINE_STATUSES = ("sent", "opened", "replied", "booked", "closed")
STATUS_LABELS = {
    "sent": "Sent",
    "opened": "Opened",
    "replied": "Replied",
    "booked": "Booked",
    "closed": "Closed",
}

# Transparent 1x1 GIF
PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_deal(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "business_name": doc.get("business_name") or "",
        "business_address": doc.get("business_address") or "",
        "place_id": doc.get("place_id"),
        "category": doc.get("category") or "",
        "city": doc.get("city") or "",
        "province": doc.get("province") or "",
        "recipients": doc.get("recipients") or [],
        "subject": doc.get("subject") or "",
        "status": doc.get("status") or "sent",
        "status_label": STATUS_LABELS.get(doc.get("status") or "sent", "Sent"),
        "open_count": int(doc.get("open_count") or 0),
        "opened_at": doc["opened_at"].isoformat() if doc.get("opened_at") else None,
        "sent_at": doc["sent_at"].isoformat() if doc.get("sent_at") else None,
        "updated_at": doc["updated_at"].isoformat() if doc.get("updated_at") else None,
        "google_maps_url": doc.get("google_maps_url"),
        "notes": doc.get("notes") or "",
        "status_history": [
            {
                "status": h.get("status"),
                "at": h["at"].isoformat() if h.get("at") else None,
                "note": h.get("note") or "",
            }
            for h in (doc.get("status_history") or [])
        ],
    }


def inject_tracking_pixel(html_body: str, open_token: str) -> str:
    if not open_token or not html_body:
        return html_body
    pixel_url = f"{settings.app_base_url.rstrip('/')}/api/track/open/{open_token}.gif"
    pixel = (
        f'<img src="{pixel_url}" width="1" height="1" alt="" '
        'style="display:block;width:1px;height:1px;border:0" />'
    )
    if "</body>" in html_body:
        return html_body.replace("</body>", f"{pixel}</body>", 1)
    return html_body + pixel


def plain_to_html(
    body: str,
    open_token: str,
    *,
    include_tracking_pixel: bool = True,
) -> str:
    escaped = html.escape(body).replace("\n", "<br>\n")
    doc = (
        "<!DOCTYPE html><html><body "
        'style="font-family:Georgia,serif;line-height:1.55;color:#222;font-size:15px">'
        f"<div>{escaped}</div>"
        "</body></html>"
    )
    if include_tracking_pixel and open_token:
        return inject_tracking_pixel(doc, open_token)
    return doc


async def record_outreach_send(
    user_id: str,
    *,
    business: dict[str, Any],
    recipients: list[str],
    subject: str,
    body: str,
    include_tracking_pixel: bool = True,
    html_body: str | None = None,
) -> dict[str, Any]:
    db = get_db()
    now = _now()
    place_id = business.get("place_id")
    query: dict[str, Any] = {"user_id": user_id}
    if place_id:
        query["place_id"] = place_id
    else:
        query["recipients.0"] = recipients[0] if recipients else ""

    existing = await db.outreach_deals.find_one(query)
    open_token = secrets.token_urlsafe(24) if include_tracking_pixel else ""
    dims = deal_dimensions_from_business(business)

    history_entry = {"status": "sent", "at": now, "note": "Email sent"}

    tracking_fields = {
        "open_tracking_enabled": include_tracking_pixel,
        "open_token": open_token or None,
    }

    if existing:
        # Keep higher pipeline status if already past sent; still log a new send
        current = existing.get("status") or "sent"
        new_status = current if current in ("replied", "booked", "closed") else "sent"
        set_fields = {
            "recipients": recipients,
            "subject": subject,
            "body_preview": body[:500],
            "business_name": business.get("name") or existing.get("business_name"),
            "business_address": business.get("address") or existing.get("business_address"),
            "google_maps_url": business.get("google_maps_url") or existing.get("google_maps_url"),
            "place_id": place_id or existing.get("place_id"),
            "category": dims["category"] or existing.get("category") or "",
            "city": dims["city"] or existing.get("city") or "",
            "province": dims["province"] or existing.get("province") or "",
            "category_key": dims["category_key"] or existing.get("category_key") or "",
            "city_key": dims["city_key"] or existing.get("city_key") or "",
            "status": new_status if new_status != "opened" else "sent",
            "updated_at": now,
            "last_sent_at": now,
            **tracking_fields,
        }
        await db.outreach_deals.update_one(
            {"_id": existing["_id"]},
            {
                "$set": set_fields,
                "$push": {"status_history": history_entry},
                "$inc": {"send_count": 1},
            },
        )
        if current == "opened":
            await db.outreach_deals.update_one(
                {"_id": existing["_id"]},
                {"$set": {"status": "sent", "open_count": 0, "opened_at": None}},
            )
        doc = await db.outreach_deals.find_one({"_id": existing["_id"]})
    else:
        doc = {
            "user_id": user_id,
            "place_id": place_id,
            "business_name": business.get("name") or "",
            "business_address": business.get("address") or "",
            "google_maps_url": business.get("google_maps_url"),
            "category": dims["category"],
            "city": dims["city"],
            "province": dims["province"],
            "category_key": dims["category_key"],
            "city_key": dims["city_key"],
            "recipients": recipients,
            "subject": subject,
            "body_preview": body[:500],
            "status": "sent",
            "open_count": 0,
            "opened_at": None,
            "send_count": 1,
            "notes": "",
            "status_history": [history_entry],
            "sent_at": now,
            "last_sent_at": now,
            "updated_at": now,
            "created_at": now,
            **tracking_fields,
        }
        result = await db.outreach_deals.insert_one(doc)
        doc["_id"] = result.inserted_id

    if html_body and html_body.strip():
        final_html = html_body
        if include_tracking_pixel and open_token:
            final_html = inject_tracking_pixel(final_html, open_token)
    else:
        final_html = plain_to_html(
            body,
            open_token,
            include_tracking_pixel=include_tracking_pixel,
        )

    return {
        "deal": serialize_deal(doc),
        "open_token": open_token,
        "html_body": final_html,
        "include_tracking_pixel": include_tracking_pixel,
    }


async def mark_opened(open_token: str) -> bool:
    if not open_token:
        return False
    # strip .gif if present
    token = open_token.replace(".gif", "")
    db = get_db()
    doc = await db.outreach_deals.find_one({"open_token": token})
    if not doc:
        return False

    now = _now()
    updates: dict[str, Any] = {
        "$inc": {"open_count": 1},
        "$set": {"updated_at": now},
    }
    if not doc.get("opened_at"):
        updates["$set"]["opened_at"] = now

    # Only auto-advance to opened if still at sent
    if (doc.get("status") or "sent") == "sent":
        updates["$set"]["status"] = "opened"
        updates["$push"] = {
            "status_history": {"status": "opened", "at": now, "note": "Email opened"}
        }

    await db.outreach_deals.update_one({"_id": doc["_id"]}, updates)
    return True


async def update_deal_status(
    user_id: str,
    deal_id: str,
    status: str,
    notes: str | None = None,
) -> dict[str, Any]:
    if status not in PIPELINE_STATUSES:
        raise ValueError(f"Invalid status. Use one of: {', '.join(PIPELINE_STATUSES)}")
    if not ObjectId.is_valid(deal_id):
        raise ValueError("Deal not found.")

    db = get_db()
    doc = await db.outreach_deals.find_one({
        "_id": ObjectId(deal_id),
        "user_id": user_id,
    })
    if not doc:
        raise ValueError("Deal not found.")

    now = _now()
    update: dict[str, Any] = {
        "$set": {
            "status": status,
            "updated_at": now,
        },
        "$push": {
            "status_history": {
                "status": status,
                "at": now,
                "note": notes or f"Marked as {STATUS_LABELS[status]}",
            }
        },
    }
    if notes is not None:
        update["$set"]["notes"] = notes.strip()

    await db.outreach_deals.update_one({"_id": doc["_id"]}, update)
    refreshed = await db.outreach_deals.find_one({"_id": doc["_id"]})
    return serialize_deal(refreshed)


async def list_deals(user_id: str, status: str | None = None) -> dict[str, Any]:
    db = get_db()
    query: dict[str, Any] = {"user_id": user_id}
    if status and status in PIPELINE_STATUSES:
        query["status"] = status

    cursor = db.outreach_deals.find(query).sort("updated_at", -1).limit(200)
    deals = [serialize_deal(d) async for d in cursor]

    # Counts by status
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    counts = {s: 0 for s in PIPELINE_STATUSES}
    async for row in db.outreach_deals.aggregate(pipeline):
        if row["_id"] in counts:
            counts[row["_id"]] = row["count"]

    return {
        "deals": deals,
        "counts": counts,
        "statuses": [
            {"id": s, "label": STATUS_LABELS[s], "count": counts[s]}
            for s in PIPELINE_STATUSES
        ],
    }


async def get_deals_for_places(user_id: str, place_ids: list[str]) -> dict[str, dict]:
    """Map place_id -> deal summary for result cards."""
    place_ids = [p for p in place_ids if p]
    if not place_ids:
        return {}
    cursor = get_db().outreach_deals.find({
        "user_id": user_id,
        "place_id": {"$in": place_ids},
    })
    out = {}
    async for doc in cursor:
        pid = doc.get("place_id")
        if pid:
            out[pid] = {
                "id": str(doc["_id"]),
                "status": doc.get("status") or "sent",
                "status_label": STATUS_LABELS.get(doc.get("status") or "sent", "Sent"),
                "open_count": int(doc.get("open_count") or 0),
            }
    return out
