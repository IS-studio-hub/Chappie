import asyncio
import json
import re
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, Page

from app.models import Business, Product, Review
from app.services.email_finder import extract_emails_from_text, merge_emails, pick_best_email, rank_emails
from app.services.social_discovery import detect_social_platform, merge_social_profiles, normalize_social_url

# Google Place types for nearby search — covers most local businesses
PLACE_TYPES = [
    "restaurant", "cafe", "bar", "bakery", "meal_takeaway", "meal_delivery",
    "grocery_or_supermarket", "convenience_store", "liquor_store", "pharmacy",
    "drugstore", "hospital", "doctor", "dentist", "physiotherapist", "veterinary_care",
    "beauty_salon", "hair_care", "spa", "gym", "school", "university",
    "lawyer", "accounting", "insurance_agency", "real_estate_agency",
    "bank", "atm", "post_office", "local_government_office",
    "car_repair", "car_wash", "car_dealer", "gas_station", "parking",
    "electrician", "plumber", "roofing_contractor", "painter", "locksmith",
    "moving_company", "storage", "laundry", "dry_cleaner",
    "florist", "jewelry_store", "clothing_store", "shoe_store", "furniture_store",
    "home_goods_store", "hardware_store", "electronics_store", "book_store",
    "pet_store", "bicycle_store", "sporting_goods_store",
    "church", "mosque", "synagogue", "hindu_temple",
    "lodging", "travel_agency", "tourist_attraction", "museum", "art_gallery",
    "night_club", "movie_theater", "bowling_alley", "amusement_park",
    "cemetery", "funeral_home", "library", "stadium",
    "general_contractor", "establishment", "point_of_interest", "store",
    "finance", "health", "food", "shopping_mall", "department_store",
    "wholesaler", "corporate_office", "coworking_space",
]


async def _accept_cookies(page: Page) -> None:
    for selector in [
        'button:has-text("Accept all")',
        'button:has-text("Accept")',
        'button:has-text("I agree")',
        'button[aria-label="Accept all"]',
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=600):
                await btn.click()
                await page.wait_for_timeout(200)
                return
        except Exception:
            continue


async def _has_website_button(page: Page) -> bool:
    """Check if the Google Maps listing shows a Website button."""
    selectors = [
        'a[data-item-id="authority"]',
        'button[data-item-id="authority"]',
        'a[aria-label*="Website"]',
        'button[aria-label*="Website"]',
        '[data-tooltip="Open website"]',
        'a[data-tooltip="Open website"]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


async def _extract_text_by_label(page: Page, label: str) -> str | None:
    try:
        el = page.locator(f'button[aria-label*="{label}"], div[aria-label*="{label}"]').first
        if await el.is_visible(timeout=2000):
            text = await el.inner_text()
            return text.strip() if text else None
    except Exception:
        pass
    return None


async def _extract_website_url(page: Page) -> str | None:
    selectors = [
        'a[data-item-id="authority"]',
        'a[aria-label*="Website"]',
        'a[data-tooltip="Open website"]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=500):
                href = await el.get_attribute("href")
                if href and href.startswith("http"):
                    return href.strip()
        except Exception:
            continue
    return None


async def scrape_place_details(
    page: Page,
    maps_url: str,
    *,
    allow_with_website: bool = False,
) -> dict | None:
    """
    Scrape a Google Maps place page for enrichment fields Places API misses
    (social links, email, products, areas served).

    When ``allow_with_website`` is False, returns None if the listing has a
    Website button (SMB no-site pipeline). When True, keeps scraping and
    records the website URL.
    """
    await page.goto(maps_url, wait_until="domcontentloaded", timeout=45000)
    try:
        await page.wait_for_selector("h1", timeout=4000)
    except Exception:
        await page.wait_for_timeout(800)
    await _accept_cookies(page)

    has_site = await _has_website_button(page)
    if has_site and not allow_with_website:
        return None

    data: dict = {"has_website": has_site}
    if has_site:
        site = await _extract_website_url(page)
        if site:
            data["website_url"] = site
            data["has_website"] = True


    try:
        name_el = page.locator("h1").first
        if await name_el.is_visible(timeout=2000):
            data["name"] = (await name_el.inner_text()).strip()
    except Exception:
        pass

    try:
        rating_el = page.locator('div[role="img"][aria-label*="stars"]').first
        if await rating_el.is_visible(timeout=1500):
            aria = await rating_el.get_attribute("aria-label") or ""
            rating_match = re.search(r"([\d.]+)\s*stars?", aria, re.I)
            if rating_match:
                data["rating"] = float(rating_match.group(1))
            review_match = re.search(r"(\d[\d,]*)\s*reviews?", aria, re.I)
            if review_match:
                data["review_count"] = int(review_match.group(1).replace(",", ""))
    except Exception:
        pass

    try:
        cat_el = page.locator('button[jsaction*="category"]').first
        if await cat_el.is_visible(timeout=800):
            data["category"] = (await cat_el.inner_text()).strip()
    except Exception:
        pass

    try:
        addr_btn = page.locator('button[data-item-id="address"]').first
        if await addr_btn.is_visible(timeout=800):
            data["address"] = (await addr_btn.inner_text()).strip()
    except Exception:
        pass

    try:
        phone_btn = page.locator('button[data-item-id^="phone"]').first
        if await phone_btn.is_visible(timeout=800):
            data["phone"] = (await phone_btn.inner_text()).strip()
    except Exception:
        pass

    try:
        hours_btn = page.locator('button[data-item-id="oh"]').first
        if await hours_btn.is_visible(timeout=800):
            hours_text = (await hours_btn.inner_text()).strip()
            data["hours"] = hours_text
            data["is_open"] = "open" in hours_text.lower() and "closed" not in hours_text.lower().split("·")[0]
    except Exception:
        pass

    # Products / Services — quick pass only
    products: list[dict] = []
    try:
        menu_btn = page.locator('button:has-text("Menu"), button:has-text("Products"), button:has-text("Services")').first
        if await menu_btn.is_visible(timeout=800):
            await menu_btn.click()
            await page.wait_for_timeout(600)
            product_els = page.locator('[data-section-id="menu"] [role="listitem"], [data-section-id="products"] [role="listitem"]')
            count = await product_els.count()
            for i in range(min(count, 15)):
                item = product_els.nth(i)
                try:
                    name = await item.locator("div").first.inner_text()
                    if name and len(name) < 200:
                        products.append({"name": name.strip(), "price": None})
                except Exception:
                    continue
    except Exception:
        pass
    data["products"] = products

    try:
        page_text = await page.locator("body").inner_text()
        for pattern, key in [
            (r"Located in:\s*(.+)", "located_in"),
            (r"Areas served:\s*(.+)", "areas_served"),
            (r"Province:\s*(.+)", "province"),
            (r"Appointments?:\s*(\S+)", "appointment_url"),
        ]:
            match = re.search(pattern, page_text, re.I)
            if match:
                data[key] = match.group(1).strip().split("\n")[0]
        emails = extract_emails_from_text(page_text)
        best = pick_best_email(emails, data.get("name", ""))
        if best:
            data["contact_email"] = best
            data["email_source"] = "google_maps"
    except Exception:
        pass

    social: dict[str, str] = {}
    try:
        links = page.locator('a[href^="http"]')
        count = await links.count()
        for i in range(min(count, 60)):
            try:
                href = await links.nth(i).get_attribute("href")
                if not href:
                    continue
                platform = detect_social_platform(href)
                if platform:
                    social[platform] = normalize_social_url(href)
            except Exception:
                continue
    except Exception:
        pass
    data["social_profiles"] = social

    if not data.get("contact_email"):
        try:
            for sel in [
                'a[data-item-id="email"]', 'button[data-item-id="email"]',
                'a[href^="mailto:"]', 'button[aria-label*="Email"]',
            ]:
                el = page.locator(sel).first
                if await el.is_visible(timeout=500):
                    href = await el.get_attribute("href") or ""
                    text = await el.inner_text() or ""
                    emails = extract_emails_from_text(href + " " + text)
                    if emails:
                        data["contact_email"] = pick_best_email(emails, data.get("name", ""))
                        data["email_source"] = "google_maps"
                        break
        except Exception:
            pass

    data["google_maps_url"] = maps_url
    return data


def _scraped_to_business(data: dict) -> Business:
    return Business(
        name=data.get("name", "Unknown"),
        rating=data.get("rating"),
        review_count=data.get("review_count"),
        category=data.get("category"),
        address=data.get("address"),
        phone=data.get("phone"),
        hours=data.get("hours"),
        is_open=data.get("is_open"),
        areas_served=data.get("areas_served"),
        province=data.get("province"),
        located_in=data.get("located_in"),
        description=data.get("description"),
        appointment_url=data.get("appointment_url"),
        has_website=False,
        google_maps_url=data.get("google_maps_url"),
        photo_count=data.get("photo_count"),
        products=[Product(**p) for p in data.get("products", [])],
        reviews=[Review(**r) for r in data.get("reviews", [])],
        social_profiles=data.get("social_profiles", {}),
        contact_email=data.get("contact_email"),
        contact_emails=([data["contact_email"]] if data.get("contact_email") else []),
        email_source=data.get("email_source"),
    )


async def enrich_businesses(
    businesses: list[Business],
    progress_callback=None,
    *,
    keep_with_website: bool = False,
) -> list[Business]:
    """Enrich listings via Google Maps — parallel workers, skip when little to gain.

    When ``keep_with_website`` is False (default), listings that show a Website
    button on Maps are dropped (SMB no-site pipeline). When True (large plan),
    those listings are kept and marked ``has_website=True``.
    """
    total = len(businesses)
    if not total:
        return []

    enriched: list[Business | None] = [None] * total
    workers = min(3, total)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(total):
        await queue.put(i)
    done = 0
    done_lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        async def worker() -> None:
            nonlocal done
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = await context.new_page()

            while True:
                try:
                    idx = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                biz = businesses[idx]
                if progress_callback:
                    await progress_callback(idx, total, f"Enriching: {biz.name}")

                # Large plan: Places already provides website_url — skip Maps.
                # Email finder scrapes the site directly (much higher yield).
                if keep_with_website and biz.website_url and not biz.contact_email:
                    enriched[idx] = biz
                    async with done_lock:
                        done += 1
                    continue

                needs_scrape = not biz.contact_email or not biz.social_profiles
                url = biz.google_maps_url
                if not url and biz.name and biz.address:
                    query = quote_plus(f"{biz.name} {biz.address}")
                    url = f"https://www.google.com/maps/search/{query}"

                if not url or not needs_scrape:
                    enriched[idx] = biz
                else:
                    try:
                        scraped = await scrape_place_details(
                            page,
                            url,
                            allow_with_website=keep_with_website,
                        )
                        if scraped is None:
                            if keep_with_website or biz.has_website:
                                enriched[idx] = biz.model_copy(update={"has_website": True})
                            else:
                                enriched[idx] = None  # has website — drop for SMB pipeline
                        else:
                            merged = biz.model_dump()
                            for key, val in scraped.items():
                                if not val:
                                    continue
                                if key == "contact_email":
                                    emails = merge_emails(
                                        merged.get("contact_emails"),
                                        merged.get("contact_email"),
                                        val,
                                    )
                                    if emails:
                                        ranked = rank_emails(emails, merged.get("name", ""))
                                        merged["contact_emails"] = ranked
                                        merged["contact_email"] = ranked[0]
                                    continue
                                if not merged.get(key) or key in ("products", "reviews", "social_profiles"):
                                    merged[key] = val
                            enriched[idx] = Business(**merged)
                    except Exception:
                        enriched[idx] = biz

                async with done_lock:
                    done += 1
                    if progress_callback and done == total:
                        await progress_callback(total, total, f"Enriched {sum(1 for b in enriched if b)} businesses")

            await context.close()

        await asyncio.gather(*[worker() for _ in range(workers)])
        await browser.close()

    return [b for b in enriched if b is not None]


async def search_maps_directly(
    address: str,
    radius_km: float,
    progress_callback=None,
) -> list[Business]:
    """
    Alternative: search Google Maps directly via browser automation.
    Finds businesses in area and filters those without Website button.
    """
    businesses: list[Business] = []
    query = quote_plus(f"businesses near {address}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        if progress_callback:
            await progress_callback(0, 1, "Opening Google Maps...")

        await page.goto(f"https://www.google.com/maps/search/{query}", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        await _accept_cookies(page)

        # Scroll the results feed to load more listings
        feed = page.locator('div[role="feed"]').first
        seen_urls: set[str] = set()

        for scroll_round in range(15):
            if progress_callback:
                await progress_callback(scroll_round, 15, f"Scanning listings (round {scroll_round + 1})...")

            links = page.locator('a[href*="/maps/place/"]')
            count = await links.count()

            for i in range(count):
                link = links.nth(i)
                try:
                    href = await link.get_attribute("href")
                    if not href or href in seen_urls:
                        continue
                    seen_urls.add(href)

                    await link.click()
                    await page.wait_for_timeout(2500)

                    if await _has_website_button(page):
                        continue

                    scraped = await scrape_place_details(page, page.url)
                    if scraped and scraped.get("name"):
                        businesses.append(_scraped_to_business(scraped))
                except Exception:
                    continue

            try:
                await feed.evaluate("el => el.scrollTop = el.scrollHeight")
                await page.wait_for_timeout(2000)
            except Exception:
                break

        await browser.close()

    return businesses
