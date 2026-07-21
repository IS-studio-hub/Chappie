"""Per-business brand book: colors, logo, tone, pricing, content, imagery.

Built after search (heuristics for all; OpenAI upgrades when connected)
and injected into the Figma Make prompt on Create Site.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.models import BrandBook, Business
from app.services.db import get_db

OPENAI_API_BASE = "https://api.openai.com/v1"
BRAND_CONCURRENCY = 3
CACHE_COLLECTION = "brand_books"

# Category → starter brand directions (used when OpenAI is unavailable)
CATEGORY_BRAND_PRESETS: dict[str, dict[str, Any]] = {
    "dentist": {
        "colors": ["#0B6E4F", "#F7F9F8", "#1C1C1C", "#A8D5C2"],
        "tone": "Calm, trustworthy, clinical but warm",
        "pricing_positioning": "Transparent professional care; mid-to-premium",
        "content_themes": ["Patient comfort", "Modern equipment", "Family care", "Easy booking"],
        "imagery_style": "Bright clean clinic, soft greens, smiling patients, minimal medical props",
        "fonts_suggestion": "Clean sans for UI + soft humanist headings",
        "logo_description": "Simple wordmark or tooth/smile mark in teal or soft green",
    },
    "restaurant": {
        "colors": ["#8B1E1E", "#F4EFE6", "#1A1A1A", "#C4A484"],
        "tone": "Warm, inviting, appetite-driven",
        "pricing_positioning": "Neighborhood favorite; fair everyday pricing",
        "content_themes": ["Signature dishes", "Local ingredients", "Atmosphere", "Reservations"],
        "imagery_style": "Food-forward photography, warm lighting, textured surfaces",
        "fonts_suggestion": "Expressive serif headlines + simple sans body",
        "logo_description": "Bold wordmark with optional culinary symbol",
    },
    "cafe": {
        "colors": ["#4A3728", "#F5F0E8", "#2C2C2C", "#D4A574"],
        "tone": "Cozy, friendly, everyday ritual",
        "pricing_positioning": "Approachable specialty coffee / casual bites",
        "content_themes": ["Brew methods", "Pastries", "Community hangout", "Wi‑Fi work-friendly"],
        "imagery_style": "Latte art, wood textures, morning light, people chatting",
        "fonts_suggestion": "Rounded sans or soft serif for cafe charm",
        "logo_description": "Friendly wordmark with coffee cup or bean motif",
    },
    "salon": {
        "colors": ["#2D2A32", "#F8F4F0", "#C9A86A", "#8E7B8F"],
        "tone": "Polished, stylish, personal",
        "pricing_positioning": "Premium self-care with clear service menus",
        "content_themes": ["Transformations", "Stylists", "Booking", "Products"],
        "imagery_style": "Editorial beauty shots, soft glam lighting, before/after energy",
        "fonts_suggestion": "Elegant display + modern sans",
        "logo_description": "Minimal monogram or chic wordmark",
    },
    "lawyer": {
        "colors": ["#1B2A4A", "#F5F6F8", "#9A7B4F", "#4A5568"],
        "tone": "Authoritative, clear, reassuring",
        "pricing_positioning": "Professional counsel; consult-first",
        "content_themes": ["Practice areas", "Results", "Process", "Confidential consult"],
        "imagery_style": "Clean office, confident portraits, abstract justice motifs (no clichés overload)",
        "fonts_suggestion": "Strong serif headlines + neutral sans body",
        "logo_description": "Classic initials wordmark in navy/gold",
    },
    "plumber": {
        "colors": ["#0B4F6C", "#F2F5F7", "#F0A202", "#1F2933"],
        "tone": "Reliable, practical, no-nonsense",
        "pricing_positioning": "Upfront estimates; emergency-ready",
        "content_themes": ["24/7 help", "Licensed techs", "Service list", "Reviews"],
        "imagery_style": "Real technicians, vans, clean bathrooms/kitchens, high-contrast CTAs",
        "fonts_suggestion": "Bold sans throughout",
        "logo_description": "Bold wordmark with wrench/pipe icon",
    },
    "gym": {
        "colors": ["#111111", "#E8FF47", "#FFFFFF", "#333333"],
        "tone": "Energetic, motivating, disciplined",
        "pricing_positioning": "Membership tiers; results-focused",
        "content_themes": ["Classes", "Coaches", "Transformations", "Trial offer"],
        "imagery_style": "High-contrast training shots, motion, bold typography overlays",
        "fonts_suggestion": "Condensed athletic sans",
        "logo_description": "Strong geometric mark or bold wordmark",
    },
}

DEFAULT_PRESET = {
    "colors": ["#1F3A5F", "#F4F1EA", "#2A2A2A", "#3D9A7A"],
    "tone": "Professional, local, approachable",
    "pricing_positioning": "Clear value for neighborhood customers",
    "content_themes": ["About", "Services", "Reviews", "Contact / booking"],
    "imagery_style": "Authentic local business photography, clean compositions, natural light",
    "fonts_suggestion": "Modern sans with one distinctive display face",
    "logo_description": "Simple wordmark matching the business name",
}


def _norm_cat(text: str | None) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()


def _pick_preset(business: Business) -> dict[str, Any]:
    blob = _norm_cat(" ".join([
        business.category or "",
        " ".join(business.categories or []),
        business.name or "",
    ]))
    for key, preset in CATEGORY_BRAND_PRESETS.items():
        if key in blob:
            return preset
    # fuzzy aliases
    aliases = {
        "hair": "salon",
        "beauty": "salon",
        "spa": "salon",
        "pizza": "restaurant",
        "bakery": "cafe",
        "coffee": "cafe",
        "dental": "dentist",
        "attorney": "lawyer",
        "law": "lawyer",
        "fitness": "gym",
        "electric": "plumber",
        "hvac": "plumber",
        "roof": "plumber",
    }
    for alias, key in aliases.items():
        if alias in blob and key in CATEGORY_BRAND_PRESETS:
            return CATEGORY_BRAND_PRESETS[key]
    return DEFAULT_PRESET


def heuristic_brand_book(business: Business) -> BrandBook:
    preset = _pick_preset(business)
    return BrandBook(
        colors=list(preset["colors"]),
        logo_description=preset["logo_description"],
        logo_url=None,
        tone=preset["tone"],
        pricing_positioning=preset["pricing_positioning"],
        content_themes=list(preset["content_themes"]),
        imagery_style=preset["imagery_style"],
        fonts_suggestion=preset["fonts_suggestion"],
        source="heuristic",
        status="ready",
        notes="Category-based brand starter (no OpenAI research yet).",
    )


def brand_book_to_prompt_block(book: BrandBook | None, business_name: str) -> str:
    if not book:
        return ""
    lines = [
        "",
        "Brand book (use this as the visual & verbal system):",
        f"- Business: {business_name}",
    ]
    if book.colors:
        lines.append(f"- Colors (hex): {', '.join(book.colors)}")
    if book.logo_description:
        lines.append(f"- Logo direction: {book.logo_description}")
    if book.logo_url:
        lines.append(f"- Logo reference URL: {book.logo_url}")
    if book.logo_svg:
        lines.append("- Logo SVG: provided separately in the brief — use that exact markup")
    if book.tone:
        lines.append(f"- Tone of voice: {book.tone}")
    if book.pricing_positioning:
        lines.append(f"- Pricing / positioning: {book.pricing_positioning}")
    if book.content_themes:
        lines.append(f"- Content themes: {', '.join(book.content_themes)}")
    if book.imagery_style:
        lines.append(f"- Imagery style: {book.imagery_style}")
    if book.fonts_suggestion:
        lines.append(f"- Typography: {book.fonts_suggestion}")
    if book.notes:
        lines.append(f"- Notes: {book.notes}")
    lines.append("- Apply this brand book consistently across all pages and components.")
    return "\n".join(lines)


def _parse_json(raw: str) -> dict:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def _book_from_ai(data: dict, fallback: BrandBook) -> BrandBook:
    colors = data.get("colors") or data.get("brand_colors") or fallback.colors
    if isinstance(colors, str):
        colors = [c.strip() for c in colors.split(",") if c.strip()]
    if not isinstance(colors, list):
        colors = fallback.colors
    colors = [str(c).strip() for c in colors if str(c).strip()][:6]

    themes = data.get("content_themes") or data.get("content") or fallback.content_themes
    if isinstance(themes, str):
        themes = [t.strip() for t in themes.split(",") if t.strip()]
    if not isinstance(themes, list):
        themes = fallback.content_themes
    themes = [str(t).strip() for t in themes if str(t).strip()][:8]

    return BrandBook(
        colors=colors or fallback.colors,
        logo_description=(data.get("logo_description") or data.get("logo") or fallback.logo_description),
        logo_url=(data.get("logo_url") if isinstance(data.get("logo_url"), str) else None),
        logo_svg=(data.get("logo_svg") if isinstance(data.get("logo_svg"), str) else fallback.logo_svg),
        tone=(data.get("tone") or fallback.tone),
        pricing_positioning=(
            data.get("pricing_positioning")
            or data.get("pricing")
            or fallback.pricing_positioning
        ),
        content_themes=themes or fallback.content_themes,
        imagery_style=(data.get("imagery_style") or data.get("imagery") or fallback.imagery_style),
        fonts_suggestion=(data.get("fonts_suggestion") or data.get("fonts") or fallback.fonts_suggestion),
        source="openai",
        status="ready",
        notes=(data.get("notes") or "Researched brand direction for website prototype."),
    )


async def _load_cached(place_id: str | None, user_id: str | None = None) -> BrandBook | None:
    if not place_id or not user_id:
        return None
    try:
        doc = await get_db()[CACHE_COLLECTION].find_one(
            {"place_id": place_id, "user_id": user_id}
        )
    except Exception:
        return None
    if not doc or not doc.get("brand_book"):
        return None
    try:
        return BrandBook(**doc["brand_book"])
    except Exception:
        return None


async def _save_cached(
    place_id: str | None,
    book: BrandBook,
    user_id: str | None = None,
) -> None:
    if not place_id or not user_id:
        return
    try:
        await get_db()[CACHE_COLLECTION].update_one(
            {"place_id": place_id, "user_id": user_id},
            {
                "$set": {
                    "place_id": place_id,
                    "user_id": user_id,
                    "brand_book": book.model_dump(),
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception:
        pass


async def _openai_brand_book(
    business: Business,
    api_key: str,
    fallback: BrandBook,
) -> BrandBook:
    socials = ", ".join(f"{k}: {v}" for k, v in (business.social_profiles or {}).items()) or "none"
    products = ", ".join(
        f"{p.name}{(' ' + p.price) if p.price else ''}" for p in (business.products or [])[:6]
    ) or "unknown"
    prompt = f"""Create a website brand book for this local business that likely has no dedicated website.
Infer a credible brand direction from category, reviews, socials, and offerings.
Do NOT invent a fake logo URL. logo_url only if you are confident a real public logo URL exists.

Business: {business.name}
Category: {business.category or 'unknown'}
Address: {business.address or 'unknown'}
Description: {(business.description or '')[:400] or 'unknown'}
Rating: {business.rating or 'n/a'} ({business.review_count or 0} reviews)
Socials: {socials}
Products/services: {products}

Return JSON with keys:
colors (array of 3-5 hex strings),
logo_description (string),
logo_url (string or null),
tone (string),
pricing_positioning (string),
content_themes (array of short strings),
imagery_style (string),
fonts_suggestion (string),
notes (short string).
"""
    payload = {
        "model": settings.openai_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a brand designer for local SMB websites. Respond with JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code != 200:
        return fallback.model_copy(update={"notes": "OpenAI brand research failed; using category preset."})
    raw = response.json()["choices"][0]["message"]["content"]
    data = _parse_json(raw or "")
    if not data:
        return fallback
    return _book_from_ai(data, fallback)


async def build_brand_book_for_business(
    business: Business,
    *,
    api_key: str | None = None,
    use_cache: bool = True,
    user_id: str | None = None,
) -> BrandBook:
    if use_cache:
        cached = await _load_cached(business.place_id, user_id=user_id)
        if cached and cached.status == "ready":
            return cached

    book = heuristic_brand_book(business)
    key = (api_key or settings.openai_api_key or "").strip()
    if key:
        book = await _openai_brand_book(business, key, book)

    await _save_cached(business.place_id, book, user_id=user_id)
    return book


async def enrich_businesses_with_brand_books(
    businesses: list[Business],
    *,
    api_key: str | None = None,
    user_id: str | None = None,
    progress_callback=None,
) -> list[Business]:
    """Attach a brand book to every business (heuristic, then OpenAI when available)."""
    if not businesses:
        return []

    total = len(businesses)
    results: list[Business | None] = [None] * total
    sem = asyncio.Semaphore(BRAND_CONCURRENCY)
    done = 0
    lock = asyncio.Lock()

    async def run_one(idx: int, biz: Business) -> None:
        nonlocal done
        async with sem:
            try:
                book = await build_brand_book_for_business(
                    biz, api_key=api_key, user_id=user_id
                )
            except Exception:
                book = heuristic_brand_book(biz)
            results[idx] = biz.model_copy(update={"brand_book": book})
        async with lock:
            done += 1
            if progress_callback:
                await progress_callback(
                    done,
                    total,
                    f"Building brand books ({done}/{total})...",
                )

    await asyncio.gather(*[run_one(i, b) for i, b in enumerate(businesses)])
    return [b for b in results if b is not None]
