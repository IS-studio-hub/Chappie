"""Resolve a business logo as inline SVG for Figma Make prompts.

Order of preference:
1) Existing brand_book.logo_svg
2) Fetch real SVG from brand_book.logo_url (when it is SVG)
3) OpenAI-designed SVG from name + brand book (when API key available)
4) Deterministic wordmark/monogram SVG from name + brand colors
"""

from __future__ import annotations

import html
import re
from xml.sax.saxutils import escape as xml_escape

import httpx

from app.config import settings
from app.models import BrandBook, Business

OPENAI_API_BASE = "https://api.openai.com/v1"
MAX_SVG_CHARS = 4500


def _safe_hex(color: str | None, fallback: str) -> str:
    if not color:
        return fallback
    c = color.strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?", c):
        return c
    return fallback


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name or "") if p]
    if not parts:
        return "B"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def _display_name(name: str, max_len: int = 28) -> str:
    text = (name or "Business").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def generate_wordmark_svg(business: Business, book: BrandBook | None = None) -> str:
    """Compact, brand-colored SVG wordmark + monogram mark."""
    colors = (book.colors if book and book.colors else None) or ["#1F3A5F", "#F4F1EA", "#2A2A2A"]
    primary = _safe_hex(colors[0] if len(colors) > 0 else None, "#1F3A5F")
    secondary = _safe_hex(colors[1] if len(colors) > 1 else None, "#F4F1EA")
    accent = _safe_hex(colors[2] if len(colors) > 2 else None, primary)
    name = _display_name(business.name)
    initials = _initials(business.name)
    name_esc = xml_escape(name)
    init_esc = xml_escape(initials)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 72" role="img" '
        f'aria-label="{xml_escape(business.name or "Logo")}">'
        f'<rect width="360" height="72" fill="none"/>'
        f'<rect x="4" y="10" width="52" height="52" rx="14" fill="{primary}"/>'
        f'<text x="30" y="46" text-anchor="middle" font-family="Georgia, \'Times New Roman\', serif" '
        f'font-size="22" font-weight="700" fill="{secondary}">{init_esc}</text>'
        f'<text x="72" y="46" font-family="system-ui, -apple-system, Segoe UI, sans-serif" '
        f'font-size="26" font-weight="700" letter-spacing="-0.02em" fill="{accent}">{name_esc}</text>'
        f"</svg>"
    )


def _extract_svg(raw: str) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown fences if model wrapped the SVG
    fence = re.search(r"```(?:svg|xml)?\s*(.*?)```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    match = re.search(r"<svg\b[\s\S]*?</svg>", text, re.I)
    if not match:
        return None
    svg = match.group(0).strip()
    if len(svg) > MAX_SVG_CHARS:
        return None
    # Basic sanity: must look like SVG and not be a huge data dump
    if "xmlns" not in svg and "<path" not in svg and "<text" not in svg:
        return None
    return svg


async def fetch_svg_from_url(url: str | None) -> str | None:
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return None

    looks_svg = bool(re.search(r"\.svg(\?|#|$)", url, re.I)) or "svg" in url.lower()
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={"Accept": "image/svg+xml,text/plain,*/*"},
            )
    except Exception:
        return None

    if response.status_code != 200:
        return None

    content_type = (response.headers.get("content-type") or "").lower()
    body = response.text or ""
    if "svg" in content_type or looks_svg or "<svg" in body[:500].lower():
        return _extract_svg(body)
    return None


async def generate_logo_svg_openai(
    business: Business,
    book: BrandBook,
    api_key: str,
) -> str | None:
    colors = ", ".join(book.colors[:5]) if book.colors else "#1F3A5F, #F4F1EA"
    direction = book.logo_description or f"distinctive wordmark for {business.name}"
    prompt = f"""Design a real, production-ready logo as a single inline SVG for this local business.

Business name: {business.name}
Category: {business.category or 'local business'}
Brand colors (use these hex values): {colors}
Logo direction: {direction}

Requirements:
- Return ONLY the SVG markup (no markdown, no explanation).
- Root element must be <svg xmlns="http://www.w3.org/2000/svg" ...>
- Compact viewBox (e.g. 0 0 320 80 or 0 0 120 120). Keep file small (<3500 chars).
- Prefer a mark + wordmark, or a strong monogram if the name is long.
- Use brand colors. Transparent background. Vector shapes/text only (no external images, no base64).
- Must clearly read as the logo for "{business.name}".
"""
    payload = {
        "model": settings.openai_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a brand designer who outputs only compact SVG logo markup.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "max_tokens": 1200,
    }
    try:
        async with httpx.AsyncClient(timeout=40.0) as client:
            response = await client.post(
                f"{OPENAI_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception:
        return None

    if response.status_code != 200:
        return None
    try:
        raw = response.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
    return _extract_svg(raw or "")


async def resolve_logo_svg(
    business: Business,
    book: BrandBook | None = None,
    *,
    api_key: str | None = None,
) -> str:
    """Return SVG markup to embed in the Figma Make prompt."""
    book = book or business.brand_book
    if book and book.logo_svg:
        extracted = _extract_svg(book.logo_svg)
        if extracted:
            return extracted

    if book and book.logo_url:
        fetched = await fetch_svg_from_url(book.logo_url)
        if fetched:
            return fetched

    key = (api_key or settings.openai_api_key or "").strip()
    if key and book:
        ai_svg = await generate_logo_svg_openai(business, book, key)
        if ai_svg:
            return ai_svg

    return generate_wordmark_svg(business, book)


def logo_svg_prompt_block(svg: str, business_name: str) -> str:
    """Instructions + SVG for the Figma Make brief."""
    safe_name = html.escape(business_name or "the business", quote=False)
    return "\n".join([
        "",
        "OFFICIAL BUSINESS LOGO (SVG) — MANDATORY:",
        f"Use this EXACT SVG as the real logo for {safe_name} in the nav, favicon-style mark, and footer.",
        "Do NOT invent a different logo. Do NOT replace it with a generic icon or placeholder.",
        "Paste/embed this SVG markup as-is (you may scale it; keep proportions and colors):",
        "```svg",
        svg.strip(),
        "```",
    ])
