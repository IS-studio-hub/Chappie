import re
from urllib.parse import quote_plus, urlparse, urlunparse

from app.models import Business
from app.services.email_finder import extract_emails_from_text, pick_best_email

SOCIAL_PLATFORMS: dict[str, dict] = {
    "facebook": {
        "label": "Facebook",
        "hosts": ("facebook.com", "fb.com", "fb.me"),
        "search_site": "facebook.com",
        "skip_paths": ("/sharer", "/share", "/login", "/help", "/policies", "/watch", "/reel"),
    },
    "instagram": {
        "label": "Instagram",
        "hosts": ("instagram.com",),
        "search_site": "instagram.com",
        "skip_paths": ("/explore", "/accounts", "/p/", "/reel/", "/stories/"),
    },
    "linkedin": {
        "label": "LinkedIn",
        "hosts": ("linkedin.com",),
        "search_site": "linkedin.com/company",
        "skip_paths": ("/login", "/jobs", "/learning", "/pulse"),
    },
    "twitter": {
        "label": "X",
        "hosts": ("twitter.com", "x.com"),
        "search_site": "twitter.com",
        "skip_paths": ("/home", "/login", "/search", "/intent", "/share"),
    },
    "youtube": {
        "label": "YouTube",
        "hosts": ("youtube.com", "youtu.be"),
        "search_site": "youtube.com",
        "skip_paths": ("/watch", "/shorts", "/playlist", "/results"),
    },
    "tiktok": {
        "label": "TikTok",
        "hosts": ("tiktok.com",),
        "search_site": "tiktok.com",
        "skip_paths": ("/login", "/discover", "/foryou"),
    },
    "pinterest": {
        "label": "Pinterest",
        "hosts": ("pinterest.com", "pinterest.ca"),
        "search_site": "pinterest.com",
        "skip_paths": ("/login", "/search", "/ideas"),
    },
    "yelp": {
        "label": "Yelp",
        "hosts": ("yelp.com", "yelp.ca"),
        "search_site": "yelp.ca",
        "skip_paths": ("/search", "/login", "/signup"),
    },
}


def _city_from_business(business: Business) -> str:
    if not business.address:
        return ""
    parts = [p.strip() for p in business.address.split(",")]
    if len(parts) >= 3:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


def normalize_social_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
        if not parsed.netloc:
            return url
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.rstrip("/") or ""
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception:
        return url


def detect_social_platform(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
        path = urlparse(url).path.lower()
        for platform, meta in SOCIAL_PLATFORMS.items():
            if not any(host == h or host.endswith("." + h) for h in meta["hosts"]):
                continue
            if any(skip in path for skip in meta.get("skip_paths", ())):
                continue
            if path in ("", "/"):
                continue
            return platform
    except Exception:
        pass
    return None


def merge_social_profiles(existing: dict[str, str] | None, found: dict[str, str]) -> dict[str, str]:
    merged = dict(existing or {})
    for platform, url in found.items():
        if url and platform not in merged:
            merged[platform] = normalize_social_url(url)
    return merged


def extract_social_urls_from_text(text: str) -> dict[str, str]:
    profiles: dict[str, str] = {}
    if not text:
        return profiles
    for match in re.finditer(r"https?://[^\s\"'<>]+", text, re.I):
        url = match.group(0).rstrip(".,);]")
        platform = detect_social_platform(url)
        if platform and platform not in profiles:
            profiles[platform] = normalize_social_url(url)
    return profiles


async def _search_result_urls(page, query: str, max_results: int = 6) -> list[str]:
    urls: list[str] = []
    # Bing first — usually faster/less blocked than Google for automation
    engines = [
        f"https://www.bing.com/search?q={quote_plus(query)}",
        f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
    ]
    for search_url in engines:
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(600)
            links = page.locator("a[href^='http']")
            count = await links.count()
            for i in range(min(count, 20)):
                try:
                    href = await links.nth(i).get_attribute("href")
                    if href and href.startswith("http") and href not in urls:
                        urls.append(href)
                        if len(urls) >= max_results:
                            return urls
                except Exception:
                    continue
            if urls:
                return urls
        except Exception:
            continue
    return urls


def _profile_matches_business(url: str, business_name: str) -> bool:
    name_parts = [
        p for p in re.sub(r"[^a-z0-9]+", " ", business_name.lower()).split() if len(p) > 2
    ]
    if not name_parts:
        return True
    blob = (url + " " + urlparse(url).path).lower()
    return any(part in blob for part in name_parts[:3])


async def discover_social_profiles(
    page,
    business: Business,
    skip_maps: bool = False,
    max_platforms: int | None = None,
) -> dict[str, str]:
    """Find social profile/page URLs via web search and page link extraction."""
    profiles = merge_social_profiles(business.social_profiles, {})

    if not skip_maps and business.google_maps_url and not profiles:
        try:
            await page.goto(business.google_maps_url, wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_selector("h1", timeout=2500)
            except Exception:
                await page.wait_for_timeout(400)
            content = await page.content()
            profiles = merge_social_profiles(profiles, extract_social_urls_from_text(content))
            links = page.locator('a[href^="http"]')
            count = await links.count()
            for i in range(min(count, 40)):
                try:
                    href = await links.nth(i).get_attribute("href")
                    if href:
                        platform = detect_social_platform(href)
                        if platform:
                            profiles = merge_social_profiles(profiles, {platform: href})
                except Exception:
                    continue
        except Exception:
            pass

    city = _city_from_business(business)
    province = business.province or ""
    location = f"{city} {province}".strip()
    name = business.name

    # Prioritize high-yield platforms; stop after max_platforms new finds
    priority_platforms = [
        "facebook", "instagram", "linkedin", "yelp",
        "twitter", "youtube", "tiktok", "pinterest",
    ]
    found_new = 0
    limit = max_platforms if max_platforms is not None else len(priority_platforms)

    for platform in priority_platforms:
        if found_new >= limit:
            break
        if platform in profiles:
            continue
        meta = SOCIAL_PLATFORMS.get(platform)
        if not meta:
            continue
        query = f'site:{meta["search_site"]} "{name}" {location}'
        for url in await _search_result_urls(page, query, max_results=5):
            detected = detect_social_platform(url)
            if detected == platform and _profile_matches_business(url, name):
                profiles[platform] = normalize_social_url(url)
                found_new += 1
                break

    return profiles


async def _scrape_facebook(page, url: str) -> list[str]:
    emails: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        await page.wait_for_timeout(400)
        content = await page.content()
        emails.extend(extract_emails_from_text(content))
    except Exception:
        pass
    return emails


async def _scrape_instagram(page, url: str) -> list[str]:
    emails: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        await page.wait_for_timeout(400)
        content = await page.content()
        emails.extend(extract_emails_from_text(content))
    except Exception:
        pass
    return emails


async def _scrape_linkedin(page, url: str) -> list[str]:
    emails: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        await page.wait_for_timeout(400)
        content = await page.content()
        emails.extend(extract_emails_from_text(content))
    except Exception:
        pass
    return emails


async def _scrape_twitter(page, url: str) -> list[str]:
    emails: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        await page.wait_for_timeout(400)
        content = await page.content()
        emails.extend(extract_emails_from_text(content))
    except Exception:
        pass
    return emails


async def _scrape_generic_social(page, url: str) -> list[str]:
    emails: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=8000)
        await page.wait_for_timeout(300)
        content = await page.content()
        emails.extend(extract_emails_from_text(content))
    except Exception:
        pass
    return emails


_SOCIAL_SCRAPERS = {
    "facebook": _scrape_facebook,
    "instagram": _scrape_instagram,
    "linkedin": _scrape_linkedin,
    "twitter": _scrape_twitter,
}


async def scrape_social_page_for_emails(page, url: str) -> list[str]:
    platform = detect_social_platform(url)
    scraper = _SOCIAL_SCRAPERS.get(platform, _scrape_generic_social)
    return await scraper(page, url)


async def find_email_from_social_profiles(
    page, business: Business, profiles: dict[str, str] | None = None
) -> tuple[str | None, str | None]:
    """Search business social pages for a contact email — stop on first good hit."""
    profiles = profiles or business.social_profiles or {}
    if not profiles:
        return None, None

    priority = ("facebook", "instagram", "yelp", "linkedin", "twitter", "youtube", "tiktok", "pinterest")
    ordered = sorted(profiles.items(), key=lambda x: priority.index(x[0]) if x[0] in priority else 99)

    for platform, url in ordered:
        if not url:
            continue
        scraper = _SOCIAL_SCRAPERS.get(platform, _scrape_generic_social)
        found = await scraper(page, url)
        best = pick_best_email(found, business.name)
        if best:
            return best, platform

    return None, None
