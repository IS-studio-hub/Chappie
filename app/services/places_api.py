import asyncio
import math
import time

import httpx

from app.models import Business
from app.services.google_auth import get_google_headers
from app.services.google_setup import ensure_google_apis_enabled, google_post, parse_google_error

PLACES_BASE = "https://places.googleapis.com/v1"

# SMB / local high-yield types (free / small / mid — no-website leads)
PLACE_TYPES_SMB = [
    "restaurant", "cafe", "bar", "bakery",
    "grocery_or_supermarket", "convenience_store", "pharmacy",
    "doctor", "dentist", "physiotherapist", "veterinary_care",
    "beauty_salon", "hair_care", "spa", "gym",
    "lawyer", "accounting", "insurance_agency", "real_estate_agency",
    "car_repair", "car_dealer",
    "electrician", "plumber", "roofing_contractor", "painter", "locksmith",
    "moving_company", "laundry",
    "florist", "clothing_store", "furniture_store",
    "home_goods_store", "hardware_store",
    "pet_store", "lodging",
]

# Large plan: SMB + corporate / professional / public / tech / marketing
PLACE_TYPES_LARGE = list(dict.fromkeys(PLACE_TYPES_SMB + [
    "corporate_office", "coworking_space", "business_center", "manufacturer",
    "consultant",
    "marketing_consultant", "courier_service",
    "telecommunications_service_provider", "television_studio",
    "electronics_store",
    "bank", "atm",
    "employment_agency", "travel_agency", "storage",
    "government_office", "local_government_office", "city_hall", "courthouse",
    "post_office", "police", "fire_station", "embassy",
    "library", "university", "school", "hospital",
    "hotel", "museum", "performing_arts_theater", "stadium",
    "shopping_mall", "department_store", "sporting_goods_store", "book_store",
]))

PLACE_TYPES = PLACE_TYPES_SMB

# Text-search queries used when Nearby alone can't fill the email quota (large plan)
TEXT_SEARCH_QUERIES = [
    "companies",
    "corporate offices",
    "marketing agencies",
    "IT companies",
    "software companies",
    "consulting firms",
    "government offices",
    "law firms",
    "accounting firms",
    "real estate offices",
    "clinics",
    "dentists",
    "restaurants",
    "hotels",
    "banks",
    "schools",
    "coworking spaces",
]

NEARBY_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.primaryType",
    "places.types",
])

TYPE_CONCURRENCY = 6
MAX_PAGES_PER_TYPE = 2
DEFAULT_MAX_RESULTS = 50
MAX_CANDIDATE_POOL = 400
CACHE_TTL_SECONDS = 6 * 3600

_nearby_cache: dict[str, tuple[float, dict]] = {}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two WGS84 points, in kilometers."""
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def annotate_distance_from_center(
    businesses: list[Business],
    center_lat: float,
    center_lng: float,
) -> list[Business]:
    out: list[Business] = []
    for biz in businesses:
        data = biz.model_dump()
        if biz.latitude is not None and biz.longitude is not None:
            data["distance_km"] = round(
                haversine_km(center_lat, center_lng, biz.latitude, biz.longitude),
                3,
            )
        else:
            data["distance_km"] = None
        out.append(Business(**data))
    return out


def sort_businesses_by_distance(businesses: list[Business]) -> list[Business]:
    """Closest to search center first; unknown distance last."""
    return sorted(
        businesses,
        key=lambda b: (
            b.distance_km is None,
            b.distance_km if b.distance_km is not None else 1e9,
            -(b.review_count or 0),
            (b.name or "").lower(),
        ),
    )


def search_radius_rings(max_km: float) -> list[float]:
    """Growing search radii from near the center out to ``max_km``.

    Example for 10 km: 1 → 2 → 3.5 → 5 → 7.5 → 10.
    """
    max_km = max(0.5, float(max_km))
    if max_km <= 1.0:
        return [round(max_km, 2)]

    milestones = [1.0, 2.0, 3.5, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0]
    rings: list[float] = []
    for r in milestones:
        if r < max_km - 0.05:
            rings.append(r)
    rings.append(max_km)

    out: list[float] = []
    for r in rings:
        rr = round(r, 2)
        if not out or abs(out[-1] - rr) > 0.05:
            out.append(rr)
    return out


def _headers() -> dict:
    return get_google_headers(NEARBY_FIELD_MASK)


def _cache_key(place_type: str, lat: float, lng: float, radius_m: float) -> str:
    return f"{place_type}:{lat:.4f}:{lng:.4f}:{int(radius_m)}"


def _parse_place(place: dict, *, require_no_website: bool = True) -> Business | None:
    website = place.get("websiteUri") or None
    has_website = bool(website)
    if require_no_website and has_website:
        return None

    name = place.get("displayName", {}).get("text", "Unknown")
    location = place.get("location", {})

    return Business(
        place_id=place.get("id"),
        name=name,
        rating=place.get("rating"),
        review_count=place.get("userRatingCount"),
        category=place.get("primaryType", "").replace("_", " ").title() if place.get("primaryType") else None,
        address=place.get("formattedAddress"),
        phone=place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"),
        has_website=has_website,
        website_url=website,
        google_maps_url=place.get("googleMapsUri"),
        categories=[t.replace("_", " ").title() for t in place.get("types", [])[:10]],
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
    )


async def _search_nearby(
    place_type: str,
    lat: float,
    lng: float,
    radius_m: float,
    page_token: str | None = None,
) -> dict:
    cache_key = None
    if not page_token:
        cache_key = _cache_key(place_type, lat, lng, radius_m)
        cached = _nearby_cache.get(cache_key)
        if cached and cached[0] > time.time():
            return cached[1]

    body: dict = {
        "includedTypes": [place_type],
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
    }
    if page_token:
        body["pageToken"] = page_token

    response = await google_post(
        f"{PLACES_BASE}/places:searchNearby",
        headers=_headers(),
        json=body,
    )
    if response.status_code == 403:
        raise ValueError(parse_google_error(response))
    response.raise_for_status()
    data = response.json()

    if cache_key is not None:
        _nearby_cache[cache_key] = (time.time() + CACHE_TTL_SECONDS, data)

    return data


async def _search_text(
    query: str,
    lat: float,
    lng: float,
    radius_m: float,
) -> dict:
    body = {
        "textQuery": query,
        "maxResultCount": 20,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
    }
    response = await google_post(
        f"{PLACES_BASE}/places:searchText",
        headers=_headers(),
        json=body,
    )
    if response.status_code == 403:
        raise ValueError(parse_google_error(response))
    response.raise_for_status()
    return response.json()


async def _search_type(
    place_type: str,
    lat: float,
    lng: float,
    radius_m: float,
    seen_ids: set[str],
    lock: asyncio.Lock,
    *,
    require_no_website: bool = True,
    per_type_limit: int | None = None,
) -> list[Business]:
    found: list[Business] = []
    page_token = None
    cap = per_type_limit if per_type_limit and per_type_limit > 0 else None

    for _ in range(MAX_PAGES_PER_TYPE):
        if cap is not None and len(found) >= cap:
            break
        try:
            data = await _search_nearby(place_type, lat, lng, radius_m, page_token)
        except httpx.HTTPStatusError:
            break

        places = data.get("places", [])
        if not places:
            break

        for p in places:
            if cap is not None and len(found) >= cap:
                break
            pid = p.get("id")
            if not pid:
                continue
            async with lock:
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)

            business = _parse_place(p, require_no_website=require_no_website)
            if business:
                found.append(business)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return found


async def search_nearby_businesses(
    lat: float,
    lng: float,
    radius_km: float,
    progress_callback=None,
    max_results: int = DEFAULT_MAX_RESULTS,
    exclude_ids: set[str] | None = None,
    *,
    require_no_website: bool = True,
    place_types: list[str] | None = None,
    per_type_limit: int | None = None,
    exhaust_types: bool = False,
) -> list[Business]:
    """Nearby Places search.

    ``require_no_website=True`` (default): only businesses with no websiteUri.
    ``require_no_website=False``: include businesses with websites (large plan).
    ``per_type_limit``: max new places kept per type (forces category diversity).
    ``exhaust_types``: scan every type even after hitting max_results soft target
    (still hard-capped by max_results).
    """
    ensure_google_apis_enabled()
    radius_m = radius_km * 1000
    seen_ids: set[str] = set(exclude_ids or ())
    businesses: list[Business] = []
    types = list(place_types or PLACE_TYPES_SMB)
    total_types = len(types)
    lock = asyncio.Lock()
    completed = 0
    target = max(1, min(int(max_results), MAX_CANDIDATE_POOL))
    label = "businesses" if not require_no_website else "businesses without websites"

    async def run_type(place_type: str) -> list[Business]:
        return await _search_type(
            place_type,
            lat,
            lng,
            radius_m,
            seen_ids,
            lock,
            require_no_website=require_no_website,
            per_type_limit=per_type_limit,
        )

    for batch_start in range(0, total_types, TYPE_CONCURRENCY):
        # Soft early-stop only when not exhausting types for diversity
        if not exhaust_types and len(businesses) >= target:
            break

        batch = types[batch_start : batch_start + TYPE_CONCURRENCY]
        results = await asyncio.gather(
            *[run_type(t) for t in batch],
            return_exceptions=True,
        )

        for result in results:
            completed += 1
            if isinstance(result, Exception):
                continue
            for biz in result:
                if len(businesses) >= target:
                    break
                businesses.append(biz)

        if progress_callback:
            await progress_callback(
                min(completed, total_types),
                total_types,
                f"Found {len(businesses)} {label}...",
            )

        if len(businesses) >= target:
            break

    if progress_callback:
        await progress_callback(
            total_types,
            total_types,
            f"Found {len(businesses)} {label}",
        )

    return businesses[:target]


async def search_text_businesses(
    lat: float,
    lng: float,
    radius_km: float,
    *,
    area_label: str = "",
    require_no_website: bool = True,
    exclude_ids: set[str] | None = None,
    max_results: int = 80,
    progress_callback=None,
) -> list[Business]:
    """Supplement Nearby with Text Search queries across industries."""
    ensure_google_apis_enabled()
    radius_m = radius_km * 1000
    seen_ids: set[str] = set(exclude_ids or ())
    found: list[Business] = []
    area = (area_label or "").strip()
    queries = [
        f"{q} near {area}" if area else q
        for q in TEXT_SEARCH_QUERIES
    ]
    total = len(queries)

    for i, query in enumerate(queries):
        if len(found) >= max_results:
            break
        try:
            data = await _search_text(query, lat, lng, radius_m)
        except Exception:
            continue
        for p in data.get("places", []) or []:
            if len(found) >= max_results:
                break
            pid = p.get("id")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            biz = _parse_place(p, require_no_website=require_no_website)
            if biz:
                found.append(biz)
        if progress_callback:
            await progress_callback(
                i + 1,
                total,
                f"Text search {i + 1}/{total}: {len(found)} extra candidates...",
            )

    return found


async def search_businesses_without_website(
    lat: float,
    lng: float,
    radius_km: float,
    progress_callback=None,
    max_results: int = DEFAULT_MAX_RESULTS,
    exclude_ids: set[str] | None = None,
) -> list[Business]:
    """Back-compat wrapper — no-website SMB search."""
    return await search_nearby_businesses(
        lat,
        lng,
        radius_km,
        progress_callback=progress_callback,
        max_results=max_results,
        exclude_ids=exclude_ids,
        require_no_website=True,
        place_types=PLACE_TYPES_SMB,
    )


def candidate_pool_size(plan_max_results: int, *, large_plan: bool = False) -> int:
    """How many candidates to scan to try filling the email quota."""
    target = max(1, int(plan_max_results))
    if large_plan:
        # Broader types + websites → need a large diverse pool for ~50 emails
        return min(MAX_CANDIDATE_POOL, max(target * 8, 250))
    return min(MAX_CANDIDATE_POOL, max(target * 5, target + 25))
