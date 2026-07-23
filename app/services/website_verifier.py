"""Verify that Google's 'no website' signal is real — HTTP, redirects, social-only sites."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.models import Business

VERIFY_CONCURRENCY = 8
REQUEST_TIMEOUT = 6.0

# Platforms that count as "social presence", not a real business website
SOCIAL_HOSTS = {
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "fb.me",
    "instagram.com", "www.instagram.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "linkedin.com", "www.linkedin.com",
    "tiktok.com", "www.tiktok.com",
    "youtube.com", "www.youtube.com", "youtu.be",
    "yelp.com", "www.yelp.com",
    "tripadvisor.com", "www.tripadvisor.com",
    "google.com", "maps.google.com", "g.page",
    "linktr.ee", "linktree.com",
    "wa.me", "api.whatsapp.com",
}

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.ca", "hotmail.com",
    "outlook.com", "live.com", "icloud.com", "me.com", "aol.com",
    "protonmail.com", "proton.me", "mail.com", "gmx.com",
}

URL_RE = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", re.I)


def _host(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return (parsed.hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _is_social_host(host: str) -> bool:
    h = host.lower().removeprefix("www.")
    if h in {x.removeprefix("www.") for x in SOCIAL_HOSTS}:
        return True
    return any(h.endswith(f".{s.removeprefix('www.')}") for s in ("facebook.com", "instagram.com"))


def _normalize_url(raw: str) -> str | None:
    raw = (raw or "").strip().rstrip(".,);]")
    if not raw:
        return None
    if raw.startswith("www."):
        raw = "https://" + raw
    if not raw.startswith(("http://", "https://")):
        return None
    host = _host(raw)
    if not host or "." not in host:
        return None
    return raw


def _extract_urls_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    found = []
    for match in URL_RE.findall(text):
        url = _normalize_url(match)
        if url:
            found.append(url)
    return found


def _email_domains(business: Business) -> list[str]:
    emails = list(business.contact_emails or [])
    if business.contact_email:
        emails.insert(0, business.contact_email)
    domains = []
    for email in emails:
        if "@" not in email:
            continue
        domain = email.split("@", 1)[1].strip().lower()
        if domain and domain not in FREE_EMAIL_DOMAINS:
            domains.append(domain)
    return list(dict.fromkeys(domains))


def collect_candidates(business: Business) -> tuple[list[str], list[str]]:
    """Return (website_candidates, social_urls)."""
    websites: list[str] = []
    socials: list[str] = []

    if business.appointment_url:
        url = _normalize_url(business.appointment_url)
        if url:
            (socials if _is_social_host(_host(url)) else websites).append(url)

    for platform, raw in (business.social_profiles or {}).items():
        url = _normalize_url(raw)
        if not url:
            continue
        host = _host(url)
        if _is_social_host(host) or platform.lower() in {
            "facebook", "instagram", "twitter", "linkedin", "tiktok", "youtube"
        }:
            socials.append(url)
        else:
            websites.append(url)

    for url in _extract_urls_from_text(business.description):
        (socials if _is_social_host(_host(url)) else websites).append(url)

    for domain in _email_domains(business):
        websites.append(f"https://{domain}")
        websites.append(f"http://{domain}")

    # Dedupe preserving order
    def uniq(items: list[str]) -> list[str]:
        seen = set()
        out = []
        for u in items:
            key = _host(u) + urlparse(u).path.rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            out.append(u)
        return out

    return uniq(websites), uniq(socials)


async def _probe_url(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        response = await client.get(url, follow_redirects=True)
        final = str(response.url)
        final_host = _host(final)
        ok = response.status_code < 400
        return {
            "url": url,
            "final_url": final,
            "status": response.status_code,
            "ok": ok,
            "is_social": _is_social_host(final_host),
            "host": final_host,
        }
    except Exception as e:
        return {
            "url": url,
            "final_url": None,
            "status": None,
            "ok": False,
            "is_social": _is_social_host(_host(url)),
            "host": _host(url),
            "error": str(e)[:120],
        }


def _score_from_probes(
    business: Business,
    website_probes: list[dict],
    social_urls: list[str],
) -> dict[str, Any]:
    signals: list[str] = []
    score = 72  # Google Places listed no websiteUri

    signals.append("google_maps_no_website")

    live_sites = [
        p for p in website_probes
        if p.get("ok") and not p.get("is_social")
    ]
    social_redirects = [
        p for p in website_probes
        if p.get("ok") and p.get("is_social")
    ]
    dead_sites = [p for p in website_probes if not p.get("ok")]

    suspected = None
    status = "verified_none"

    if live_sites:
        # Real independent website found — weak "no website" claim
        best = live_sites[0]
        suspected = best.get("final_url") or best.get("url")
        score = 18
        status = "suspected_site"
        signals.append(f"live_website:{suspected}")
        if best.get("url") != best.get("final_url"):
            signals.append(f"redirect:{best.get('url')}→{best.get('final_url')}")
    elif social_redirects:
        suspected = social_redirects[0].get("final_url")
        score = 58
        status = "social_only"
        signals.append("candidate_url_redirects_to_social")
    elif social_urls and not live_sites:
        score = 78
        status = "social_only"
        signals.append("social_presence_only")
        # Prefer noting Facebook if present
        fb = next((u for u in social_urls if "facebook" in u.lower()), None)
        if fb:
            signals.append("facebook_only_site_pattern")
            suspected = fb
    else:
        score = 88
        status = "verified_none"
        signals.append("no_live_website_found")

    if dead_sites and status != "suspected_site":
        signals.append(f"dead_or_unreachable_candidates:{len(dead_sites)}")
        score = min(100, score + 4)

    if business.phone:
        score = min(100, score + 2)
        signals.append("has_phone")

    # Clamp
    score = max(0, min(100, int(score)))

    if score >= 80:
        label = "Verified"
        tier = "high"
    elif score >= 55:
        label = "Likely"
        tier = "medium"
    elif score >= 30:
        label = "Uncertain"
        tier = "low"
    else:
        label = "Has website?"
        tier = "fail"

    return {
        "no_website_score": score,
        "no_website_label": label,
        "no_website_tier": tier,
        "no_website_status": status,
        "no_website_signals": signals[:8],
        "suspected_website": suspected,
    }


async def verify_business(client: httpx.AsyncClient, business: Business) -> Business:
    websites, socials = collect_candidates(business)
    # Cap probes for speed
    to_probe = websites[:4]
    probes = []
    if to_probe:
        probes = await asyncio.gather(*[_probe_url(client, u) for u in to_probe])

    result = _score_from_probes(business, list(probes), socials)
    data = business.model_dump()
    data.update(result)
    # Keep has_website False for leads; flag suspicion separately
    if result["no_website_status"] == "suspected_site":
        data["has_website"] = False  # still show as lead but low score
    return Business(**data)


async def verify_businesses_no_website(
    businesses: list[Business],
    progress_callback=None,
) -> list[Business]:
    if not businesses:
        return []

    total = len(businesses)
    verified: list[Business | None] = [None] * total
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)
    done = 0
    lock = asyncio.Lock()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; CH4PP!3Bot/1.0; +https://isexperience.house) "
            "AppleWebKit/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
    }

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers=headers,
        follow_redirects=True,
        verify=False,  # many small biz sites have bad certs; still counts as "has site"
    ) as client:

        async def run_one(idx: int, biz: Business) -> None:
            nonlocal done
            async with sem:
                try:
                    if biz.has_website or biz.website_url:
                        data = biz.model_dump()
                        data.update({
                            "has_website": True,
                            "no_website_score": 0,
                            "no_website_label": "Has Website",
                            "no_website_tier": "fail",
                            "no_website_status": "has_website",
                            "no_website_signals": ["google_places_website"],
                            "suspected_website": biz.website_url or biz.suspected_website,
                        })
                        verified[idx] = Business(**data)
                    else:
                        verified[idx] = await verify_business(client, biz)
                except Exception:
                    # Fallback: still mark as google-only verified
                    data = biz.model_dump()
                    if biz.has_website or biz.website_url:
                        data.update({
                            "has_website": True,
                            "no_website_score": 0,
                            "no_website_label": "Has Website",
                            "no_website_tier": "fail",
                            "no_website_status": "has_website",
                            "no_website_signals": ["google_places_website"],
                        })
                    else:
                        data.update({
                            "no_website_score": 70,
                            "no_website_label": "Likely",
                            "no_website_tier": "medium",
                            "no_website_status": "unknown",
                            "no_website_signals": ["google_maps_no_website", "verify_error"],
                            "suspected_website": None,
                        })
                    verified[idx] = Business(**data)
            async with lock:
                done += 1
                if progress_callback:
                    await progress_callback(
                        done,
                        total,
                        f"Verifying no-website score ({done}/{total})...",
                    )

        await asyncio.gather(*[run_one(i, b) for i, b in enumerate(businesses)])

    results = [b for b in verified if b is not None]
    # Highest confidence no-website first
    results.sort(key=lambda b: (b.no_website_score or 0), reverse=True)

    if progress_callback:
        high = sum(1 for b in results if (b.no_website_score or 0) >= 80)
        await progress_callback(
            total,
            total,
            f"Verified {high}/{total} high-confidence no-website leads",
        )

    return results
