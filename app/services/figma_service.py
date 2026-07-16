import random

import httpx
import lzstring

from app.models import Business, BrandBook
from app.services.brand_book import heuristic_brand_book
from app.services.logo_svg import logo_svg_prompt_block, resolve_logo_svg
from app.services.make_variants import (
    format_photo_references,
    format_variant_block,
    pick_make_variant,
)
from app.services.place_photos import fetch_place_photo_uris

FIGMA_API_BASE = "https://api.figma.com/v1"
FIGMA_MAKE_BASE = "https://www.figma.com/make/new"

_connected_token: str | None = None
_connected_user: dict | None = None


def is_figma_connected() -> bool:
    return _connected_token is not None


def get_figma_status() -> dict:
    if not _connected_token:
        return {"connected": False}
    return {
        "connected": True,
        "email": (_connected_user or {}).get("email"),
        "handle": (_connected_user or {}).get("handle"),
    }


async def connect_figma(token: str) -> dict:
    global _connected_token, _connected_user

    token = token.strip()
    if not token:
        raise ValueError("Figma API token is required")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{FIGMA_API_BASE}/me",
            headers={"X-Figma-Token": token},
        )

    if response.status_code == 403:
        raise ValueError("Invalid Figma API token. Check your personal access token.")
    if response.status_code != 200:
        raise ValueError(f"Figma API error: {response.status_code}")

    data = response.json()
    _connected_token = token
    _connected_user = {
        "email": data.get("email"),
        "handle": data.get("handle"),
        "id": data.get("id"),
    }
    return get_figma_status()


def disconnect_figma() -> None:
    global _connected_token, _connected_user
    _connected_token = None
    _connected_user = None


def _ensure_brand_book(business: Business) -> BrandBook:
    if business.brand_book and business.brand_book.status == "ready":
        return business.brand_book
    return heuristic_brand_book(business)


def _build_make_prompt(
    business: Business,
    *,
    logo_svg: str | None = None,
    photo_uris: list[str] | None = None,
    variant=None,
    language: str = "English",
) -> str:
    book = _ensure_brand_book(business)
    variant = variant or pick_make_variant()
    photo_uris = photo_uris or []
    language = (language or "English").strip() or "English"

    colors = ", ".join(book.colors[:5]) if book.colors else "derive from category"
    category = business.category or "local business"
    tone = book.tone or "premium, trustworthy, modern"
    imagery = book.imagery_style or "authentic premium photography with depth"
    logo = book.logo_description or f"distinctive wordmark for {business.name}"
    fonts = book.fonts_suggestion or "expressive display + clean modern sans"
    pricing = book.pricing_positioning or "clear value, conversion-focused"
    themes = ", ".join(book.content_themes[:5]) if book.content_themes else "trust, services, booking"
    svg = logo_svg or book.logo_svg

    lines = [
        "CRITICAL CREATIVE BRIEF — READ FIRST AND OBEY:",
        "Build a CUTTING-EDGE interactive website prototype worthy of Awwwards (Sites of the Day / Developer Award caliber).",
        "Reference the craft standard of https://www.awwwards.com/websites/ — originality, motion fluency, art direction, and unforgettable first viewport.",
        "NOT a generic brochure, NOT a SaaS template, NOT a boring card layout.",
        f"This generation must be UNIQUE (variant {variant.id}). Same business/brand facts — entirely different design, layout, motion, and components than any prior run.",
        "",
        "SITE LANGUAGE (mandatory):",
        f"- Write ALL visible website copy in {language}: headlines, buttons, nav, services, about, forms, footers, CTAs, placeholders, alt text.",
        f"- UI chrome and microcopy must be {language}. Do not mix in English unless the selected language IS English.",
        "- Keep proper nouns as-is: business name, street addresses, phone numbers, emails, URLs, and brand logo text.",
        "- If reviews are quoted, you may keep the original review language or gently localize — prefer localizing UI labels around them.",
        "",
        "NON-NEGOTIABLE REQUIREMENTS:",
        "1) AWWARDS-LEVEL CRAFT: cinematic composition, distinctive typography, intentional whitespace, layered depth, premium micro-interactions.",
        f"2) MEDIA MODE: follow the assigned media direction ({variant.media_mode}). Most builds should weave video + 3D + imagery; if this variant is specialized, still keep subtle accents from the other two.",
        "3) BRAND-LOCKED: every color, type choice, tone, and image mood MUST follow the brand book. Do not invent a purple/indigo AI default theme.",
        "4) LOGO: use the official SVG below in nav/footer — never invent a substitute mark.",
        "5) REAL GOOGLE PHOTOS: when photo URLs are provided, treat them as primary references for all photographic and video mood content.",
        "6) BUSINESS TRUTH: use the real name, services, contact, hours, reviews — no placeholder lorem or fake addresses.",
        f"7) LANGUAGE: the entire site experience must be in {language}.",
        "",
        "FORBIDDEN:",
        "- Generic white marketing pages with purple gradients",
        "- Flat Bootstrap-style cards in neat rows",
        "- Static pages with weak or no animation",
        "- Clipart icons instead of crafted 3D / media",
        "- Inter/Roboto-only boring typography stacks",
        "- Inventing a different logo than the SVG supplied",
        "- Ignoring Google photo references when provided",
        "- Repeating a previous variant's layout for this business",
        f"- Writing the site in a language other than {language}",
        "",
        f"QUALITY BAR: {variant.quality_bar}",
        "",
        f"CLIENT / BUSINESS: {business.name}",
        f"CATEGORY: {category}",
        f"SITE LANGUAGE: {language}",
    ]

    if business.address:
        lines.append(f"LOCATION: {business.address}")
    if business.phone:
        lines.append(f"PHONE: {business.phone}")
    if business.hours:
        lines.append(f"HOURS: {business.hours.split(';')[0]}")
    if business.rating and business.review_count:
        lines.append(f"SOCIAL PROOF: {business.rating}★ from {business.review_count} reviews")
    if business.description:
        lines.append(f"ABOUT: {business.description[:280]}")
    if business.products:
        lines.append("SERVICES/PRODUCTS: " + "; ".join(
            f"{p.name}{(' (' + p.price + ')') if p.price else ''}" for p in business.products[:6]
        ))
    if business.reviews:
        snippets = []
        for r in business.reviews[:3]:
            text = (r.text or "").strip()
            if not text:
                continue
            author = r.author or "Customer"
            snippets.append(f'"{text[:140]}" — {author}')
        if snippets:
            lines.append("REAL REVIEW SNIPPETS: " + " | ".join(snippets))

    if svg:
        lines.append(logo_svg_prompt_block(svg, business.name))

    lines.extend(format_photo_references(photo_uris))

    lines.extend([
        "",
        "BRAND BOOK (hard constraints — use exactly; never change these facts):",
        f"- Palette hex: {colors}",
        f"- Tone of voice: {tone}",
        f"- Logo direction: {logo}",
        f"- Typography: {fonts}",
        f"- Imagery style: {imagery}",
        f"- Pricing / positioning: {pricing}",
        f"- Content themes: {themes}",
    ])
    if book.logo_url:
        lines.append(f"- Logo URL reference: {book.logo_url}")
    if book.notes:
        lines.append(f"- Notes: {book.notes}")

    lines.extend(format_variant_block(variant, business.name))

    lines.extend([
        "",
        "MOTION IMPLEMENTATION NOTES:",
        f"- Deliver the assigned animation system: {variant.animation}",
        f"- Signature interaction: {variant.interaction}",
        "- Page load choreography + scroll reveals + hover states on all interactive modules",
        "- Keep motion smooth and premium — never random or chaotic",
        "",
        "OUTPUT QUALITY CHECK:",
        f"If this does not look unmistakably custom for {business.name}, with brand colors {colors}, the official SVG logo,",
        f"media mode {variant.media_mode}, all copy in {language}, and Awwwards-level craft, it is wrong — redesign until it does.",
        "Mobile responsive while keeping cinematic presence.",
    ])

    return "\n".join(lines)


def build_figma_make_url(
    business: Business,
    *,
    logo_svg: str | None = None,
    photo_uris: list[str] | None = None,
    variant=None,
    language: str = "English",
) -> str:
    prompt = _build_make_prompt(
        business,
        logo_svg=logo_svg,
        photo_uris=photo_uris,
        variant=variant,
        language=language,
    )
    compressed = lzstring.LZString.compressToEncodedURIComponent(prompt)
    return f"{FIGMA_MAKE_BASE}#prompt={compressed}"


async def create_site_for_business(
    business: Business,
    *,
    connected: bool = False,
    openai_api_key: str | None = None,
    language: str = "English",
) -> dict:
    if not connected and not is_figma_connected():
        raise ValueError("Figma API not connected. Add your token and click Connect.")

    if not business.brand_book or business.brand_book.status != "ready":
        business = business.model_copy(update={"brand_book": heuristic_brand_book(business)})

    language = (language or "English").strip() or "English"
    variant = pick_make_variant(random.Random())

    svg = await resolve_logo_svg(
        business,
        business.brand_book,
        api_key=openai_api_key,
    )
    book = business.brand_book.model_copy(update={"logo_svg": svg})
    business = business.model_copy(update={"brand_book": book})

    photo_uris = await fetch_place_photo_uris(business.place_id)

    prompt = _build_make_prompt(
        business,
        logo_svg=svg,
        photo_uris=photo_uris,
        variant=variant,
        language=language,
    )
    make_url = build_figma_make_url(
        business,
        logo_svg=svg,
        photo_uris=photo_uris,
        variant=variant,
        language=language,
    )

    return {
        "make_url": make_url,
        "prompt": prompt,
        "business_name": business.name,
        "has_brand_book": bool(business.brand_book),
        "has_logo_svg": bool(svg),
        "photo_count": len(photo_uris),
        "variant": variant.to_meta(),
        "language": language,
    }
