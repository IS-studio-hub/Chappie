import re
from urllib.parse import quote_plus, urlparse

from app.models import Business
from app.services.email_finder import extract_emails_from_text, pick_best_email
from app.services.social_discovery import detect_social_platform, scrape_social_page_for_emails

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SKIP_DOMAINS = {
    "google.com", "google.ca", "gstatic.com", "youtube.com",
    "twitter.com", "x.com", "instagram.com", "tiktok.com",
    "pinterest.com", "wikipedia.org", "apple.com", "microsoft.com",
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


def _search_queries(business: Business) -> list[str]:
    city = _city_from_business(business)
    province = business.province or ""
    name = business.name
    location = f"{city} {province}".strip()

    queries = [
        f'"{name}" {location} email contact',
        f'"{name}" {location} "@gmail.com" OR "@yahoo.com" OR "@hotmail.com"',
        f'"{name}" {city} email address',
        f'{name} {location} contact us email',
        f'site:facebook.com "{name}" {city} email',
        f'site:yelp.ca "{name}" {city}',
        f'site:yellowpages.ca "{name}" {city}',
        f'site:linkedin.com/company "{name}" {city}',
        f'site:canada411.ca "{name}" {city}',
        f'"{name}" {location} info@ OR contact@ OR hello@',
    ]
    if business.phone:
        phone_digits = re.sub(r"\D", "", business.phone)[-10:]
        if phone_digits:
            queries.append(f'"{name}" "{phone_digits}" email')

    return queries


def _is_useful_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().replace("www.", "")
        return not any(host.endswith(d) or d in host for d in SKIP_DOMAINS)
    except Exception:
        return False


async def _google_result_urls(page, query: str, max_results: int = 8) -> list[str]:
    urls: list[str] = []
    try:
        await page.goto(
            f"https://www.google.com/search?q={quote_plus(query)}&num=10",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(2000)

        for sel in ['div#search a[href^="http"]', 'a[jsname="UWckNb"]', '#rso a[href^="http"]']:
            links = page.locator(sel)
            count = await links.count()
            for i in range(min(count, 20)):
                try:
                    href = await links.nth(i).get_attribute("href")
                    if href and href.startswith("http") and _is_useful_url(href) and href not in urls:
                        urls.append(href)
                        if len(urls) >= max_results:
                            return urls
                except Exception:
                    continue
            if urls:
                break
    except Exception:
        pass
    return urls


async def _bing_result_urls(page, query: str, max_results: int = 6) -> list[str]:
    urls: list[str] = []
    try:
        await page.goto(
            f"https://www.bing.com/search?q={quote_plus(query)}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        await page.wait_for_timeout(700)

        links = page.locator("li.b_algo h2 a, #b_results a[href^='http']")
        count = await links.count()
        for i in range(min(count, 15)):
            try:
                href = await links.nth(i).get_attribute("href")
                if href and _is_useful_url(href) and href not in urls:
                    urls.append(href)
                    if len(urls) >= max_results:
                        break
            except Exception:
                continue
    except Exception:
        pass
    return urls


async def _duckduckgo_result_urls(page, query: str, max_results: int = 6) -> list[str]:
    urls: list[str] = []
    try:
        await page.goto(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        await page.wait_for_timeout(700)

        links = page.locator("a.result__a, a[href^='http']")
        count = await links.count()
        for i in range(min(count, 15)):
            try:
                href = await links.nth(i).get_attribute("href")
                if href and href.startswith("http") and _is_useful_url(href) and href not in urls:
                    urls.append(href)
                    if len(urls) >= max_results:
                        break
            except Exception:
                continue
    except Exception:
        pass
    return urls


async def _scrape_url_for_emails(page, url: str, business_name: str) -> list[str]:
    if detect_social_platform(url):
        return await scrape_social_page_for_emails(page, url)

    emails: list[str] = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(600)

        content = await page.content()
        emails.extend(extract_emails_from_text(content))

        # mailto links
        mailtos = page.locator('a[href^="mailto:"]')
        count = await mailtos.count()
        for i in range(min(count, 5)):
            href = await mailtos.nth(i).get_attribute("href") or ""
            emails.extend(extract_emails_from_text(href))

        body = await page.locator("body").inner_text()
        emails.extend(extract_emails_from_text(body))
    except Exception:
        pass

    return emails


def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    for label, domains in [
        ("facebook", ("facebook.com",)),
        ("yelp", ("yelp.ca", "yelp.com")),
        ("yellowpages", ("yellowpages.ca", "yellowpages.com")),
        ("linkedin", ("linkedin.com",)),
        ("canada411", ("canada411.ca",)),
        ("instagram", ("instagram.com",)),
        ("twitter", ("twitter.com", "x.com")),
        ("youtube", ("youtube.com", "youtu.be")),
        ("tiktok", ("tiktok.com",)),
        ("pinterest", ("pinterest.com", "pinterest.ca")),
    ]:
        if any(d in host for d in domains):
            return label
    return "web"


async def search_email_on_internet(page, business: Business) -> tuple[str | None, str | None]:
    """
    Search the web for a business contact email.
    Stops early once a good email is found to save time.
    """
    all_emails: list[str] = []
    email_sources: dict[str, str] = {}

    queries = _search_queries(business)[:2]
    collected_urls: list[str] = []

    for platform, url in (business.social_profiles or {}).items():
        if url and url not in collected_urls:
            collected_urls.insert(0, url)

    # Prefer Bing/DDG (faster); skip Google unless needed
    for query in queries:
        for fetcher in (_bing_result_urls, _duckduckgo_result_urls):
            urls = await fetcher(page, query, max_results=4)
            for u in urls:
                if u not in collected_urls:
                    collected_urls.append(u)
            if len(collected_urls) >= 8:
                break
        if len(collected_urls) >= 8:
            break

    for url in collected_urls[:6]:
        found = await _scrape_url_for_emails(page, url, business.name)
        source = _source_from_url(url)
        for e in found:
            if e not in all_emails:
                all_emails.append(e)
                email_sources[e] = source
        best = pick_best_email(all_emails, business.name)
        if best:
            return best, email_sources.get(best, "web_search")

    return None, None
