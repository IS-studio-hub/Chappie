"""Large-plan marketing campaign generator (concept + posts + reels + images)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

import httpx

from app.config import settings
from app.models import (
    Business,
    CampaignReel,
    CampaignResponse,
    CampaignStaticPost,
)
from app.services.brand_book import (
    brand_book_to_prompt_block,
    build_brand_book_for_business,
    heuristic_brand_book,
)

OPENAI_API_BASE = "https://api.openai.com/v1"
CampaignGoal = Literal["awareness", "leads", "engagement"]


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


def pick_campaign_goal(business: Business, requested: str = "auto") -> CampaignGoal:
    req = (requested or "auto").strip().lower()
    if req in ("awareness", "leads", "engagement"):
        return req  # type: ignore[return-value]

    flags = business.website_flags or {}
    opp = business.website_opportunity_score or 0
    reviews = business.review_count or 0
    has_email = bool(business.contact_email or (business.contact_emails or []))
    has_booking = bool(business.appointment_url) or flags.get("missing_booking") is False

    if opp >= 70 or flags.get("no_website") or flags.get("outdated_website"):
        return "leads" if has_email or has_booking else "awareness"
    if reviews >= 40:
        return "engagement"
    if has_email or has_booking:
        return "leads"
    return "awareness"


def _business_context(business: Business) -> str:
    socials = ", ".join(
        f"{k}: {v}" for k, v in (business.social_profiles or {}).items()
    ) or "none"
    products = ", ".join(
        f"{p.name}{(' — ' + p.price) if p.price else ''}"
        for p in (business.products or [])[:8]
    ) or "unknown"
    review_bits = []
    for r in (business.reviews or [])[:3]:
        text = (r.text or "").strip()
        if text:
            review_bits.append(f"- {(r.author or 'Customer')}: {text[:180]}")
    reviews_block = "\n".join(review_bits) or "- none"
    signals = ", ".join(business.website_opportunity_signals or []) or "none"
    flags = ", ".join(k for k, v in (business.website_flags or {}).items() if v) or "none"

    return f"""Business name: {business.name}
Category: {business.category or 'unknown'}
Address / area: {business.address or 'unknown'}
Description: {(business.description or '')[:500] or 'unknown'}
Rating: {business.rating or 'n/a'} ({business.review_count or 0} reviews)
Phone: {business.phone or 'n/a'}
Website: {business.website_url or ('none' if not business.has_website else 'unknown')}
Products / services: {products}
Socials: {socials}
Website opportunity: {business.website_opportunity_score or 'n/a'} ({business.website_opportunity_label or ''})
Opportunity signals: {signals}
Digital flags: {flags}
Sample reviews:
{reviews_block}
"""


async def _ensure_brand_book(business: Business, api_key: str | None) -> Business:
    if business.brand_book and business.brand_book.status == "ready":
        return business
    try:
        book = await build_brand_book_for_business(business, api_key=api_key)
    except Exception:
        book = heuristic_brand_book(business)
    return business.model_copy(update={"brand_book": book})


def _normalize_posts(raw_posts: Any) -> list[CampaignStaticPost]:
    posts: list[CampaignStaticPost] = []
    if not isinstance(raw_posts, list):
        return posts
    for i, item in enumerate(raw_posts[:5], start=1):
        if not isinstance(item, dict):
            continue
        tags = item.get("hashtags") or []
        if isinstance(tags, str):
            tags = [t.strip().lstrip("#") for t in tags.replace(",", " ").split() if t.strip()]
        tags = [str(t).lstrip("#") for t in tags if str(t).strip()][:12]
        posts.append(
            CampaignStaticPost(
                id=i,
                title=str(item.get("title") or f"Post {i}").strip()[:120],
                caption=str(item.get("caption") or "").strip()[:2200],
                hashtags=tags,
                cta=str(item.get("cta") or "").strip()[:200],
                image_prompt=str(item.get("image_prompt") or "").strip()[:1200],
            )
        )
    return posts


def _normalize_reels(raw_reels: Any) -> list[CampaignReel]:
    reels: list[CampaignReel] = []
    if not isinstance(raw_reels, list):
        return reels
    for i, item in enumerate(raw_reels[:5], start=1):
        if not isinstance(item, dict):
            continue
        on_screen = item.get("on_screen_text") or item.get("on_screen") or []
        if isinstance(on_screen, str):
            on_screen = [on_screen]
        on_screen = [str(x).strip() for x in on_screen if str(x).strip()][:8]
        try:
            duration = int(item.get("duration_sec") or item.get("duration") or 30)
        except (TypeError, ValueError):
            duration = 30
        duration = max(10, min(90, duration))
        reels.append(
            CampaignReel(
                id=i,
                title=str(item.get("title") or f"Reel {i}").strip()[:120],
                hook=str(item.get("hook") or "").strip()[:400],
                script=str(item.get("script") or "").strip()[:4000],
                on_screen_text=on_screen,
                cta=str(item.get("cta") or "").strip()[:200],
                duration_sec=duration,
            )
        )
    return reels


async def _generate_concept_json(
    business: Business,
    goal: CampaignGoal,
    api_key: str,
) -> dict:
    brand_block = brand_book_to_prompt_block(business.brand_book, business.name)
    goal_brief = {
        "awareness": "Grow local brand awareness and recognition in the area.",
        "leads": "Drive inquiries, bookings, calls, or form fills from ideal customers.",
        "engagement": "Spark comments, shares, saves, and conversation with the local audience.",
    }[goal]

    prompt = f"""You are a senior social media strategist for local businesses.
Create ONE cohesive Instagram/Facebook/TikTok marketing campaign concept.

Campaign goal: {goal} — {goal_brief}

Use ONLY the business data and brand book below. Do not invent fake awards, locations, or offers.
The concept must realistically improve {goal} for this specific business.

{_business_context(business)}
{brand_block}

Return JSON with:
{{
  "concept_title": "short campaign name",
  "concept_summary": "2-4 sentences describing the campaign idea and how it helps {goal}",
  "hook": "one memorable campaign hook / tagline",
  "primary_cta": "main call to action",
  "why_it_works": "1-2 sentences tying the idea to this business's data/signals",
  "brand_notes": "how tone/colors/imagery from the brand book show up in the campaign",
  "static_posts": [
    {{
      "title": "post title",
      "caption": "full social caption with line breaks as \\n",
      "hashtags": ["tag1", "tag2"],
      "cta": "post CTA",
      "image_prompt": "detailed image generation prompt for a square social photo. Must be photorealistic or polished brand photography matching imagery_style. ABSOLUTELY NO text, letters, words, logos, watermarks, or captions inside the image. Describe scene, lighting, mood, and brand color palette only."
    }}
  ],
  "reels": [
    {{
      "title": "reel title",
      "hook": "first 3 seconds spoken/visual hook",
      "script": "full spoken script with timing cues",
      "on_screen_text": ["short overlay line 1", "line 2"],
      "cta": "end CTA",
      "duration_sec": 30
    }}
  ]
}}

Requirements:
- Exactly 5 static_posts and exactly 5 reels.
- Captions and scripts must match the brand tone.
- Each static post needs a unique image_prompt with NO text in the image.
- Reels should be filmable by a small local business (phone-friendly).
"""

    payload = {
        "model": settings.openai_model or "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You create practical local-business social campaigns. "
                    "Respond with valid JSON only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.55,
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code != 200:
        detail = response.text[:300]
        raise ValueError(f"OpenAI campaign concept failed ({response.status_code}): {detail}")
    raw = response.json()["choices"][0]["message"]["content"]
    data = _parse_json(raw or "")
    if not data:
        raise ValueError("OpenAI returned an empty campaign concept.")
    return data


def _enrich_image_prompt(prompt: str, business: Business) -> str:
    book = business.brand_book
    colors = ", ".join((book.colors if book else []) or []) or "natural brand colors"
    imagery = (book.imagery_style if book else None) or "clean professional photography"
    base = (prompt or "").strip()
    if not base:
        base = (
            f"Lifestyle photograph representing {business.name}, "
            f"a local {business.category or 'business'}, mood: {imagery}"
        )
    suffix = (
        f" Visual style: {imagery}. Brand color accents: {colors}. "
        "Square 1:1 composition for Instagram. Photorealistic. "
        "CRITICAL: no text, no typography, no letters, no logos, no watermarks, "
        "no signs with readable words anywhere in the image."
    )
    combined = f"{base.rstrip('.')}."
    if "no text" not in combined.lower():
        combined = f"{combined} {suffix}"
    return combined[:3900]


async def _generate_post_image(prompt: str, api_key: str) -> str:
    """Return an image URL or data URL from OpenAI Images API."""
    model = (settings.openai_image_model or "dall-e-3").strip()
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
    }
    # dall-e-3 accepts quality; gpt-image-* does not use response_format
    if model.startswith("dall-e"):
        payload["quality"] = "standard"

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code != 200:
        # Fallback to dall-e-3 if another model fails
        if model != "dall-e-3":
            return await _generate_post_image_dalle3(prompt, api_key)
        raise ValueError(
            f"Image generation failed ({response.status_code}): {response.text[:240]}"
        )

    return _extract_image_src(response.json())


def _extract_image_src(payload: dict) -> str:
    data = payload.get("data") or []
    if not data:
        raise ValueError("Image generation returned no data.")
    item = data[0] or {}
    url = item.get("url")
    if url:
        return url
    b64 = item.get("b64_json")
    if b64:
        return f"data:image/png;base64,{b64}"
    raise ValueError("Image generation returned neither url nor b64_json.")


async def _generate_post_image_dalle3(prompt: str, api_key: str) -> str:
    payload = {
        "model": "dall-e-3",
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "quality": "standard",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code != 200:
        raise ValueError(f"DALL·E 3 failed ({response.status_code}): {response.text[:240]}")
    return _extract_image_src(response.json())


async def _attach_images(
    posts: list[CampaignStaticPost],
    business: Business,
    api_key: str,
) -> list[CampaignStaticPost]:
    async def one(post: CampaignStaticPost) -> CampaignStaticPost:
        prompt = _enrich_image_prompt(post.image_prompt, business)
        try:
            url = await _generate_post_image(prompt, api_key)
            return post.model_copy(update={"image_prompt": prompt, "image_url": url})
        except Exception as e:
            return post.model_copy(
                update={
                    "image_prompt": prompt,
                    "image_error": str(e)[:240],
                }
            )

    results = await asyncio.gather(*[one(p) for p in posts])
    return list(results)


async def generate_marketing_campaign(
    business: Business,
    *,
    api_key: str,
    goal: str = "auto",
) -> CampaignResponse:
    key = (api_key or "").strip()
    if not key:
        raise ValueError("Connect OpenAI in Integrations before creating a campaign.")

    business = await _ensure_brand_book(business, key)
    resolved_goal = pick_campaign_goal(business, goal)
    data = await _generate_concept_json(business, resolved_goal, key)

    posts = _normalize_posts(data.get("static_posts") or data.get("posts"))
    reels = _normalize_reels(data.get("reels"))

    # Pad if model returned fewer than 5
    while len(posts) < 5:
        n = len(posts) + 1
        posts.append(
            CampaignStaticPost(
                id=n,
                title=f"Post {n}",
                caption=f"Discover what makes {business.name} special in your neighborhood.",
                hashtags=["local", "smallbusiness"],
                cta="Learn more",
                image_prompt=(
                    f"Photorealistic local business scene for {business.name}, "
                    f"{business.category or 'shop'}, no text in image"
                ),
            )
        )
    while len(reels) < 5:
        n = len(reels) + 1
        reels.append(
            CampaignReel(
                id=n,
                title=f"Reel {n}",
                hook=f"Quick look inside {business.name}",
                script=f"Show the space, a happy customer moment, and invite people to visit {business.name}.",
                on_screen_text=[business.name, "Visit us"],
                cta="Follow for more",
                duration_sec=25,
            )
        )

    posts = await _attach_images(posts[:5], business, key)

    return CampaignResponse(
        business_name=business.name,
        goal=resolved_goal,
        concept_title=str(data.get("concept_title") or f"{business.name} Campaign").strip()[:160],
        concept_summary=str(data.get("concept_summary") or "").strip()[:2000],
        hook=str(data.get("hook") or "").strip()[:400],
        primary_cta=str(data.get("primary_cta") or "").strip()[:200],
        why_it_works=str(data.get("why_it_works") or "").strip()[:800],
        brand_notes=str(data.get("brand_notes") or "").strip()[:800],
        static_posts=posts,
        reels=reels[:5],
    )
