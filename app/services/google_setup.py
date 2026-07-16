import asyncio
import time

import httpx
from google.auth.transport.requests import Request

from app.config import settings

REQUIRED_APIS = [
    "places.googleapis.com",
    "geocoding-backend.googleapis.com",
    "gmail.googleapis.com",
]

_last_enable_time = 0.0


def _get_creds():
    from app.services.google_auth import _load_credentials
    return _load_credentials()


def ensure_google_apis_enabled(force: bool = False) -> None:
    """Enable required Google Maps APIs on the project if not already active."""
    global _last_enable_time
    if not force and time.time() - _last_enable_time < 60:
        return

    creds = _get_creds()
    if not creds:
        return

    if not creds.valid:
        creds.refresh(Request())

    project = creds.project_id or settings.google_cloud_project
    if not project:
        return

    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        for api in REQUIRED_APIS:
            try:
                client.post(
                    f"https://serviceusage.googleapis.com/v1/projects/{project}/services/{api}:enable",
                    headers=headers,
                    json={},
                )
            except Exception:
                pass

    _last_enable_time = time.time()


def parse_google_error(response: httpx.Response) -> str:
    """Extract a user-friendly message from a Google API error response."""
    try:
        data = response.json()
        error = data.get("error", {})
        message = error.get("message", response.text)
        status = error.get("status", "")

        if status == "PERMISSION_DENIED":
            details = error.get("details", [])
            for d in details:
                reason = d.get("reason", "")
                meta = d.get("metadata", {})
                if reason == "SERVICE_DISABLED":
                    url = meta.get("activationUrl", "")
                    return (
                        "Google Places API is not enabled on your project. "
                        f"Enable it here: {url}"
                    )
                if reason == "API_KEY_SERVICE_BLOCKED":
                    return "API access is blocked. Check billing and API restrictions in Google Cloud Console."

        if "billing" in message.lower():
            return (
                "Google Cloud billing is not enabled. "
                "Link a billing account at https://console.cloud.google.com/billing "
                f"(project: {settings.google_cloud_project})"
            )

        return message
    except Exception:
        return f"Google API error {response.status_code}: {response.text[:300]}"


async def google_post(url: str, headers: dict, json: dict, retries: int = 2) -> httpx.Response:
    """POST to Google API with auto-retry on 403 SERVICE_DISABLED."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(retries + 1):
            response = await client.post(url, headers=headers, json=json)
            if response.status_code != 403:
                return response

            error_text = response.text
            if "SERVICE_DISABLED" in error_text or "has not been used" in error_text:
                ensure_google_apis_enabled(force=True)
                await asyncio.sleep(3 * (attempt + 1))
                from app.services.google_auth import get_google_headers
                field_mask = headers.get("X-Goog-FieldMask", "*")
                headers = get_google_headers(field_mask)
                continue
            return response

        return response


async def google_get(url: str, headers: dict, retries: int = 2) -> httpx.Response:
    """GET from Google API with auto-retry on 403 SERVICE_DISABLED."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(retries + 1):
            response = await client.get(url, headers=headers)
            if response.status_code != 403:
                return response

            error_text = response.text
            if "SERVICE_DISABLED" in error_text or "has not been used" in error_text:
                ensure_google_apis_enabled(force=True)
                await asyncio.sleep(3 * (attempt + 1))
                from app.services.google_auth import get_google_headers
                field_mask = headers.get("X-Goog-FieldMask", "*")
                headers = get_google_headers(field_mask)
                continue
            return response

        return response


async def diagnose_google_access() -> dict:
    """Run diagnostics to check Google API access."""
    result = {
        "credentials_loaded": False,
        "service_account": None,
        "project": settings.google_cloud_project,
        "places_api": "unknown",
        "geocode_test": "unknown",
        "message": "",
    }

    creds = _get_creds()
    if not creds:
        result["message"] = "No service account credentials found. Check GOOGLE_APPLICATION_CREDENTIALS in .env"
        return result

    result["credentials_loaded"] = True
    result["service_account"] = creds.service_account_email
    result["project"] = creds.project_id

    ensure_google_apis_enabled(force=True)

    try:
        from app.services.google_auth import get_google_headers
        headers = get_google_headers("places.displayName,places.location")
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers=headers,
                json={"textQuery": "Toronto", "maxResultCount": 1},
            )
        result["places_api"] = "ok" if r.status_code == 200 else f"error_{r.status_code}"
        if r.status_code != 200:
            result["message"] = parse_google_error(r)
        else:
            result["message"] = "All Google APIs working correctly."
    except Exception as e:
        result["places_api"] = "error"
        result["message"] = str(e)

    return result
