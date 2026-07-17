"""Website Opportunity Score — prioritize digital-service / website sales leads.

Weights (0–100, clamped):
  No website                    +35
  Website not mobile-friendly   +20
  Outdated design               +15
  Slow loading                  +10
  Missing online booking         +8
  Strong Google reviews          +7
  Verified email found           +5

Also emits boolean `website_flags` used by advanced search filters.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable, Awaitable
from urllib.parse import urlparse

import httpx

from app.models import Business

ProgressCallback = Callable[[int, int, str], Awaitable[None] | None]

PROBE_CONCURRENCY = 6
PROBE_TIMEOUT = 5.0
SLOW_SECONDS = 2.5

WEIGHTS = {
    "no_website": 35,
    "not_mobile": 20,
    "outdated": 15,
    "slow": 10,
    "missing_booking": 8,
    "strong_reviews": 7,
    "verified_email": 5,
}

BOOKING_HINTS = re.compile(
    r"book\s*(online|now|a\s*table|an?\s*appointment)|appointments?|"
    r"reservations?|calendly|squareup\.com/appointments|mindbody|"
    r"acuityscheduling|setmore|schedulicity|opentable|resy|"
    r"booksy|vagaro|fresha|styleseat|housecallpro",
    re.I,
)

STORE_HINTS = re.compile(
    r"add\s*to\s*cart|shopify|woocommerce|bigcommerce|squarespace\s*commerce|"
    r"\bcart\b|checkout|buy\s*now|online\s*store|e-?commerce|shop\s*now|"
    r"myshopify\.com|stripe\.com/docs/payments",
    re.I,
)

GENERIC_EMAIL_LOCALS = {
    "info", "hello", "contact", "office", "admin", "support", "sales",
    "team", "mail", "booking", "bookings", "reservations", "enquiry",
    "enquiries", "inquiry", "inquiries", "service", "services", "help",
    "customerservice", "customer", "noreply", "no-reply", "donotreply",
}

OUTDATED_HINTS = [
    (re.compile(r"<frameset\b", re.I), "frameset"),
    (re.compile(r"application/x-shockwave-flash|\.swf\b", re.I), "flash"),
    (re.compile(r"jquery[-.](1\.[0-9]|2\.[0-2])\b", re.I), "old_jquery"),
    (re.compile(r"generator\"\s+content=\"WordPress\s+([0-3]\.|4\.[0-5])", re.I), "old_wordpress"),
    (re.compile(r"<table[^>]*(?:width\s*=\s*[\"']?\d{3,}|layout)", re.I), "table_layout"),
    (re.compile(r"copyright\s*(?:©|&copy;)?\s*(19\d{2}|200\d|201[0-8])\b", re.I), "old_copyright"),
]

ProgressFn = Callable[..., Any]

EMPTY_FLAGS = {
    "no_website": False,
    "outdated_website": False,
    "not_mobile_friendly": False,
    "slow_website": False,
    "missing_https": False,
    "missing_booking": False,
    "missing_online_store": False,
    "has_email": False,
    "has_decision_maker": False,
}


def _emails(business: Business) -> list[str]:
    emails = list(business.contact_emails or [])
    if business.contact_email and business.contact_email not in emails:
        emails.insert(0, business.contact_email)
    return [e for e in emails if e]


def _looks_like_person_email(email: str) -> bool:
    local = (email or "").split("@", 1)[0].lower().strip()
    if not local or local in GENERIC_EMAIL_LOCALS:
        return False
    # john.smith / jane_doe / first.last
    if re.match(r"^[a-z]{2,}[._-][a-z]{2,}", local):
        return True
    # single first-name style (avoid ultra-short)
    if re.match(r"^[a-z]{3,16}$", local) and local not in GENERIC_EMAIL_LOCALS:
        return True
    return False


def _site_url(business: Business) -> str | None:
    for raw in (business.website_url, business.suspected_website, business.appointment_url):
        url = (raw or "").strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        host = urlparse(url).hostname or ""
        if not host or "." not in host:
            continue
        social = (
            "facebook.com", "instagram.com", "twitter.com", "x.com",
            "linkedin.com", "tiktok.com", "youtube.com", "yelp.com",
            "linktr.ee", "wa.me",
        )
        h = host.lower().removeprefix("www.")
        if any(h == s or h.endswith("." + s) for s in social):
            continue
        return url
    return None


def _has_real_website(business: Business) -> bool:
    if business.no_website_status in ("verified_none", "social_only"):
        return False
    if business.no_website_status == "suspected_site" and business.suspected_website:
        return True
    if business.has_website and business.website_url:
        return True
    if business.website_url and (business.no_website_score or 100) < 40:
        return True
    return False


def _score_reviews(business: Business) -> tuple[int, str | None]:
    count = int(business.review_count or 0)
    rating = float(business.rating or 0)
    if count >= 10 and rating >= 4.0:
        return WEIGHTS["strong_reviews"], f"Strong Google reviews: +{WEIGHTS['strong_reviews']}"
    if count >= 25 and rating >= 3.7:
        return WEIGHTS["strong_reviews"], f"Strong Google reviews: +{WEIGHTS['strong_reviews']}"
    return 0, None


def _score_email(business: Business) -> tuple[int, str | None]:
    if _emails(business):
        return WEIGHTS["verified_email"], f"Verified email found: +{WEIGHTS['verified_email']}"
    return 0, None


def _has_booking(business: Business, html: str | None) -> bool:
    if business.appointment_url:
        return True
    if html and BOOKING_HINTS.search(html):
        return True
    return False


def _has_online_store(html: str | None) -> bool:
    if not html:
        return False
    return bool(STORE_HINTS.search(html))


def _contact_flags(business: Business) -> dict[str, bool]:
    emails = _emails(business)
    return {
        "has_email": bool(emails),
        "has_decision_maker": any(_looks_like_person_email(e) for e in emails),
    }


def _score_booking(business: Business, html: str | None) -> tuple[int, str | None]:
    if _has_booking(business, html):
        return 0, None
    return WEIGHTS["missing_booking"], f"Missing online booking: +{WEIGHTS['missing_booking']}"


def _analyze_html(html: str, elapsed: float) -> tuple[list[tuple[int, str]], dict[str, Any]]:
    """Return (points_with_labels, probe_meta) for an existing website."""
    awards: list[tuple[int, str]] = []
    meta: dict[str, Any] = {
        "not_mobile": False,
        "outdated": False,
        "slow": False,
        "has_online_store": _has_online_store(html),
    }

    has_viewport = bool(
        re.search(
            r"<meta[^>]+name=[\"']viewport[\"'][^>]*content=[\"'][^\"']*[\"']",
            html,
            re.I,
        )
        or re.search(
            r"<meta[^>]+content=[\"'][^\"']*[\"'][^>]+name=[\"']viewport[\"']",
            html,
            re.I,
        )
    )
    has_media = bool(re.search(r"@media\b", html, re.I))
    if not has_viewport:
        awards.append(
            (WEIGHTS["not_mobile"], f"Website not mobile-friendly: +{WEIGHTS['not_mobile']}")
        )
        meta["not_mobile"] = True
    elif not has_media and not has_viewport:
        awards.append(
            (WEIGHTS["not_mobile"], f"Website not mobile-friendly: +{WEIGHTS['not_mobile']}")
        )
        meta["not_mobile"] = True

    outdated_hits = []
    for pattern, label in OUTDATED_HINTS:
        if pattern.search(html):
            outdated_hits.append(label)
    if outdated_hits:
        awards.append(
            (WEIGHTS["outdated"], f"Outdated design: +{WEIGHTS['outdated']}")
        )
        meta["outdated"] = True
        meta["outdated_signals"] = outdated_hits

    if elapsed >= SLOW_SECONDS:
        awards.append(
            (WEIGHTS["slow"], f"Slow loading: +{WEIGHTS['slow']}")
        )
        meta["slow"] = True
        meta["load_seconds"] = round(elapsed, 2)

    return awards, meta


async def _probe_website(client: httpx.AsyncClient, url: str) -> tuple[str | None, float, dict[str, Any]]:
    started = time.perf_counter()
    try:
        resp = await client.get(
            url,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; ChappieBot/1.0; "
                    "+https://github.com/IS-studio-hub/Chappie)"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        elapsed = time.perf_counter() - started
        final = str(resp.url)
        missing_https = not final.lower().startswith("https://")
        ctype = (resp.headers.get("content-type") or "").lower()
        if resp.status_code >= 400:
            return None, elapsed, {
                "http_status": resp.status_code,
                "unreachable": True,
                "missing_https": missing_https,
                "final_url": final,
            }
        if "text/html" not in ctype and "application/xhtml" not in ctype:
            return "", elapsed, {
                "non_html": True,
                "http_status": resp.status_code,
                "missing_https": missing_https,
                "final_url": final,
            }
        text = resp.text[:180_000]
        return text, elapsed, {
            "http_status": resp.status_code,
            "missing_https": missing_https,
            "final_url": final,
        }
    except Exception as e:
        elapsed = time.perf_counter() - started
        missing_https = url.lower().startswith("http://")
        return None, elapsed, {
            "error": str(e)[:120],
            "unreachable": True,
            "missing_https": missing_https,
        }


def _tier(score: int) -> tuple[str, str]:
    if score >= 75:
        return "Hot", "hot"
    if score >= 55:
        return "Warm", "warm"
    if score >= 35:
        return "Cool", "cool"
    return "Low", "low"


def _pack(score: int, breakdown: list[str], flags: dict[str, bool]) -> dict[str, Any]:
    label, tier = _tier(score)
    merged = {**EMPTY_FLAGS, **flags}
    return {
        "website_opportunity_score": score,
        "website_opportunity_label": label,
        "website_opportunity_tier": tier,
        "website_opportunity_signals": breakdown,
        "website_opportunity_breakdown": breakdown,
        "website_flags": merged,
    }


def _score_no_website_path(business: Business) -> dict[str, Any]:
    """No real site → full rebuild opportunity using the same gap weights."""
    points = 0
    breakdown: list[str] = []
    flags = {**EMPTY_FLAGS, **_contact_flags(business)}

    points += WEIGHTS["no_website"]
    breakdown.append(f"No website: +{WEIGHTS['no_website']}")
    flags["no_website"] = True

    points += WEIGHTS["not_mobile"]
    breakdown.append(f"Website not mobile-friendly: +{WEIGHTS['not_mobile']}")
    flags["not_mobile_friendly"] = True

    points += WEIGHTS["outdated"]
    breakdown.append(f"Outdated design: +{WEIGHTS['outdated']}")
    flags["outdated_website"] = True

    points += WEIGHTS["slow"]
    breakdown.append(f"Slow loading: +{WEIGHTS['slow']}")
    flags["slow_website"] = True

    flags["missing_https"] = True
    flags["missing_online_store"] = True

    pts, label = _score_booking(business, None)
    if pts:
        points += pts
        breakdown.append(label)
        flags["missing_booking"] = True
    else:
        flags["missing_booking"] = False

    pts, label = _score_reviews(business)
    if pts:
        points += pts
        breakdown.append(label)

    pts, label = _score_email(business)
    if pts:
        points += pts
        breakdown.append(label)

    return _pack(max(0, min(100, points)), breakdown, flags)


async def _score_with_site(
    business: Business,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    points = 0
    breakdown: list[str] = []
    flags = {**EMPTY_FLAGS, **_contact_flags(business)}
    url = _site_url(business)

    html = None
    elapsed = 0.0
    probe_meta: dict[str, Any] = {}

    if url:
        html, elapsed, probe_meta = await _probe_website(client, url)
        flags["missing_https"] = bool(probe_meta.get("missing_https"))

        if probe_meta.get("unreachable"):
            points += WEIGHTS["no_website"]
            breakdown.append(f"Unreachable website: +{WEIGHTS['no_website']}")
            flags["no_website"] = True
            flags["not_mobile_friendly"] = True
            flags["outdated_website"] = True
            flags["slow_website"] = True
            flags["missing_online_store"] = True
            points += WEIGHTS["not_mobile"]
            breakdown.append(f"Website not mobile-friendly: +{WEIGHTS['not_mobile']}")
            points += WEIGHTS["outdated"]
            breakdown.append(f"Outdated design: +{WEIGHTS['outdated']}")
            points += WEIGHTS["slow"]
            breakdown.append(f"Slow loading: +{WEIGHTS['slow']}")
        else:
            awards, meta = _analyze_html(html or "", elapsed)
            probe_meta.update(meta)
            flags["not_mobile_friendly"] = bool(meta.get("not_mobile"))
            flags["outdated_website"] = bool(meta.get("outdated"))
            flags["slow_website"] = bool(meta.get("slow"))
            flags["missing_online_store"] = not bool(meta.get("has_online_store"))

            if html is None or (html == "" and probe_meta.get("non_html")):
                points += WEIGHTS["outdated"]
                breakdown.append(f"Outdated design: +{WEIGHTS['outdated']}")
                flags["outdated_website"] = True
            for pts, label in awards:
                points += pts
                breakdown.append(label)
    else:
        points += WEIGHTS["outdated"]
        breakdown.append(f"Outdated design: +{WEIGHTS['outdated']}")
        flags["outdated_website"] = True
        flags["missing_online_store"] = True
        flags["missing_https"] = True

    pts, label = _score_booking(business, html)
    if pts:
        points += pts
        breakdown.append(label)
        flags["missing_booking"] = True
    else:
        flags["missing_booking"] = False

    pts, label = _score_reviews(business)
    if pts:
        points += pts
        breakdown.append(label)

    pts, label = _score_email(business)
    if pts:
        points += pts
        breakdown.append(label)

    return _pack(max(0, min(100, points)), breakdown, flags)


def score_website_opportunity_sync(business: Business) -> dict[str, Any]:
    """Sync path without HTTP (uses presence signals only)."""
    if not _has_real_website(business):
        return _score_no_website_path(business)

    points = 0
    breakdown: list[str] = []
    flags = {**EMPTY_FLAGS, **_contact_flags(business)}
    flags["missing_online_store"] = True  # unknown without probe

    url = _site_url(business) or ""
    flags["missing_https"] = url.lower().startswith("http://")

    pts, label = _score_booking(business, None)
    if pts:
        points += pts
        breakdown.append(label)
        flags["missing_booking"] = True

    pts, label = _score_reviews(business)
    if pts:
        points += pts
        breakdown.append(label)

    pts, label = _score_email(business)
    if pts:
        points += pts
        breakdown.append(label)

    points += 10
    breakdown.append("Site quality unchecked: +10")
    return _pack(max(0, min(100, points)), breakdown, flags)


async def score_website_opportunities(
    businesses: list[Business],
    progress_callback: ProgressFn | None = None,
) -> list[Business]:
    if not businesses:
        return businesses

    total = len(businesses)
    sem = asyncio.Semaphore(PROBE_CONCURRENCY)
    done = 0

    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        async def one(biz: Business) -> Business:
            nonlocal done
            async with sem:
                if _has_real_website(biz):
                    result = await _score_with_site(biz, client)
                else:
                    result = _score_no_website_path(biz)
            data = biz.model_dump()
            data.update(result)
            done += 1
            if progress_callback:
                maybe = progress_callback(
                    done,
                    total,
                    f"Scoring website opportunities… {done}/{total}",
                )
                if asyncio.iscoroutine(maybe):
                    await maybe
            return Business(**data)

        scored = await asyncio.gather(*[one(b) for b in businesses])

    ranked = list(scored)
    ranked.sort(
        key=lambda b: (
            b.website_opportunity_score or 0,
            b.lead_quality_score or 0,
            b.review_count or 0,
        ),
        reverse=True,
    )
    return ranked
