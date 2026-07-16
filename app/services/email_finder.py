import asyncio
import re
from functools import lru_cache

from email_validator import EmailNotValidError, validate_email

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

JUNK_EMAIL_DOMAINS = {
    "sentry.io", "wixpress.com", "example.com", "email.com",
    "domain.com", "yoursite.com", "wordpress.com", "sentry-next.wixpress.com",
    "googleusercontent.com", "schema.org", "w3.org",
    "google.com", "google.ca", "gstatic.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "youtube.com", "tiktok.com", "pinterest.com",
    "duckduckgo.com", "bing.com", "wikipedia.org",
    "cloudflare.com", "jquery.com", "bootstrapcdn.com",
    "gravatar.com", "wp.com", "squarespace.com",
}

JUNK_PREFIXES = ("noreply", "no-reply", "donotreply", "mailer-daemon", "postmaster")


def extract_emails_from_text(text: str) -> list[str]:
    if not text:
        return []
    found = EMAIL_REGEX.findall(text)
    clean = []
    for email in found:
        email = email.lower().strip(".")
        domain = email.split("@")[-1]
        prefix = email.split("@")[0]
        if domain in JUNK_EMAIL_DOMAINS:
            continue
        if any(prefix.startswith(p) for p in JUNK_PREFIXES):
            continue
        if email not in clean:
            clean.append(email)
    return clean


def merge_emails(*groups: list[str] | str | None) -> list[str]:
    """Deduplicate emails while preserving first-seen order."""
    merged: list[str] = []
    for group in groups:
        if not group:
            continue
        items = [group] if isinstance(group, str) else list(group)
        for email in items:
            if not email:
                continue
            cleaned = extract_emails_from_text(str(email))
            candidates = cleaned or ([str(email).lower().strip()] if "@" in str(email) else [])
            for e in candidates:
                domain = e.split("@")[-1]
                prefix = e.split("@")[0]
                if domain in JUNK_EMAIL_DOMAINS:
                    continue
                if any(prefix.startswith(p) for p in JUNK_PREFIXES):
                    continue
                if e not in merged:
                    merged.append(e)
    return merged


def rank_emails(emails: list[str], business_name: str = "") -> list[str]:
    """Return emails sorted best-first for outreach."""
    if not emails:
        return []
    name_parts = re.sub(r"[^a-z0-9]", "", business_name.lower())
    scored = []
    for email in emails:
        score = 0
        local = email.split("@")[0]
        domain = email.split("@")[-1]
        if any(k in local for k in (
            "info", "contact", "hello", "office", "admin", "sales",
            "booking", "inquiry", "operator", "franchise", "partner",
        )):
            score += 3
        if any(domain.endswith(ext) for ext in (".ca", ".com", ".net", ".org")):
            score += 1
        if name_parts and (name_parts[:6] in local or name_parts[:6] in domain.replace(".", "")):
            score += 2
        if local in ("info", "contact", "hello"):
            score += 1
        if any(k in local for k in ("media", "press", "investor", "noreply")):
            score -= 2
        scored.append((score, email))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, e in scored]


def pick_best_email(emails: list[str], business_name: str = "") -> str | None:
    ranked = rank_emails(emails, business_name)
    return ranked[0] if ranked else None


def _base_email_ok(email: str) -> bool:
    raw = (email or "").strip().lower()
    if not raw or "@" not in raw:
        return False
    domain = raw.split("@")[-1]
    prefix = raw.split("@")[0]
    if domain in JUNK_EMAIL_DOMAINS:
        return False
    if any(prefix.startswith(p) for p in JUNK_PREFIXES):
        return False
    return True


@lru_cache(maxsize=2048)
def verify_email_syntax(email: str) -> bool:
    """Junk + syntax check (no MX). Cached by address."""
    if not _base_email_ok(email):
        return False
    raw = email.strip().lower()
    try:
        validate_email(raw, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


@lru_cache(maxsize=2048)
def verify_email_deliverable(email: str) -> bool:
    """Syntax + MX/deliverability check. Cached by address."""
    if not _base_email_ok(email):
        return False
    raw = email.strip().lower()
    try:
        validate_email(raw, check_deliverability=True)
        return True
    except EmailNotValidError:
        return False
    except Exception:
        # Network/DNS hiccup — fall back to syntax-only
        return verify_email_syntax(raw)


async def filter_verified_emails(emails: list[str]) -> list[str]:
    """Keep only deliverable emails (MX-verified), preserving order."""
    if not emails:
        return []
    unique = merge_emails(emails)
    checks = await asyncio.gather(*[
        asyncio.to_thread(verify_email_deliverable, e) for e in unique
    ])
    return [e for e, ok in zip(unique, checks) if ok]


async def filter_usable_emails(emails: list[str]) -> tuple[list[str], bool]:
    """Prefer MX-verified; fall back to syntax-valid. Returns (emails, mx_verified)."""
    if not emails:
        return [], False
    verified = await filter_verified_emails(emails)
    if verified:
        return verified, True
    unique = merge_emails(emails)
    checks = await asyncio.gather(*[
        asyncio.to_thread(verify_email_syntax, e) for e in unique
    ])
    usable = [e for e, ok in zip(unique, checks) if ok]
    return usable, False


def collect_business_emails(business) -> list[str]:
    return merge_emails(
        getattr(business, "contact_emails", None),
        getattr(business, "contact_email", None),
    )


async def keep_businesses_with_verified_emails(businesses: list) -> list:
    """Keep leads with a usable contact email (MX preferred, syntax fallback)."""
    from app.models import Business

    kept: list[Business] = []
    for biz in businesses:
        candidates = collect_business_emails(biz)
        if not candidates:
            continue
        usable, mx_ok = await filter_usable_emails(candidates)
        if not usable:
            continue
        source = biz.email_source or "found"
        tag = "+verified" if mx_ok else "+syntax"
        if tag not in source:
            source = f"{source}{tag}"
        kept.append(biz.model_copy(update={
            "contact_emails": usable,
            "contact_email": usable[0],
            "email_source": source,
        }))
    return kept
