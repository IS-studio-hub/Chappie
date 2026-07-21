import asyncio
import contextvars
import json
import re
from urllib.parse import quote_plus

import httpx

from app.config import settings
from app.models import Business
from app.services.email_finder import (
    extract_emails_from_text,
    merge_emails,
    pick_best_email,
    rank_emails,
)
from app.services.social_discovery import detect_social_platform, merge_social_profiles, normalize_social_url

OPENAI_API_BASE = "https://api.openai.com/v1"

# Process-level key only for optional system/default connection (not per-request)
_api_key: str | None = None
# Per-task key so concurrent searches never cross-bill OpenAI accounts
_request_api_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "openai_request_api_key", default=None
)


def _active_api_key(explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    ctx = _request_api_key.get()
    if ctx and ctx.strip():
        return ctx.strip()
    return _api_key


def is_openai_connected() -> bool:
    return _active_api_key() is not None


def get_openai_status() -> dict:
    if not _active_api_key():
        return {"connected": False}
    return {"connected": True, "model": settings.openai_model}


def init_openai_from_settings() -> None:
    global _api_key
    key = (settings.openai_api_key or "").strip()
    if key:
        _api_key = key


async def connect_openai(api_key: str) -> dict:
    global _api_key

    api_key = api_key.strip()
    if not api_key:
        raise ValueError("OpenAI API key is required")
    if not api_key.startswith("sk-"):
        raise ValueError("Invalid OpenAI API key format (should start with sk-)")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{OPENAI_API_BASE}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )

    if response.status_code == 401:
        raise ValueError("Invalid OpenAI API key.")
    if response.status_code != 200:
        raise ValueError(f"OpenAI API error: {response.status_code}")

    _api_key = api_key
    return get_openai_status()


def disconnect_openai() -> None:
    global _api_key
    _api_key = None


def _apply_emails(business: Business, emails: list[str], source: str | None = None) -> Business:
    all_emails = rank_emails(
        merge_emails(business.contact_emails, business.contact_email, emails),
        business.name,
    )
    if not all_emails:
        return business
    updates: dict = {
        "contact_emails": all_emails,
        "contact_email": all_emails[0],
    }
    if source:
        updates["email_source"] = source
    elif not business.email_source:
        updates["email_source"] = "openai"
    return business.model_copy(update=updates)


def _missing_fields(business: Business) -> list[str]:
    missing: list[str] = []
    if not business.address:
        missing.append("address")
    if not business.phone:
        missing.append("phone")
    if not business.contact_email and not business.contact_emails:
        missing.append("contact_email")
    if not business.hours:
        missing.append("hours")
    if not business.description:
        missing.append("description")
    if not business.province:
        missing.append("province")
    if not business.social_profiles:
        missing.append("social_profiles")
    return missing


def _city_from_business(business: Business) -> str:
    if not business.address:
        return ""
    parts = [p.strip() for p in business.address.split(",")]
    if len(parts) >= 3:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


def _business_context(business: Business) -> str:
    lines = [f"Business name: {business.name}"]
    if business.category:
        lines.append(f"Category: {business.category}")
    if business.address:
        lines.append(f"Address: {business.address}")
    if business.phone:
        lines.append(f"Phone: {business.phone}")
    emails = merge_emails(business.contact_emails, business.contact_email)
    if emails:
        lines.append(f"Known emails: {', '.join(emails)}")
    if business.hours:
        lines.append(f"Hours: {business.hours}")
    if business.description:
        lines.append(f"Description: {business.description[:400]}")
    if business.province:
        lines.append(f"Province/State: {business.province}")
    if business.rating:
        lines.append(f"Rating: {business.rating} ({business.review_count or 0} reviews)")
    if business.google_maps_url:
        lines.append(f"Google Maps: {business.google_maps_url}")
    if business.social_profiles:
        lines.append("Known social profiles:")
        for platform, url in business.social_profiles.items():
            lines.append(f"  - {platform}: {url}")
    if business.categories:
        lines.append(f"Categories: {', '.join(business.categories[:5])}")
    return "\n".join(lines)


async def _gather_web_research(business: Business) -> tuple[str, list[str]]:
    city = _city_from_business(business)
    location = f"{city} {business.province or ''}".strip()
    name = business.name
    queries = [
        f'"{name}" {location} email contact',
        f'"{name}" {city} "@" email OR contact OR info@ OR hello@',
        f'"{name}" corporate contact email Canada',
    ]

    snippets: list[str] = []
    found_emails: list[str] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, headers=headers) as client:
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
                    text = re.sub(r"\s+", " ", text).strip()
                    for e in extract_emails_from_text(text):
                        if e not in found_emails:
                            found_emails.append(e)
                    if text:
                        snippets.append(text[:2200])
                except Exception:
                    continue
            if len(snippets) >= 3:
                break

    return "\n\n---\n\n".join(snippets)[:7000], found_emails


def _extract_response_text(payload: dict) -> str:
    """Pull assistant text from Responses API payload."""
    if not payload:
        return ""
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]

    chunks: list[str] = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if isinstance(part, dict) and part.get("type") in ("output_text", "text"):
                    text = part.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
                    elif isinstance(text, dict) and text.get("value"):
                        chunks.append(str(text["value"]))
        elif item.get("type") == "output_text" and item.get("text"):
            chunks.append(str(item["text"]))
    return "\n".join(chunks)


async def _openai_web_search(prompt: str) -> str | None:
    """ChatGPT-style web search via OpenAI Responses API."""
    key = _active_api_key()
    if not key:
        return None

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    # Try modern web_search tool, then preview variant
    for tool_type in ("web_search", "web_search_preview"):
        payload = {
            "model": settings.openai_model,
            "tools": [{"type": tool_type}],
            "tool_choice": "auto",
            "input": prompt,
        }
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    f"{OPENAI_API_BASE}/responses",
                    headers=headers,
                    json=payload,
                )
            if response.status_code == 200:
                text = _extract_response_text(response.json())
                if text.strip():
                    return text
            # Invalid tool type — try next
            if response.status_code in (400, 404, 422):
                continue
        except Exception:
            continue
    return None


async def _openai_chat(
    messages: list[dict],
    temperature: float = 0.1,
    *,
    api_key: str | None = None,
    json_mode: bool = True,
) -> str | None:
    key = _active_api_key(api_key)
    if not key:
        return None
    payload: dict = {
        "model": settings.openai_model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OPENAI_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if response.status_code != 200:
        return None
    return response.json()["choices"][0]["message"]["content"]


async def translate_outreach_email(
    subject: str,
    body: str,
    language: str,
    *,
    api_key: str | None = None,
) -> tuple[str, str]:
    """Rewrite outreach subject+body into the requested language. Preserves names/URLs/emails."""
    lang = (language or "").strip()
    if not lang or lang.lower() in ("english", "en", "en-us", "en-gb"):
        return subject, body

    key = _active_api_key(api_key) or (settings.openai_api_key or "").strip()
    if not key:
        raise ValueError(
            "Connect OpenAI in Integrations to generate emails in other languages."
        )

    prompt = (
        f"Translate this outreach email into {lang}.\n"
        "Return JSON with keys: subject, body.\n"
        "Rules:\n"
        "- Keep the same meaning, tone, and structure (greeting, bullets, sign-off).\n"
        "- Do NOT translate business names, person names, URLs, email addresses, or phone numbers.\n"
        "- Keep bullet points as bullet points.\n"
        "- Use natural, professional business language for the target language.\n"
        "- body must be plain text (no HTML).\n\n"
        f"Subject:\n{subject}\n\n"
        f"Body:\n{body}"
    )
    raw = await _openai_chat(
        [
            {
                "role": "system",
                "content": "You translate business outreach emails. Respond with JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        api_key=key,
        json_mode=True,
    )
    if not raw:
        raise ValueError("Could not translate the email. Check your OpenAI key and try again.")

    data = _parse_json_object(raw)
    new_subject = (data.get("subject") or "").strip()
    new_body = (data.get("body") or "").strip()
    if not new_subject or not new_body:
        raise ValueError("Translation returned an incomplete email. Try again.")
    return new_subject[:200], new_body


def _parse_email_list(data: dict, business: Business) -> list[str]:
    emails: list[str] = []
    for key in ("contact_emails", "emails", "email_addresses"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    emails.extend(extract_emails_from_text(item))
                elif isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str):
                            emails.extend(extract_emails_from_text(v))
    for key in ("contact_email", "email", "best_email"):
        raw = data.get(key)
        if isinstance(raw, str):
            emails.extend(extract_emails_from_text(raw))
    # Also scrape any emails mentioned in free text fields
    for key in ("reason", "notes", "sources"):
        raw = data.get(key)
        if isinstance(raw, str):
            emails.extend(extract_emails_from_text(raw))
    return rank_emails(merge_emails(emails), business.name)


def _parse_json_object(raw: str) -> dict:
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


def _clean_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) < 10:
        return None
    return value.strip()


def _parse_ai_response(raw: str, business: Business) -> dict:
    data = _parse_json_object(raw)
    if not data:
        # Fall back to any emails in free text
        emails = extract_emails_from_text(raw)
        return {"contact_emails": rank_emails(emails, business.name)} if emails else {}

    result: dict = {}
    for field in ("address", "hours", "description", "province"):
        val = data.get(field)
        if isinstance(val, str) and val.strip():
            result[field] = val.strip()

    phone = _clean_phone(data.get("phone") if isinstance(data.get("phone"), str) else None)
    if phone:
        result["phone"] = phone

    emails = _parse_email_list(data, business)
    if not emails:
        emails = extract_emails_from_text(raw)
        emails = rank_emails(emails, business.name)
    if emails:
        result["contact_emails"] = emails
        result["contact_email"] = emails[0]
        result["email_source"] = "openai"

    social_raw = data.get("social_profiles")
    if isinstance(social_raw, dict):
        social: dict[str, str] = {}
        for key, url in social_raw.items():
            if isinstance(url, str) and url.startswith("http"):
                platform = detect_social_platform(url) or key.lower()
                social[platform] = normalize_social_url(url)
        if social:
            result["social_profiles"] = social

    return result


async def find_emails_with_openai(business: Business) -> tuple[list[str], str | None]:
    """
    ChatGPT-style email discovery: OpenAI web search + JSON extraction.
    Returns (emails, source).
    """
    if not _active_api_key():
        return [], None

    city = _city_from_business(business)
    location = f"{city} {business.province or ''}".strip()

    search_prompt = f"""Search the public web for contact email addresses for this business.

Business:
{_business_context(business)}

Goal: find ALL publicly listed emails I could use to send a website / digital services offer.
Include franchise/operator, corporate, store, info@, contact@, hello@, sales@, partnerships, etc.
Exclude media-only, investor-only, and noreply addresses when better options exist — but still list useful outreach emails.

Return ONLY valid JSON (no markdown):
{{
  "contact_emails": ["email1@example.com", "email2@example.com"],
  "contact_email": "best_outreach_email@example.com or null",
  "notes": "short note about each email purpose"
}}

Rules:
- Only include real emails you found or can verify from public sources.
- Prefer emails useful for business outreach / website offers.
- If this is a chain (e.g. Circle K), include corporate/franchise contact emails for {location or 'the local market'}.
- Do not invent emails.
"""

    # 1) OpenAI web search (same capability ChatGPT uses)
    web_text = await _openai_web_search(search_prompt)
    emails: list[str] = []
    if web_text:
        parsed = _parse_ai_response(web_text, business)
        emails = merge_emails(parsed.get("contact_emails"), extract_emails_from_text(web_text))
        emails = rank_emails(emails, business.name)
        if emails:
            return emails, "openai_web_search"

    # 2) Fallback: our search snippets + chat completion
    research, candidates = await _gather_web_research(business)
    if candidates:
        emails = rank_emails(candidates, business.name)

    chat_prompt = f"""Find public contact emails for this business for a website offer outreach.

Business:
{_business_context(business)}

Web research snippets:
{research or '(none)'}

Candidate emails already seen: {', '.join(candidates) if candidates else '(none)'}

Return JSON only:
{{
  "contact_emails": ["..."],
  "contact_email": "best email or null",
  "notes": "short"
}}
Only include real emails. Prefer outreach-friendly addresses. Do not invent.
"""
    content = await _openai_chat(
        [
            {"role": "system", "content": "You research public business contact emails and return JSON only."},
            {"role": "user", "content": chat_prompt},
        ]
    )
    if content:
        parsed = _parse_ai_response(content, business)
        emails = rank_emails(
            merge_emails(emails, parsed.get("contact_emails"), candidates),
            business.name,
        )

    if emails:
        return emails, "openai"
    return [], None


async def _fill_business_with_openai(business: Business) -> Business:
    if not _active_api_key():
        return business

    missing = _missing_fields(business)
    updates: dict = {}
    biz = business

    # Always run dedicated email hunt when no email yet
    if "contact_email" in missing:
        emails, source = await find_emails_with_openai(business)
        if emails:
            biz = _apply_emails(biz, emails, source)
            missing = [f for f in missing if f != "contact_email"]

    remaining = [f for f in missing if f != "contact_email"]
    if not remaining:
        return biz

    research, _ = await _gather_web_research(biz)
    content = await _openai_chat(
        [
            {"role": "system", "content": "You return accurate business contact research as JSON."},
            {
                "role": "user",
                "content": f"""Fill missing fields for this business.

{_business_context(biz)}

Web research:
{research or '(none)'}

Missing: {', '.join(remaining)}

Return JSON with keys among: address, phone, hours, description, province, social_profiles, contact_emails.
Use null when unknown. Do not invent emails/phones.
""",
            },
        ]
    )
    if content:
        parsed = _parse_ai_response(content, biz)
        for field in ("address", "phone", "hours", "description", "province"):
            if field in parsed and not getattr(biz, field):
                updates[field] = parsed[field]
        if parsed.get("social_profiles"):
            updates["social_profiles"] = merge_social_profiles(
                biz.social_profiles, parsed["social_profiles"]
            )
        if parsed.get("contact_emails") and not biz.contact_email:
            biz = _apply_emails(biz, parsed["contact_emails"], "openai")

    if updates:
        biz = biz.model_copy(update=updates)
    return biz


async def enrich_businesses_with_openai(
    businesses: list[Business],
    progress_callback=None,
    api_key: str | None = None,
) -> list[Business]:
    """Use OpenAI web search to find emails and fill missing fields."""
    key = (api_key or "").strip() or _active_api_key()
    if not key:
        return businesses

    token = _request_api_key.set(key)
    try:
        total = len(businesses)
        results: list[Business] = list(businesses)
        sem = asyncio.Semaphore(3)

        async def run_one(idx: int) -> None:
            biz = businesses[idx]
            needs_email = not biz.contact_email and not biz.contact_emails
            missing = _missing_fields(biz)
            if not needs_email and not missing:
                return
            async with sem:
                if progress_callback:
                    label = "AI finding emails" if needs_email else "AI filling gaps"
                    await progress_callback(idx, total, f"{label}: {biz.name}")
                try:
                    results[idx] = await _fill_business_with_openai(biz)
                except Exception:
                    pass

        await asyncio.gather(*[run_one(i) for i in range(total)])

        if progress_callback:
            found = sum(1 for b in results if b.contact_email or b.contact_emails)
            await progress_callback(total, total, f"AI enrichment complete — emails for {found}/{total}")

        return results
    finally:
        _request_api_key.reset(token)
