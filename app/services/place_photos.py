"""Fetch real Google Places photos for Figma Make image references."""

from __future__ import annotations

import httpx

from app.config import settings
from app.services.google_auth import get_google_headers, is_google_configured

PLACES_BASE = "https://places.googleapis.com/v1"
MAX_PHOTOS = 6


def _normalize_place_id(place_id: str | None) -> str | None:
    if not place_id:
        return None
    pid = place_id.strip()
    if not pid:
        return None
    if pid.startswith("places/"):
        return pid.removeprefix("places/")
    return pid


async def fetch_place_photo_uris(
    place_id: str | None,
    *,
    max_photos: int = MAX_PHOTOS,
    max_width_px: int = 1600,
) -> list[str]:
    """Return short-lived Google photo URIs for a place (empty if unavailable)."""
    pid = _normalize_place_id(place_id)
    if not pid or not is_google_configured():
        return []

    try:
        headers = get_google_headers("photos")
    except ValueError:
        return []

    uris: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            details = await client.get(
                f"{PLACES_BASE}/places/{pid}",
                headers=headers,
            )
            if details.status_code != 200:
                return []

            photos = details.json().get("photos") or []
            for photo in photos[:max_photos]:
                name = photo.get("name")
                if not name:
                    continue
                media_url = (
                    f"{PLACES_BASE}/{name}/media"
                    f"?maxWidthPx={max_width_px}&skipHttpRedirect=true"
                )
                # Media endpoint uses API key or bearer; reuse auth without field mask
                media_headers = {k: v for k, v in headers.items() if k != "X-Goog-FieldMask"}
                # Prefer query key when using API key auth
                if settings.google_maps_api_key and "Authorization" not in media_headers:
                    media_url += f"&key={settings.google_maps_api_key}"

                media = await client.get(media_url, headers=media_headers)
                if media.status_code != 200:
                    continue
                ctype = (media.headers.get("content-type") or "").lower()
                if "application/json" in ctype:
                    uri = (media.json() or {}).get("photoUri")
                    if uri:
                        uris.append(uri)
                elif ctype.startswith("image/"):
                    # Redirect-less binary — fall back to the media URL itself
                    uris.append(media_url.split("&key=")[0])
    except Exception:
        return uris

    return uris
