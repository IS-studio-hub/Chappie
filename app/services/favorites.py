"""Per-user favorite businesses saved from search results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.services.db import get_db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _favorite_key(business: dict[str, Any]) -> str:
    place_id = (business.get("place_id") or "").strip()
    if place_id:
        return place_id
    name = (business.get("name") or "").strip().lower()
    address = (business.get("address") or "").strip().lower()
    return f"name:{name}|addr:{address}"


def serialize_favorite(doc: dict[str, Any]) -> dict[str, Any]:
    business = doc.get("business") or {}
    return {
        "id": str(doc["_id"]),
        "place_id": doc.get("place_id"),
        "favorite_key": doc.get("favorite_key") or "",
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
        "business": business,
        "business_name": business.get("name") or doc.get("business_name") or "",
        "business_address": business.get("address") or doc.get("business_address") or "",
        "category": business.get("category") or "",
        "rating": business.get("rating"),
        "review_count": business.get("review_count"),
        "phone": business.get("phone"),
        "contact_email": business.get("contact_email"),
        "contact_emails": business.get("contact_emails") or [],
        "google_maps_url": business.get("google_maps_url"),
        "website_url": business.get("website_url"),
        "has_website": bool(business.get("has_website")),
    }


async def list_favorites(user_id: str) -> dict[str, Any]:
    db = get_db()
    cursor = db.favorites.find({"user_id": user_id}).sort("created_at", -1)
    favorites = [serialize_favorite(doc) async for doc in cursor]
    return {
        "count": len(favorites),
        "favorites": favorites,
    }


async def add_favorite(user_id: str, business: dict[str, Any]) -> dict[str, Any]:
    name = (business.get("name") or "").strip()
    if not name:
        raise ValueError("Business name is required.")

    key = _favorite_key(business)
    if key in ("name:|addr:", ""):
        raise ValueError("This business is missing an id — try another listing.")

    db = get_db()
    now = _now()
    place_id = (business.get("place_id") or "").strip() or None

    # Keep a lean snapshot (drop huge nested payloads if present)
    snapshot = dict(business)
    if isinstance(snapshot.get("brand_book"), dict):
        bb = dict(snapshot["brand_book"])
        bb.pop("logo_svg", None)
        snapshot["brand_book"] = bb

    existing = await db.favorites.find_one({"user_id": user_id, "favorite_key": key})
    if existing:
        await db.favorites.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "business": snapshot,
                    "business_name": name,
                    "business_address": business.get("address") or "",
                    "place_id": place_id,
                    "updated_at": now,
                }
            },
        )
        doc = await db.favorites.find_one({"_id": existing["_id"]})
        return serialize_favorite(doc)

    doc = {
        "user_id": user_id,
        "favorite_key": key,
        "place_id": place_id,
        "business_name": name,
        "business_address": business.get("address") or "",
        "business": snapshot,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.favorites.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_favorite(doc)


async def remove_favorite(
    user_id: str,
    *,
    favorite_id: str | None = None,
    place_id: str | None = None,
    favorite_key: str | None = None,
) -> bool:
    db = get_db()
    query: dict[str, Any] = {"user_id": user_id}

    if favorite_id:
        try:
            query["_id"] = ObjectId(favorite_id)
        except Exception as e:
            raise ValueError("Invalid favorite id.") from e
    elif place_id:
        query["place_id"] = place_id
    elif favorite_key:
        query["favorite_key"] = favorite_key
    else:
        raise ValueError("Provide a favorite id or place id.")

    result = await db.favorites.delete_one(query)
    return result.deleted_count > 0


async def get_favorite_keys_for_places(user_id: str, place_ids: list[str]) -> dict[str, bool]:
    ids = [p for p in place_ids if p]
    if not ids:
        return {}
    db = get_db()
    cursor = db.favorites.find(
        {"user_id": user_id, "place_id": {"$in": ids}},
        {"place_id": 1},
    )
    out: dict[str, bool] = {}
    async for doc in cursor:
        pid = doc.get("place_id")
        if pid:
            out[pid] = True
    return out
