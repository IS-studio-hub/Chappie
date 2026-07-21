"""Per-user Figma / OpenAI / Gmail integrations stored on the MongoDB user document."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from bson import ObjectId
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import settings
from app.services.db import get_db
from app.services.secret_crypto import decrypt_secret, encrypt_secret

OPENAI_API_BASE = "https://api.openai.com/v1"
FIGMA_API_BASE = "https://api.figma.com/v1"


def _integrations(user: dict[str, Any]) -> dict[str, Any]:
    return dict(user.get("integrations") or {})


async def _save_integrations(user_id: Any, integrations: dict[str, Any]) -> None:
    await get_db().users.update_one(
        {"_id": user_id if isinstance(user_id, ObjectId) else ObjectId(str(user_id))},
        {
            "$set": {
                "integrations": integrations,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )


async def reload_user(user: dict[str, Any]) -> dict[str, Any]:
    doc = await get_db().users.find_one({"_id": user["_id"]})
    return doc or user


# ── Figma ──

def figma_status(user: dict[str, Any]) -> dict:
    integ = _integrations(user)
    if not decrypt_secret(integ.get("figma_token")):
        return {"connected": False}
    return {
        "connected": True,
        "email": integ.get("figma_email"),
        "handle": integ.get("figma_handle"),
    }


async def connect_figma_for_user(user: dict[str, Any], token: str) -> dict:
    token = token.strip()
    if not token:
        raise ValueError("Figma API token is required")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{FIGMA_API_BASE}/me",
            headers={"X-Figma-Token": token},
        )

    if response.status_code == 403:
        raise ValueError("Invalid Figma API token. Check your personal access token.")
    if response.status_code != 200:
        raise ValueError(f"Figma API error: {response.status_code}")

    data = response.json()
    integ = _integrations(user)
    integ.update({
        "figma_token": encrypt_secret(token),
        "figma_email": data.get("email"),
        "figma_handle": data.get("handle"),
    })
    await _save_integrations(user["_id"], integ)
    user["integrations"] = integ
    return figma_status(user)


async def disconnect_figma_for_user(user: dict[str, Any]) -> dict:
    integ = _integrations(user)
    for key in ("figma_token", "figma_email", "figma_handle"):
        integ.pop(key, None)
    await _save_integrations(user["_id"], integ)
    user["integrations"] = integ
    return {"connected": False}


def get_user_figma_token(user: dict[str, Any]) -> str | None:
    return decrypt_secret(_integrations(user).get("figma_token"))


# ── OpenAI ──

def openai_status(user: dict[str, Any]) -> dict:
    integ = _integrations(user)
    if not decrypt_secret(integ.get("openai_api_key")):
        return {"connected": False}
    return {"connected": True, "model": settings.openai_model}


async def connect_openai_for_user(user: dict[str, Any], api_key: str) -> dict:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("OpenAI API key is required")
    if not api_key.startswith("sk-"):
        raise ValueError("Invalid OpenAI API key format (should start with sk-)")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{OPENAI_API_BASE}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    if response.status_code == 401:
        raise ValueError("Invalid OpenAI API key.")
    if response.status_code != 200:
        raise ValueError(f"OpenAI API error: {response.status_code}")

    integ = _integrations(user)
    integ["openai_api_key"] = encrypt_secret(api_key)
    await _save_integrations(user["_id"], integ)
    user["integrations"] = integ
    return openai_status(user)


async def disconnect_openai_for_user(user: dict[str, Any]) -> dict:
    integ = _integrations(user)
    integ.pop("openai_api_key", None)
    await _save_integrations(user["_id"], integ)
    user["integrations"] = integ
    return {"connected": False}


def get_user_openai_key(user: dict[str, Any]) -> str | None:
    return decrypt_secret(_integrations(user).get("openai_api_key"))


# ── Gmail ──

def gmail_status(user: dict[str, Any]) -> dict:
    integ = _integrations(user)
    method = integ.get("gmail_method")
    if method == "oauth" and decrypt_secret(integ.get("gmail_oauth_json")):
        return {
            "connected": True,
            "method": "oauth",
            "email": integ.get("gmail_email") or "",
        }
    if (
        method == "smtp"
        and integ.get("gmail_smtp_email")
        and decrypt_secret(integ.get("gmail_smtp_password"))
    ):
        return {
            "connected": True,
            "method": "smtp",
            "email": integ.get("gmail_smtp_email") or "",
        }
    return {"connected": False, "method": None}


def user_gmail_connected(user: dict[str, Any]) -> bool:
    return bool(gmail_status(user).get("connected"))


async def connect_gmail_smtp_for_user(user: dict[str, Any], email: str, app_password: str) -> dict:
    import smtplib

    email = email.strip()
    app_password = app_password.strip().replace(" ", "")
    if not email or not app_password:
        raise ValueError("Gmail address and app password are required")
    if len(app_password) != 16:
        raise ValueError(
            "App passwords are exactly 16 characters (no spaces). "
            "Create one at https://myaccount.google.com/apppasswords."
        )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(email, app_password)
    except smtplib.SMTPAuthenticationError as e:
        raise ValueError(
            "Gmail authentication failed. Try Connect with Google instead. "
            f"(Google error: {str(e)[:120]})"
        )
    except Exception as e:
        raise ValueError(f"Could not connect to Gmail: {e}")

    integ = _integrations(user)
    integ.update({
        "gmail_method": "smtp",
        "gmail_smtp_email": email,
        "gmail_smtp_password": encrypt_secret(app_password),
        "gmail_email": email,
        "gmail_oauth_json": None,
    })
    await _save_integrations(user["_id"], integ)
    user["integrations"] = integ
    return gmail_status(user)


async def save_gmail_oauth_for_user(user_id: str, creds: Credentials, email: str = "") -> dict:
    if not email:
        try:
            service = build("oauth2", "v2", credentials=creds)
            email = service.userinfo().get().execute().get("email", "")
        except Exception:
            email = ""

    integ: dict[str, Any] = {}
    user = await get_db().users.find_one({"_id": ObjectId(user_id)})
    if user:
        integ = _integrations(user)

    integ.update({
        "gmail_method": "oauth",
        "gmail_oauth_json": encrypt_secret(creds.to_json()),
        "gmail_email": email,
        "gmail_smtp_email": None,
        "gmail_smtp_password": None,
    })
    await _save_integrations(user_id, integ)
    return {
        "connected": True,
        "method": "oauth",
        "email": email,
    }


async def disconnect_gmail_for_user(user: dict[str, Any]) -> dict:
    integ = _integrations(user)
    for key in (
        "gmail_method",
        "gmail_oauth_json",
        "gmail_email",
        "gmail_smtp_email",
        "gmail_smtp_password",
    ):
        integ.pop(key, None)
    await _save_integrations(user["_id"], integ)
    user["integrations"] = integ
    return {"connected": False, "method": None}


def get_user_gmail_credentials(user: dict[str, Any]) -> Credentials | None:
    integ = _integrations(user)
    raw = decrypt_secret(integ.get("gmail_oauth_json"))
    if not raw:
        return None
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
        creds = Credentials.from_authorized_user_info(info)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return creds if creds.valid else None
    except Exception:
        return None


def send_email_as_user(
    user: dict[str, Any],
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    *,
    from_name: str | None = None,
    reply_to: str | None = None,
    include_list_unsubscribe: bool = True,
) -> dict:
    """Send outreach email using the signed-in user's Gmail integration."""
    import base64
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr, make_msgid

    integ = _integrations(user)
    method = integ.get("gmail_method")

    def _build_message(sender: str) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        display = (from_name or "").strip()
        msg["From"] = formataddr((display, sender)) if display else sender
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Reply-To"] = (reply_to or sender).strip()
        msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else "localhost")
        if include_list_unsubscribe:
            unsub = (reply_to or sender).strip()
            msg["List-Unsubscribe"] = f"<mailto:{unsub}?subject=unsubscribe>"
            msg["List-Id"] = f"Chappie Outreach <outreach.{unsub.split('@')[-1] if '@' in unsub else 'local'}>"
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg

    if method == "oauth":
        creds = get_user_gmail_credentials(user)
        if not creds:
            raise ValueError("Gmail not connected. Click Connect with Google in Integrations.")
        sender = integ.get("gmail_email") or settings.studio_email
        msg = _build_message(sender)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service = build("gmail", "v1", credentials=creds)
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {
            "sent": True,
            "to": to_email,
            "from": sender,
            "from_name": (from_name or "").strip() or None,
            "method": "oauth",
        }

    if method == "smtp":
        email = integ.get("gmail_smtp_email")
        password = decrypt_secret(integ.get("gmail_smtp_password"))
        if not email or not password:
            raise ValueError("Gmail not connected.")
        msg = _build_message(email)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(email, password)
            server.sendmail(email, [to_email], msg.as_string())
        return {
            "sent": True,
            "to": to_email,
            "from": email,
            "from_name": (from_name or "").strip() or None,
            "method": "smtp",
        }

    raise ValueError("Gmail not connected. Open Integrations and connect Gmail.")
