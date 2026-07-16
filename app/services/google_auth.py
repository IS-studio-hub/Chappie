import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.config import settings

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

_credentials = None


def _resolve_credentials_path() -> Path | None:
    raw = settings.google_application_credentials or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path if path.exists() else None


def _load_credentials():
    global _credentials
    if _credentials is not None:
        return _credentials

    creds_path = _resolve_credentials_path()
    if creds_path:
        _credentials = service_account.Credentials.from_service_account_file(
            str(creds_path),
            scopes=SCOPES,
        )
        return _credentials

    if settings.google_service_account_json:
        import json
        info = json.loads(settings.google_service_account_json)
        _credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=SCOPES,
        )
        return _credentials

    return None


def get_access_token() -> str | None:
    creds = _load_credentials()
    if not creds:
        return None
    if not creds.valid:
        creds.refresh(Request())
    return creds.token


def is_google_configured() -> bool:
    return bool(
        settings.google_maps_api_key
        or _resolve_credentials_path()
        or settings.google_service_account_json
    )


def get_google_headers(field_mask: str = "*") -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}

    if field_mask:
        headers["X-Goog-FieldMask"] = field_mask

    creds = _load_credentials()
    if creds:
        if not creds.valid:
            creds.refresh(Request())
        headers["Authorization"] = f"Bearer {creds.token}"
        project = creds.project_id or settings.google_cloud_project
        if project:
            headers["X-Goog-User-Project"] = project
    elif settings.google_maps_api_key:
        headers["X-Goog-Api-Key"] = settings.google_maps_api_key
    else:
        raise ValueError(
            "Google credentials not configured. Set GOOGLE_APPLICATION_CREDENTIALS "
            "or GOOGLE_MAPS_API_KEY in .env"
        )

    return headers
