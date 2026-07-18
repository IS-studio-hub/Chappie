import base64
import json
import secrets
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

TOKEN_PATH = Path(__file__).resolve().parents[2] / ".gmail_token.json"

# System Gmail (verification emails) - file or GMAIL_TOKEN_JSON env
_credentials: Credentials | None = None
# Per-user OAuth pending flows: state -> (Flow, user_id)
_pending_flows: dict[str, tuple[Flow, str]] = {}


def get_redirect_uri() -> str:
    return settings.gmail_oauth_redirect_uri.strip()


def _client_config() -> dict:
    redirect_uri = get_redirect_uri()
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        raise ValueError(
            "Google OAuth not configured. Set GOOGLE_OAUTH_CLIENT_ID and "
            "GOOGLE_OAUTH_CLIENT_SECRET in .env"
        )
    return {
        "web": {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _save_credentials(creds: Credentials) -> None:
    try:
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    except OSError:
        # Ephemeral containers may not persist the file; in-memory creds still work.
        pass


def _creds_from_authorized_user(raw: str | dict) -> Credentials | None:
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if not creds:
            return None
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(creds)
        return creds if creds.valid else None
    except Exception:
        return None


def _load_credentials() -> Credentials | None:
    if TOKEN_PATH.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            if creds:
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    _save_credentials(creds)
                if creds.valid:
                    return creds
        except Exception:
            pass

    raw = (settings.gmail_token_json or "").strip()
    if raw:
        return _creds_from_authorized_user(raw)
    return None


def ensure_gmail_credentials() -> Credentials | None:
    """System Gmail credentials for verification emails."""
    global _credentials

    if _credentials and _credentials.valid:
        return _credentials

    if _credentials and _credentials.expired and _credentials.refresh_token:
        try:
            _credentials.refresh(Request())
            _save_credentials(_credentials)
            if _credentials.valid:
                return _credentials
        except Exception:
            _credentials = None

    _credentials = _load_credentials()
    return _credentials if _credentials and _credentials.valid else None


def init_gmail_oauth() -> None:
    global _credentials
    _credentials = _load_credentials()


def is_gmail_oauth_connected() -> bool:
    return ensure_gmail_credentials() is not None


def get_gmail_oauth_status() -> dict:
    creds = ensure_gmail_credentials()
    if not creds:
        return {"connected": False, "method": None}

    email = ""
    try:
        service = build("oauth2", "v2", credentials=creds)
        email = service.userinfo().get().execute().get("email", "")
    except Exception:
        pass

    return {"connected": True, "method": "oauth", "email": email}


def start_oauth_flow(user_id: str) -> str:
    redirect_uri = get_redirect_uri()
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    state = secrets.token_urlsafe(24)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    _pending_flows[state] = (flow, str(user_id))
    return auth_url


def complete_oauth_flow(code: str, state: str = "") -> tuple[Credentials, str]:
    """Exchange code for credentials. Returns (creds, user_id)."""
    flow = None
    user_id = ""
    if state and state in _pending_flows:
        flow, user_id = _pending_flows.pop(state)
    else:
        if len(_pending_flows) == 1:
            state_key = next(iter(_pending_flows))
            flow, user_id = _pending_flows.pop(state_key)
        else:
            redirect_uri = get_redirect_uri()
            flow = Flow.from_client_config(
                _client_config(), scopes=SCOPES, redirect_uri=redirect_uri
            )

    flow.fetch_token(code=code)
    return flow.credentials, user_id


def get_oauth_setup_info() -> dict:
    return {
        "redirect_uri": get_redirect_uri(),
        "console_url": f"https://console.cloud.google.com/apis/credentials?project={settings.google_cloud_project}",
        "client_id": settings.google_oauth_client_id,
    }


def disconnect_gmail_oauth() -> None:
    """Disconnect system Gmail only (verification sender)."""
    global _credentials
    _credentials = None
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def send_via_gmail_api(
    to_email: str,
    subject: str,
    body: str,
    from_email: str | None = None,
    *,
    html: bool = False,
) -> dict:
    creds = ensure_gmail_credentials()
    if not creds:
        raise ValueError("Gmail not connected. Click 'Connect with Google'.")

    sender = from_email or settings.studio_email
    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    subtype = "html" if html else "plain"
    msg.attach(MIMEText(body, subtype, "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = build("gmail", "v1", credentials=creds)
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

    return {"sent": True, "to": to_email, "from": sender, "subject": subject, "method": "oauth"}
