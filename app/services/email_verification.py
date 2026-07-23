import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.services.auth_service import create_user_from_hash, hash_password
from app.services.db import get_db
from app.services.gmail_sender import is_gmail_connected, send_gmail_message

VERIFY_TOKEN_HOURS = 24


async def start_email_signup(
    email: str,
    password: str,
    name: str = "",
    pending_plan: str | None = None,
) -> dict[str, Any]:
    """Store a pending signup and send a verification email. Does not create the user yet."""
    db = get_db()
    email_norm = email.strip().lower()

    existing = await db.users.find_one({"email": email_norm})
    if existing:
        raise ValueError("An account with this email already exists.")

    if not is_gmail_connected():
        raise ValueError(
            "Email verification is temporarily unavailable. "
            "Please try again shortly, or contact IS Studio."
        )

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=VERIFY_TOKEN_HOURS)

    await db.pending_signups.update_one(
        {"email": email_norm},
        {
            "$set": {
                "email": email_norm,
                "password_hash": hash_password(password),
                "name": name.strip(),
                "token": token,
                "pending_plan": pending_plan,
                "created_at": now,
                "expires_at": expires_at,
            }
        },
        upsert=True,
    )

    import html as html_lib

    verify_url = f"{settings.app_base_url.rstrip('/')}/api/auth/verify?token={token}"
    raw_name = name.strip() or "there"
    display_name = html_lib.escape(raw_name)
    studio_raw = settings.studio_name or "IS Studio"
    studio = html_lib.escape(studio_raw)
    safe_href = html_lib.escape(verify_url, quote=True)
    safe_url_text = html_lib.escape(verify_url)

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#0c1118;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0c1118;padding:40px 16px;">
    <tr>
      <td align="center">
        <table width="100%" style="max-width:480px;background:#172231;border:1px solid #2a3a4f;border-radius:14px;padding:32px;">
          <tr>
            <td style="color:#eef3f8;">
              <p style="margin:0 0 8px;color:#3d9a7a;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;font-weight:600;">CH4PP!3</p>
              <h1 style="margin:0 0 12px;font-size:28px;font-weight:600;">Verify your email</h1>
              <p style="margin:0 0 24px;color:#8fa0b5;line-height:1.5;font-size:15px;">
                Hi {display_name}, thanks for signing up. Click the button below to verify your email and create your CH4PP!3 account.
              </p>
              <p style="margin:0 0 28px;text-align:center;">
                <a href="{safe_href}"
                   style="display:inline-block;background:#3d9a7a;color:#04110c;text-decoration:none;font-weight:700;padding:14px 28px;border-radius:999px;font-size:15px;">
                  Verify email
                </a>
              </p>
              <p style="margin:0 0 12px;color:#8fa0b5;font-size:13px;line-height:1.5;">
                This link expires in {VERIFY_TOKEN_HOURS} hours. If you did not sign up for CH4PP!3, you can ignore this email.
              </p>
              <p style="margin:0;color:#8fa0b5;font-size:12px;">
                Or paste this link into your browser:<br>
                <a href="{safe_href}" style="color:#c4a35a;word-break:break-all;">{safe_url_text}</a>
              </p>
              <p style="margin:24px 0 0;color:#8fa0b5;font-size:12px;">{studio}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    plain = (
        f"Hi {raw_name},\n\n"
        f"Verify your email to create your CH4PP!3 account:\n{verify_url}\n\n"
        f"This link expires in {VERIFY_TOKEN_HOURS} hours.\n\n{studio_raw}"
    )

    try:
        send_gmail_message(
            to_email=email_norm,
            subject="Verify your CH4PP!3 account",
            body=html,
            from_email=settings.studio_email,
            html=True,
        )
    except ValueError:
        # Fallback plain text if HTML send path fails unexpectedly
        send_gmail_message(
            to_email=email_norm,
            subject="Verify your CH4PP!3 account",
            body=plain,
            from_email=settings.studio_email,
            html=False,
        )

    return {
        "pending": True,
        "email": email_norm,
        "message": "Check your email for a verification link to finish creating your account.",
    }


async def complete_email_signup(token: str) -> dict[str, Any]:
    """Create the user account after the verification link is clicked."""
    if not token or not token.strip():
        raise ValueError("Invalid verification link.")

    db = get_db()
    pending = await db.pending_signups.find_one({"token": token.strip()})
    if not pending:
        raise ValueError("This verification link is invalid or has already been used.")

    expires_at = pending.get("expires_at")
    if expires_at:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            await db.pending_signups.delete_one({"_id": pending["_id"]})
            raise ValueError("This verification link has expired. Please sign up again.")

    email = pending["email"]
    existing = await db.users.find_one({"email": email})
    if existing:
        await db.pending_signups.delete_one({"_id": pending["_id"]})
        raise ValueError("An account with this email already exists. Please sign in.")

    user = await create_user_from_hash(
        email=email,
        password_hash=pending["password_hash"],
        name=pending.get("name") or "",
    )
    await db.pending_signups.delete_one({"_id": pending["_id"]})
    user["_pending_plan"] = pending.get("pending_plan")
    return user
