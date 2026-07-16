import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings
from app.services import gmail_oauth

_gmail_user: str | None = None
_gmail_app_password: str | None = None


def is_gmail_connected() -> bool:
    return gmail_oauth.is_gmail_oauth_connected() or bool(_gmail_user and _gmail_app_password)


def get_gmail_status() -> dict:
    oauth_status = gmail_oauth.get_gmail_oauth_status()
    if oauth_status.get("connected"):
        return oauth_status

    if _gmail_user and _gmail_app_password:
        return {"connected": True, "method": "smtp", "email": _gmail_user}

    return {"connected": False, "method": None}


def connect_gmail(email: str, app_password: str) -> dict:
    global _gmail_user, _gmail_app_password

    email = email.strip()
    app_password = app_password.strip().replace(" ", "")

    if not email or not app_password:
        raise ValueError("Gmail address and app password are required")

    if len(app_password) != 16:
        raise ValueError(
            "App passwords are exactly 16 characters (no spaces). "
            "Create one at https://myaccount.google.com/apppasswords while "
            f"signed in as {email}."
        )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(email, app_password)
    except smtplib.SMTPAuthenticationError as e:
        err = str(e)
        hints = [
            f"Sign in to https://myaccount.google.com/apppasswords as **{email}** (not a different Google account).",
            "Enable 2-Step Verification first, then create a new App Password for 'Mail'.",
            "If using Google Workspace, your admin must allow App Passwords in Admin Console → Security.",
            "Try 'Connect with Google' instead — it works better for Workspace accounts.",
        ]
        raise ValueError(
            "Gmail authentication failed.\n\n"
            + "\n".join(f"• {h}" for h in hints)
            + f"\n\n(Google error: {err[:120]})"
        )
    except Exception as e:
        raise ValueError(f"Could not connect to Gmail: {e}")

    gmail_oauth.disconnect_gmail_oauth()
    _gmail_user = email
    _gmail_app_password = app_password
    return get_gmail_status()


def disconnect_gmail() -> None:
    global _gmail_user, _gmail_app_password
    _gmail_user = None
    _gmail_app_password = None
    gmail_oauth.disconnect_gmail_oauth()


def send_gmail_message(
    to_email: str,
    subject: str,
    body: str,
    from_email: str | None = None,
    *,
    html: bool = False,
) -> dict:
    # Prefer OAuth — reload/refresh from disk if needed
    if gmail_oauth.ensure_gmail_credentials():
        return gmail_oauth.send_via_gmail_api(
            to_email, subject, body, from_email, html=html
        )

    if not _gmail_user or not _gmail_app_password:
        raise ValueError(
            "Gmail not connected. Connect Gmail in the Chappie app so verification emails can be sent."
        )

    sender = from_email or _gmail_user or settings.studio_email

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    subtype = "html" if html else "plain"
    msg.attach(MIMEText(body, subtype, "utf-8"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(_gmail_user, _gmail_app_password)
            server.sendmail(sender, [to_email], msg.as_string())
    except Exception as e:
        raise ValueError(f"Failed to send email: {e}")

    return {"sent": True, "to": to_email, "from": sender, "subject": subject, "method": "smtp"}
