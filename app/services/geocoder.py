import httpx

from app.services.google_setup import ensure_google_apis_enabled, google_post, parse_google_error

PLACES_BASE = "https://places.googleapis.com/v1"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


async def _geocode_nominatim(address: str) -> tuple[float, float, str]:
    """Free fallback geocoder using OpenStreetMap."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": "Chappie-Business-Finder/1.0"},
        )
        response.raise_for_status()
        results = response.json()

    if not results:
        raise ValueError(f"Could not geocode address: {address}")

    r = results[0]
    return float(r["lat"]), float(r["lon"]), r.get("display_name", address)


async def geocode_address(address: str) -> tuple[float, float, str]:
    """Convert an address to lat/lng using Google Places API, with Nominatim fallback."""
    ensure_google_apis_enabled()

    try:
        from app.services.google_auth import get_google_headers
        headers = get_google_headers("places.displayName,places.formattedAddress,places.location")

        response = await google_post(
            f"{PLACES_BASE}/places:searchText",
            headers=headers,
            json={"textQuery": address, "maxResultCount": 1},
        )

        if response.status_code == 403:
            raise ValueError(parse_google_error(response))

        response.raise_for_status()
        data = response.json()

        places = data.get("places", [])
        if places:
            place = places[0]
            location = place.get("location", {})
            lat = location.get("latitude")
            lng = location.get("longitude")
            if lat is not None and lng is not None:
                return lat, lng, place.get("formattedAddress", address)

    except ValueError:
        raise
    except Exception:
        pass

    return await _geocode_nominatim(address)
