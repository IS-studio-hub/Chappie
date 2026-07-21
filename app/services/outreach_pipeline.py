import asyncio
import re
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from playwright.async_api import async_playwright

from app.models import Business
from app.services.email_finder import extract_emails_from_text, merge_emails, pick_best_email, rank_emails
from app.services.social_discovery import (
    detect_social_platform,
    merge_social_profiles,
    normalize_social_url,
    scrape_social_page_for_emails,
)

WORKER_COUNT = 8
PER_BUSINESS_TIMEOUT_SEC = 25
PAGE_GOTO_TIMEOUT_MS = 6000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Common contact paths to try after the homepage (Large plan with-website leads)
_WEBSITE_CONTACT_PATHS = (
    "",
    "/contact",
    "/contact-us",
    "/contactus",
    "/about",
    "/about-us",
    "/aboutus",
)


def _city(business: Business) -> str:
    if not business.address:
        return ""
    parts = [p.strip() for p in business.address.split(",")]
    if len(parts) >= 3:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


def _normalize_website_url(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None


async def _http_scrape_website_emails(business: Business) -> list[str]:
    """Scrape the business website (homepage + contact/about) for emails via HTTP."""
    base = _normalize_website_url(business.website_url)
    if not base:
        return []

    found: list[str] = []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
        for path in _WEBSITE_CONTACT_PATHS:
            url = urljoin(base + "/", path.lstrip("/")) if path else base
            try:
                r = await client.get(url)
                if r.status_code >= 400:
                    continue
                ctype = (r.headers.get("content-type") or "").lower()
                if "html" not in ctype and "text" not in ctype and ctype:
                    continue
                text = r.text or ""
                for e in extract_emails_from_text(text):
                    if e not in found:
                        found.append(e)
                for m in re.finditer(r"mailto:([^\"'\\s?>]+)", text, re.I):
                    for e in extract_emails_from_text(m.group(1)):
                        if e not in found:
                            found.append(e)
                if found:
                    break
            except Exception:
                continue
    return rank_emails(found, business.name)


async def _playwright_scrape_website_emails(page, business: Business) -> list[str]:
    """Fallback when HTTP is blocked (403/JS sites) — scrape site in a browser."""
    base = _normalize_website_url(business.website_url)
    if not base or page is None:
        return []
    found: list[str] = []
    for path in ("", "/contact", "/contact-us", "/about"):
        url = urljoin(base + "/", path.lstrip("/")) if path else base
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1800)
            html = await page.content()
            body = await page.locator("body").inner_text()
            for e in extract_emails_from_text(html + "\n" + body):
                if e not in found:
                    found.append(e)
            mailtos = page.locator('a[href^="mailto:"]')
            count = await mailtos.count()
            for i in range(min(count, 8)):
                href = await mailtos.nth(i).get_attribute("href") or ""
                for e in extract_emails_from_text(href):
                    if e not in found:
                        found.append(e)
            if found:
                break
        except Exception:
            continue
    return rank_emails(found, business.name)


async def _guess_role_emails_from_website(business: Business) -> list[str]:
    """If the site domain has MX, try common role inboxes (info@, contact@, …)."""
    from app.services.email_finder import filter_usable_emails, verify_email_deliverable

    base = _normalize_website_url(business.website_url)
    if not base:
        return []
    host = urlparse(base).netloc.lower().replace("www.", "")
    if not host or "." not in host:
        return []
    # Skip generic hosts / socials
    if any(host.endswith(d) or d in host for d in (
        "facebook.com", "instagram.com", "google.com", "yelp.", "wixsite.com",
        "squarespace.com", "linktr.ee", "bit.ly",
    )):
        return []

    candidates = [
        f"{local}@{host}"
        for local in ("info", "contact", "hello", "office")
    ]
    usable, _mx = await filter_usable_emails(candidates)
    # Prefer MX-verified role inboxes only (avoid inventing junk on catch-all-ish syntax)
    verified = []
    for e in usable:
        try:
            ok = await asyncio.to_thread(verify_email_deliverable, e)
        except Exception:
            ok = False
        if ok:
            verified.append(e)
    return rank_emails(verified, business.name)


async def _http_search_emails(business: Business) -> tuple[list[str], str | None, dict[str, str]]:
    """Fast email + social URL discovery via HTTP search snippets (no Playwright)."""
    city = _city(business)
    location = f"{city} {business.province or ''}".strip()
    name = business.name
    queries = [
        f'"{name}" {location} email OR contact OR info@',
        f'site:facebook.com "{name}" {city}',
    ]

    found_emails: list[str] = []
    social: dict[str, str] = {}
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers=headers) as client:
        for query in queries:
            for url in (
                f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
                f"https://www.bing.com/search?q={quote_plus(query)}",
            ):
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    text = re.sub(r"<[^>]+>", " ", r.text)
                    text = re.sub(r"\s+", " ", text)
                    for e in extract_emails_from_text(text):
                        if e not in found_emails:
                            found_emails.append(e)
                    for match in re.finditer(r"https?://[^\s\"'<>]+", r.text, re.I):
                        href = match.group(0).rstrip(".,);]")
                        platform = detect_social_platform(href)
                        if platform and platform not in social:
                            social[platform] = normalize_social_url(href)
                except Exception:
                    continue

    ranked = rank_emails(found_emails, name)
    return ranked, ("web_search" if ranked else None), social


async def _scrape_top_socials(page, profiles: dict[str, str], business_name: str) -> tuple[list[str], str | None]:
    """Scrape at most 2 high-yield social pages for emails."""
    priority = ("facebook", "yelp", "instagram", "linkedin")
    ordered = [p for p in priority if p in profiles and profiles[p]]
    all_found: list[str] = []
    source = None
    for platform in ordered[:2]:
        try:
            found = await asyncio.wait_for(
                scrape_social_page_for_emails(page, profiles[platform]),
                timeout=10,
            )
            if found:
                all_found.extend(found)
                source = source or platform
        except Exception:
            continue
    ranked = rank_emails(all_found, business_name)
    return ranked, source


async def _process_business(page, biz: Business) -> Business:
    social_profiles = dict(biz.social_profiles or {})
    emails = merge_emails(biz.contact_emails, biz.contact_email)

    if emails:
        ranked = rank_emails(emails, biz.name)
        return biz.model_copy(update={
            "contact_emails": ranked,
            "contact_email": ranked[0],
            "social_profiles": social_profiles,
        })

    # Large-plan leads usually have a website — scrape it first (highest yield).
    site_emails = await _http_scrape_website_emails(biz)
    if not site_emails and biz.website_url:
        site_emails = await _playwright_scrape_website_emails(page, biz)
    if not site_emails and biz.website_url:
        site_emails = await _guess_role_emails_from_website(biz)
    if site_emails:
        ranked = rank_emails(merge_emails(emails, site_emails), biz.name)
        return biz.model_copy(update={
            "contact_email": ranked[0],
            "contact_emails": ranked,
            "email_source": "website",
            "social_profiles": social_profiles,
        })

    http_emails, http_source, http_social = await _http_search_emails(biz)
    social_profiles = merge_social_profiles(social_profiles, http_social)
    if http_emails:
        ranked = rank_emails(merge_emails(emails, http_emails), biz.name)
        return biz.model_copy(update={
            "contact_email": ranked[0],
            "contact_emails": ranked,
            "email_source": http_source,
            "social_profiles": social_profiles,
        })

    if social_profiles:
        social_emails, found_source = await _scrape_top_socials(page, social_profiles, biz.name)
        if social_emails:
            ranked = rank_emails(merge_emails(emails, social_emails), biz.name)
            return biz.model_copy(update={
                "contact_email": ranked[0],
                "contact_emails": ranked,
                "email_source": found_source,
                "social_profiles": social_profiles,
            })

    return biz.model_copy(update={"social_profiles": social_profiles})


async def find_emails_for_businesses(
    businesses: list[Business],
    progress_callback=None,
) -> list[Business]:
    """Find contact emails quickly with hard per-business timeouts."""
    total = len(businesses)
    if not total:
        return []

    results: list[Business | None] = [None] * total
    workers = min(WORKER_COUNT, total)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(total):
        await queue.put(i)
    completed = 0
    completed_lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        async def worker() -> None:
            nonlocal completed
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = await context.new_page()
            page.set_default_navigation_timeout(PAGE_GOTO_TIMEOUT_MS)
            page.set_default_timeout(PAGE_GOTO_TIMEOUT_MS)

            while True:
                try:
                    idx = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                biz = businesses[idx]
                if progress_callback:
                    await progress_callback(
                        completed,
                        total,
                        f"Searching email ({completed + 1}/{total}): {biz.name}",
                    )

                try:
                    results[idx] = await asyncio.wait_for(
                        _process_business(page, biz),
                        timeout=PER_BUSINESS_TIMEOUT_SEC,
                    )
                except Exception:
                    results[idx] = biz

                async with completed_lock:
                    completed += 1
                    if progress_callback:
                        await progress_callback(
                            completed,
                            total,
                            f"Email search {completed}/{total} done",
                        )

            await context.close()

        await asyncio.gather(*[worker() for _ in range(workers)])
        await browser.close()

    final = [b if b is not None else businesses[i] for i, b in enumerate(results)]
    if progress_callback:
        await progress_callback(
            total,
            total,
            f"Found emails for {sum(1 for b in final if b.contact_email)} businesses",
        )
    return final
