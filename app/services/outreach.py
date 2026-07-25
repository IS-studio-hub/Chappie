import hashlib
import html
import re

from app.config import settings
from app.models import Business, CampaignResponse
from app.services.email_templates import DEFAULT_TEMPLATE_ID, get_email_template, list_email_templates


CAMPAIGN_PLAIN_START = "—— Marketing campaign ——"
CAMPAIGN_PLAIN_END = "—— End campaign ——"



def _ban_fancy_dashes(text: str) -> str:
    """Never allow em/en dashes in outbound email copy."""
    if not text:
        return text
    text = text.replace("—", ", ")
    text = text.replace("–", "-")
    text = text.replace("−", "-")
    text = re.sub(r"[ \t]*,[ \t]+", ", ", text)
    text = re.sub(r" {2,}", " ", text)
    return text

def _first_name_from_business(name: str) -> str:
    return "there"


def _city_from_address(address: str | None) -> str:
    """Prefer a real city name (e.g. Toronto), not a province + postal code."""
    if not address:
        return "your area"
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return "your area"

    countries = {"canada", "usa", "united states", "united states of america", "us", "uk", "australia"}
    if parts[-1].lower() in countries:
        parts = parts[:-1]
    if not parts:
        return "your area"

    # Drop trailing "ON M4P 1Z2" / "Ontario" / ZIP-only segments
    postalish = re.compile(
        r"^([A-Z]{2}\s+)?[A-Z]\d[A-Z]\s*\d[A-Z]\d$|^[A-Z]{2}$|^\d{5}(-\d{4})?$",
        re.I,
    )
    while len(parts) > 1 and postalish.match(parts[-1].replace("  ", " ")):
        parts = parts[:-1]

    if len(parts) >= 2:
        candidate = parts[-1]
        # If last remaining looks like a street, use previous
        if re.match(r"^\d", candidate) and len(parts) >= 2:
            candidate = parts[-2]
        if candidate and not postalish.match(candidate):
            return candidate
        if len(parts) >= 2:
            return parts[-2]
    return parts[0]


def normalize_business_website(website: str = "") -> str:
    """Normalize a business website URL; empty if blank."""
    v = (website or "").strip()
    if not v:
        return ""
    if not re.match(r"^https?://", v, re.I):
        v = "https://" + v
    return v[:300].rstrip("/")


def parse_sender_info(
    sender_info: str,
    business_name: str = "",
    business_website: str = "",
) -> dict:
    """Public wrapper for sender Business Info parsing."""
    parsed = _parse_sender_info(sender_info)
    name = (business_name or "").strip()
    if name:
        parsed = {**parsed, "name": name[:80]}
    url = normalize_business_website(business_website)
    if url:
        parsed = {**parsed, "url": url}
    return parsed


def _parse_sender_info(sender_info: str) -> dict:
    text = (sender_info or "").strip()
    if not text:
        return {}

    url_match = re.search(r"https?://\S+", text)
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    url = url_match.group(0).rstrip(".,);]") if url_match else (settings.studio_url or "")
    email = email_match.group(0) if email_match else (settings.studio_email or "")

    working = text
    if url_match:
        working = working.replace(url_match.group(0), " ")
    if email_match:
        working = working.replace(email_match.group(0), " ")
    working = re.sub(r"\s+", " ", working).strip(" ,.-")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Prefer a short first line as the studio name
    name = ""
    body = working
    if lines:
        first = lines[0].strip()
        first_clean = re.sub(r"https?://\S+", "", first)
        first_clean = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "", first_clean).strip(" ,.-")
        looks_like_sentence = bool(
            re.search(
                r"\b(helps|builds|creates|designs|offers|provides|we\b|our\b|and|with)\b",
                first_clean,
                re.I,
            )
        ) or len(first_clean) > 48

        if first_clean and not looks_like_sentence:
            name = first_clean[:80]
            rest_lines = []
            for ln in lines[1:]:
                ln2 = re.sub(r"https?://\S+", " ", ln)
                ln2 = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", " ", ln2)
                ln2 = re.sub(r"\s+", " ", ln2).strip(" ,.-")
                if ln2 and ln2.lower() != name.lower():
                    rest_lines.append(ln2)
            body = " ".join(rest_lines) if rest_lines else working
        else:
            # "IS Studio helps businesses…" → name = "IS Studio"
            m = re.match(
                r"^([A-Z][\w&.\'’-]{0,40}?(?:\s+[A-Z][\w&.\'’-]{0,40}?){0,3})\s+"
                r"(helps|is|builds|creates|designs|offers|provides|we)\b",
                first_clean or working,
                re.I,
            )
            if m:
                name = m.group(1).strip()
            body = working

    if not name:
        # Fallback: first 1-3 capitalized tokens
        tokens = re.findall(r"[A-Za-z][\w&.\'’-]*", working)
        name = " ".join(tokens[:2]) if tokens else (settings.studio_name or "Our studio")
        if len(name) > 40:
            name = (settings.studio_name or "Our studio").strip()

    if name and body.lower().startswith(name.lower()):
        body = body[len(name) :].lstrip(" -,|:;")

    return {
        "name": name.strip() or (settings.studio_name or "Our studio"),
        "body": body.strip(),
        "url": url,
        "email": email,
        "raw": text,
    }


def _clean_sender_description(sender: dict) -> str:
    """Strip URLs/emails/name from Business Info so we can build a short pitch."""
    name = (sender.get("name") or "").strip()
    text = (sender.get("body") or sender.get("raw") or "").strip()
    if not text:
        return ""
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", " ", text)
    parts = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if name and ln.lower() == name.lower():
            continue
        parts.append(ln)
    text = " ".join(parts) if parts else text
    if name and text.lower().startswith(name.lower()):
        text = text[len(name) :].lstrip(" -,|:;")
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    return text


def _intro_from_business_info(sender: dict) -> str:
    """Short explanation of the sender's business, no links."""
    studio = (sender.get("name") or settings.studio_name or "Our studio").strip()
    desc = _clean_sender_description(sender)
    if not desc:
        return (
            f"{studio} helps local businesses show up online with clear, "
            f"professional websites and practical digital tools."
        )

    # Soften overly long / fishy marketing without inventing a new company story
    desc = re.sub(r"\s*\.\.+", ".", desc)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", desc) if s.strip()]
    intro = " ".join(sentences[:2]).strip()
    if not intro.endswith((".", "!", "?")):
        intro += "."
    if not re.match(rf"^{re.escape(studio)}\b", intro, re.I):
        # Lead with studio name when the pitch starts mid-thought ("helps businesses…")
        if re.match(r"^(helps|builds|creates|designs|offers|provides)\b", intro, re.I):
            intro = f"{studio} {intro[0].lower()}{intro[1:]}"
        else:
            intro = f"{studio}: {intro}"
    # Prefer a clean sentence cut over mid-word ellipsis
    if len(intro) > 320 and len(sentences) >= 2:
        first = sentences[0].strip()
        if not first.endswith((".", "!", "?")):
            first += "."
        if not re.match(rf"^{re.escape(studio)}\b", first, re.I):
            if re.match(r"^(helps|builds|creates|designs|offers|provides)\b", first, re.I):
                first = f"{studio} {first[0].lower()}{first[1:]}"
            else:
                first = f"{studio}: {first}"
        intro = first
    return intro

def _compress_pitch(desc: str, studio_name: str) -> str:
    """Turn Business Info into a short self-contained phrase (~55 chars)."""
    studio = (studio_name or "Our studio").strip()
    text = (desc or "").strip()
    if not text:
        return f"{studio} builds websites"

    replacements = [
        (r"\bhelps businesses build a stronger online presence\b", "builds modern websites"),
        (r"\bstronger online presence\b", "modern websites"),
        (r"\bwith modern websites\b", ""),
        (r"\bcutting[- ]edge\b", "modern"),
        (r"\brevolutionary\b", "practical"),
        (r"\bgrow your business online\b", "build clear websites"),
        (r"\bdigital experience studio\b", "web studio"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text, flags=re.I)

    for sep in ".!?;":
        if sep in text:
            text = text.split(sep)[0]
            break
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    if not text:
        return f"{studio} builds websites"

    first = text.split()[0].lower()
    verb_leads = {
        "builds", "helps", "creates", "designs", "makes", "offers",
        "provides", "crafts", "develops", "delivers",
    }
    if first in verb_leads and not text.lower().startswith(studio.lower()):
        text = f"{studio} {text[0].lower()}{text[1:]}"
    elif text[0].islower():
        text = text[0].upper() + text[1:]

    if len(text) > 55:
        text = text[:52].rsplit(" ", 1)[0].rstrip(" ,.-") + "…"
    return text


def _stable_pick(seed: str, options: list[str]) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(options)
    return options[idx]


def build_unique_email_subtitle(sender_info: str, business: Business) -> str:
    """Short unique inbox-preview line from the user's Business Info + recipient."""
    sender = _parse_sender_info(sender_info) if sender_info.strip() else {
        "name": settings.studio_name,
        "body": "",
        "raw": "",
    }
    studio = (sender.get("name") or settings.studio_name or "Our studio").strip()
    pitch = _compress_pitch(_clean_sender_description(sender), studio)
    city = _city_from_address(business.address)
    biz = (business.name or "your business").strip()
    cat = (business.category or "local business").strip().lower()

    seed = "|".join([
        business.place_id or "",
        biz,
        studio,
        pitch,
        city,
    ])
    options = [
        f"{pitch}: idea for {biz}.",
        f"Quick note for {biz} from {studio}.",
        f"{studio}: a short site idea for {biz}.",
        f"Saw {biz} in {city}: {pitch}",
        f"For {biz}: {pitch}",
        f"{pitch} Worth a look for {biz}.",
        f"{studio} x {biz}: {pitch}",
        f"One idea for {biz} ({cat}) from {studio}.",
        f"{biz}: {pitch}",
        f"About {biz}'s site: {pitch}",
    ]
    line = _stable_pick(seed, options)
    if len(line) > 78:
        line = line[:75].rsplit(" ", 1)[0].rstrip(" ,.-") + "…"
    return _ban_fancy_dashes(line)


def _positive_highlights(business: Business, city: str) -> list[str]:
    """Warm, specific standouts, not dry Category/Location bullets."""
    name = (business.name or "your business").strip()
    cat = (business.category or "").strip()
    highlights: list[str] = []

    if business.rating and business.review_count:
        highlights.append(
            f"Customers clearly trust you: {business.rating}★ from "
            f"{business.review_count} Google reviews."
        )
    elif business.rating:
        highlights.append(
            f"A {business.rating}-star Google rating says a lot about the experience you deliver."
        )
    elif business.review_count and business.review_count > 5:
        highlights.append(
            f"You've built real local proof with {business.review_count} Google reviews."
        )

    if cat:
        highlights.append(
            f"As a {cat.lower()} in {city}, you're right where neighborhood customers need you."
        )
    elif city and city != "your area":
        highlights.append(
            f"You have a solid presence in {city}, the kind of spot people already know and recommend."
        )

    desc = (business.description or "").strip()
    if desc:
        snippet = desc[:150].rstrip()
        if len(desc) > 150:
            snippet = snippet.rsplit(" ", 1)[0] + "…"
        highlights.append(snippet)

    if business.photo_count and business.photo_count >= 8:
        highlights.append(
            f"Your Google profile already shows personality. {business.photo_count} photos help people get a feel for {name} before they visit."
        )

    products = getattr(business, "products", None) or []
    if products:
        names = [p.name for p in products[:3] if getattr(p, "name", None)]
        if names:
            highlights.append(
                "You're already offering things people look for: "
                + ", ".join(names)
                + ("…" if len(products) > 3 else "")
                + "."
            )

    bb = getattr(business, "brand_book", None)
    tone = getattr(bb, "tone", None) if bb else None
    if tone:
        highlights.append(f"Your brand comes across as {tone.lower().rstrip('.')}. A website should reflect that same feel.")

    # Deduplicate and keep 2-4
    seen: set[str] = set()
    out: list[str] = []
    for h in highlights:
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= 4:
            break

    if not out:
        out = [
            f"{name} already has the fundamentals of a business people can trust.",
            f"A clear website would make it easier for new customers in {city} to find and choose you.",
        ]
    return out


def resolve_from_display_name(
    sender_info: str = "",
    user_name: str | None = None,
    business_name: str = "",
) -> str:
    """Human-friendly From name: explicit business name, else parsed info, else account name."""
    explicit = (business_name or "").strip()
    if explicit and not re.match(r"^https?://", explicit, re.I) and "@" not in explicit:
        return explicit[:80]
    sender = _parse_sender_info(sender_info)
    name = (sender.get("name") or "").strip()
    if name and not re.match(r"^https?://", name, re.I) and "@" not in name and len(name) <= 60:
        return name[:80]
    account = (user_name or "").strip()
    if account:
        return account[:80]
    return (settings.studio_name or "CH4PP!3").strip()[:80]


def append_compliance_footer(
    body: str,
    *,
    sender_name: str,
    sender_email: str,
) -> str:
    """Append a short unsubscribe line if not already present."""
    text = (body or "").rstrip()
    lower = text.lower()
    if "unsubscribe" in lower or "opt out" in lower or "opt-out" in lower:
        return text

    footer = (
        '\n\nIf this email isn\'t relevant, reply with "unsubscribe" '
        "and we won't contact you again."
    )
    return text + footer


_FIGMA_URL_RE = re.compile(
    r"https?://(?:www\.)?figma\.com/\S+",
    re.I,
)


def strip_campaign_plain_block(plain_body: str) -> str:
    """Remove the client-side campaign text block so HTML can render it separately."""
    text = plain_body or ""
    start = text.find(CAMPAIGN_PLAIN_START)
    if start == -1:
        return text
    end = text.find(CAMPAIGN_PLAIN_END, start)
    if end == -1:
        return (text[:start] + text[start:].replace(CAMPAIGN_PLAIN_START, "")).strip()
    cleaned = (text[:start] + text[end + len(CAMPAIGN_PLAIN_END) :]).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def build_marketing_campaign_html(
    campaign: CampaignResponse | dict | None,
    *,
    template_id: str = "",
) -> str:
    """HTML block with concept + each static post image and copy for the email bottom."""
    if not campaign:
        return ""
    if isinstance(campaign, dict):
        try:
            campaign = CampaignResponse.model_validate(campaign)
        except Exception:
            return ""

    theme = get_email_template(template_id)
    posts = list(campaign.static_posts or [])
    if not posts and not (campaign.concept_title or campaign.concept_summary):
        return ""

    goal_labels = {
        "awareness": "Awareness",
        "leads": "Leads / inquiries",
        "engagement": "Engagement",
    }
    goal = goal_labels.get(campaign.goal, campaign.goal or "Campaign")
    title = html.escape(campaign.concept_title or "Marketing campaign")
    summary = html.escape(campaign.concept_summary or "").replace("\n", "<br>\n")
    hook = html.escape(campaign.hook or "")
    primary_cta = html.escape(campaign.primary_cta or "")

    header_bits = [
        f"""
              <p style="margin:0 0 6px;color:{theme['eyebrow']};font-size:11px;letter-spacing:0.14em;text-transform:uppercase;font-weight:700;">
                Suggested marketing campaign
              </p>
              <h2 style="margin:0 0 8px;font-size:18px;font-weight:600;line-height:1.3;color:{theme['heading']};">{title}</h2>
              <p style="margin:0 0 10px;color:{theme['muted']};font-size:12px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">{html.escape(goal)}</p>
        """
    ]
    if summary:
        header_bits.append(
            f'<p style="margin:0 0 10px;color:{theme["body"]};line-height:1.55;font-size:14px;">{summary}</p>'
        )
    if hook:
        header_bits.append(
            f'<p style="margin:0 0 8px;color:{theme["body"]};line-height:1.5;font-size:14px;"><strong style="color:{theme["heading"]};">Hook:</strong> {hook}</p>'
        )
    if primary_cta:
        header_bits.append(
            f'<p style="margin:0 0 16px;color:{theme["body"]};line-height:1.5;font-size:14px;"><strong style="color:{theme["heading"]};">Primary CTA:</strong> {primary_cta}</p>'
        )

    post_blocks: list[str] = []
    for i, post in enumerate(posts):
        post_title = html.escape(post.title or f"Post {post.id or i + 1}")
        caption = html.escape(post.caption or "").replace("\n", "<br>\n")
        cta = html.escape(post.cta or "")
        tags = " ".join(
            f"#{html.escape(str(t).lstrip('#'))}" for t in (post.hashtags or []) if t
        )
        img_src = (post.image_url or "").strip()
        img_ok = img_src.startswith("https://") or img_src.startswith("http://") or img_src.startswith("data:image/")
        if img_ok:
            img_html = f"""
                    <img src="{html.escape(img_src, quote=True)}"
                         alt="{post_title}"
                         width="420"
                         style="display:block;width:100%;max-width:420px;height:auto;border:0;outline:none;border-radius:10px;">
            """
        else:
            note = html.escape(post.image_error or "Image unavailable")
            img_html = f"""
                    <div style="padding:28px 16px;text-align:center;background:{theme['page_bg']};border:1px dashed {theme['card_border']};border-radius:10px;color:{theme['muted']};font-size:13px;">
                      {note}
                    </div>
            """

        meta_bits = []
        if caption:
            meta_bits.append(
                f'<p style="margin:0 0 8px;color:{theme["body"]};line-height:1.55;font-size:14px;">{caption}</p>'
            )
        if cta:
            meta_bits.append(
                f'<p style="margin:0 0 6px;color:{theme["body"]};font-size:13px;"><strong style="color:{theme["heading"]};">CTA:</strong> {cta}</p>'
            )
        if tags:
            meta_bits.append(
                f'<p style="margin:0;color:{theme["muted"]};font-size:12px;line-height:1.45;">{tags}</p>'
            )

        post_blocks.append(
            f"""
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 18px;border:1px solid {theme['card_border']};border-radius:12px;overflow:hidden;background:{theme['card_bg']};">
                <tr>
                  <td style="padding:12px 12px 8px;color:{theme['heading']};font-size:14px;font-weight:700;">
                    Post {i + 1}: {post_title}
                  </td>
                </tr>
                <tr>
                  <td style="padding:0 12px 12px;">
                    {img_html}
                  </td>
                </tr>
                <tr>
                  <td style="padding:0 12px 14px;">
                    {''.join(meta_bits) or f'<p style="margin:0;color:{theme["muted"]};font-size:13px;">No caption</p>'}
                  </td>
                </tr>
              </table>
            """
        )

    return f"""
              <table width="100%" cellpadding="0" cellspacing="0" style="margin:28px 0 0;border-top:1px solid {theme['card_border']};">
                <tr>
                  <td style="padding-top:22px;">
                    {''.join(header_bits)}
                    {''.join(post_blocks)}
                  </td>
                </tr>
              </table>
    """


def build_outreach_email_html(
    *,
    studio_name: str,
    intro: str,
    body_sections_html: str,
    prototype_url: str = "",
    studio_email: str = "",
    studio_url: str = "",
    business_name: str = "",
    prototype_lead: str = "",
    prototype_follow: str = "",
    after_cta_html: str = "",
    template_id: str = "",
    logo_url: str = "",
    campaign_html: str = "",
) -> str:
    """Designed HTML card email using a selected color template."""
    theme = get_email_template(template_id)
    studio = html.escape(studio_name or "Our studio")
    eyebrow = studio
    title = html.escape(
        f"Website concept for {business_name}" if business_name else "Website concept"
    )
    intro_html = html.escape(intro).replace("\n", "<br>\n")

    logo_block = ""
    logo = (logo_url or "").strip()
    if logo and (
        logo.startswith("https://")
        or logo.startswith("http://")
        or logo.startswith("data:image/")
    ):
        logo_block = f"""
              <p style="margin:0 0 18px;">
                <img src="{html.escape(logo, quote=True)}"
                     alt="{studio}"
                     width="140"
                     style="display:block;max-width:140px;height:auto;border:0;outline:none;">
              </p>"""

    cta = ""
    if prototype_url.strip():
        href = html.escape(prototype_url.strip(), quote=True)
        lead = (prototype_lead or "").strip() or (
            f"I put together a website concept specifically for {business_name or 'your business'}:"
        )
        follow = (prototype_follow or "").strip() or (
            f"This is a working prototype. Click through it to see how {business_name or 'your business'} "
            "could look online with a modern, professional site tailored to your brand."
        )
        cta = f"""
              <p style="margin:0 0 8px;color:{theme['body']};line-height:1.55;font-size:15px;">
                {html.escape(lead)}
              </p>
              <p style="margin:0 0 16px;text-align:center;">
                <a href="{href}"
                   style="display:inline-block;background:{theme['cta_bg']};color:{theme['cta_text']};text-decoration:none;font-weight:700;padding:14px 28px;border-radius:999px;font-size:15px;">
                  Your New Website
                </a>
              </p>
              <p style="margin:0 0 20px;color:{theme['muted']};line-height:1.55;font-size:14px;">
                {html.escape(follow)}
              </p>"""

    after = after_cta_html or ""

    sig_bits = []
    if studio_email:
        sig_bits.append(
            f'<a href="mailto:{html.escape(studio_email, quote=True)}" style="color:{theme["link"]};text-decoration:none;">{html.escape(studio_email)}</a>'
        )
    if studio_url:
        sig_bits.append(
            f'<a href="{html.escape(studio_url, quote=True)}" style="color:{theme["link"]};word-break:break-all;">{html.escape(studio_url)}</a>'
        )
    sig_html = "<br>\n".join(sig_bits)

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:{theme['page_bg']};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{theme['page_bg']};padding:40px 16px;">
    <tr>
      <td align="center">
        <table width="100%" style="max-width:520px;background:{theme['card_bg']};border:1px solid {theme['card_border']};border-radius:14px;padding:32px;">
          <tr>
            <td style="color:{theme['text']};">
              {logo_block}
              <p style="margin:0 0 8px;color:{theme['eyebrow']};font-size:12px;letter-spacing:0.12em;text-transform:uppercase;font-weight:600;">{eyebrow}</p>
              <h1 style="margin:0 0 16px;font-size:24px;font-weight:600;line-height:1.25;color:{theme['heading']};">{title}</h1>
              <p style="margin:0 0 18px;color:{theme['body']};line-height:1.55;font-size:15px;">{intro_html}</p>
              {body_sections_html}
              {cta}
              {after}
              {campaign_html}
              <p style="margin:24px 0 0;color:{theme['muted']};font-size:13px;line-height:1.5;">Best regards,</p>
              <p style="margin:6px 0 0;color:{theme['muted']};font-size:13px;line-height:1.6;">{sig_html}</p>
              <p style="margin:24px 0 0;color:{theme['muted']};font-size:12px;line-height:1.5;">
                If this email isn't relevant, reply with "unsubscribe" and we won't contact you again.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def wrap_outreach_plain_as_html(
    plain_body: str,
    *,
    studio_name: str,
    studio_email: str = "",
    studio_url: str = "",
    business_name: str = "",
    prototype_url: str = "",
    template_id: str = "",
    logo_url: str = "",
    marketing_campaign: CampaignResponse | dict | None = None,
) -> str:
    """Wrap an edited plain-text body in the designed card; turn Figma links into a CTA."""
    theme = get_email_template(template_id)
    text = strip_campaign_plain_block(plain_body or "").strip()
    proto = (prototype_url or "").strip()
    if not proto:
        m = _FIGMA_URL_RE.search(text)
        if m:
            proto = m.group(0).rstrip(".,);]")

    # Remove prototype URL from body so we can replace with button
    body_wo_link = text
    if proto:
        body_wo_link = body_wo_link.replace(proto, "").strip()
        body_wo_link = re.sub(r"\n{3,}", "\n\n", body_wo_link)

    # Split intro (first paragraph) from the rest when possible
    parts = re.split(r"\n\s*\n", body_wo_link, maxsplit=1)
    intro = parts[0].strip() if parts else ""
    # Drop greeting from intro for card title area if present
    intro_clean = re.sub(r"^Hi\s+[^,\n]+,\s*", "", intro, flags=re.I).strip()

    rest = parts[1].strip() if len(parts) > 1 else ""
    # Avoid duplicating CTA copy that generate_send_email already includes
    rest = re.sub(
        r"I put together a website concept specifically for[^\n]*\n*",
        "",
        rest,
        flags=re.I,
    )
    rest = re.sub(
        r"This is a working prototype[^\n]*\n*",
        "",
        rest,
        flags=re.I,
    )
    rest = re.sub(
        r"Best regards.*$",
        "",
        rest,
        flags=re.I | re.S,
    ).strip()
    rest = re.sub(
        r'If this email isn\'t relevant.*$',
        "",
        rest,
        flags=re.I | re.S,
    ).strip()

    # Convert remaining plain text to HTML paragraphs / bullets
    sections: list[str] = []
    for block in re.split(r"\n\s*\n", rest):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if all(re.match(r"^\s*[•\-\*]\s+", ln) or not ln.strip() for ln in lines if ln.strip()):
            items = []
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                item = re.sub(r"^[•\-\*]\s+", "", ln)
                items.append(f"<li style=\"margin:0 0 6px;\">{html.escape(item)}</li>")
            sections.append(
                f'<ul style="margin:0 0 18px;padding-left:18px;color:{theme["body"]};line-height:1.5;font-size:15px;">'
                + "".join(items)
                + "</ul>"
            )
        else:
            sections.append(
                f'<p style="margin:0 0 16px;color:{theme["body"]};line-height:1.55;font-size:15px;">'
                + html.escape(block).replace("\n", "<br>\n")
                + "</p>"
            )

    campaign_html = build_marketing_campaign_html(
        marketing_campaign,
        template_id=template_id,
    )

    return build_outreach_email_html(
        studio_name=studio_name,
        intro=intro_clean or intro,
        body_sections_html="\n".join(sections),
        prototype_url=proto,
        studio_email=studio_email,
        studio_url=studio_url,
        business_name=business_name,
        template_id=template_id,
        logo_url=logo_url,
        campaign_html=campaign_html,
    )


def _html_p(text: str, theme: dict | None = None) -> str:
    color = (theme or get_email_template(DEFAULT_TEMPLATE_ID))["body"]
    return (
        f'<p style="margin:0 0 16px;color:{color};line-height:1.55;font-size:15px;">'
        + html.escape(text)
        + "</p>"
    )


def _html_bullets(items: list[str], theme: dict | None = None) -> str:
    color = (theme or get_email_template(DEFAULT_TEMPLATE_ID))["body"]
    lis = "".join(
        f'<li style="margin:0 0 6px;">{html.escape(item)}</li>' for item in items
    )
    return (
        f'<ul style="margin:0 0 18px;padding-left:18px;color:{color};line-height:1.5;font-size:15px;">'
        f"{lis}</ul>"
    )


def get_available_email_templates() -> list[dict]:
    return list_email_templates()


def generate_outreach(business: Business, sender_info: str = "") -> tuple[str, str]:
    """Return (subject, body) for a personalized outreach email."""
    subject, body, _html = generate_send_email(
        business,
        sender_info=sender_info or (settings.studio_name or "Our studio"),
        figma_prototype_link="",
        require_sender_info=False,
    )
    return subject, body


def _email_seed(business: Business, studio: str, salt: str = "") -> str:
    return "|".join([
        business.place_id or "",
        (business.name or "").strip().lower(),
        (studio or "").strip().lower(),
        salt,
    ])


def _pick(business: Business, studio: str, salt: str, options: list[str]) -> str:
    return _stable_pick(_email_seed(business, studio, salt), options)


def _short_business_info_pitch(sender: dict) -> str:
    """One clean sentence from Business Info, no URLs/emails."""
    studio = (sender.get("name") or settings.studio_name or "Our studio").strip()
    desc = _clean_sender_description(sender)
    if not desc:
        return f"{studio} helps local businesses with clear, professional websites."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", desc) if s.strip()]
    first = sentences[0]
    if not first.endswith((".", "!", "?")):
        first += "."
    if not re.match(rf"^{re.escape(studio)}\b", first, re.I):
        if re.match(r"^(helps|builds|creates|designs|offers|provides)\b", first, re.I):
            first = f"{studio} {first[0].lower()}{first[1:]}"
        else:
            first = f"{studio}: {first}"
    # Trim long service laundry-lists: keep up to first "and" clause or ~120 chars of substance
    if len(first) > 140:
        # Prefer cut before a long "with X, Y, and Z" list
        m = re.match(
            rf"^({re.escape(studio)}\s+\w+\b.+?)(?:\s+with\s+.+)$",
            first.rstrip("."),
            re.I,
        )
        if m and len(m.group(1)) >= 40:
            first = m.group(1).rstrip(" ,") + "."
        if len(first) > 140:
            cut = first[:137].rsplit(" ", 1)[0].rstrip(" ,.-")
            first = cut + ("…" if not cut.endswith((".", "!", "?")) else "")
    return first


def _we_clause_from_pitch(pitch: str, studio: str) -> str:
    """'IS Studio helps X.' -> 'we help X.' for use after studio intro."""
    text = (pitch or "").strip()
    conjugations = {
        "helps": "help",
        "builds": "build",
        "creates": "create",
        "designs": "design",
        "offers": "offer",
        "provides": "provide",
        "makes": "make",
        "crafts": "craft",
        "develops": "develop",
        "delivers": "deliver",
    }
    m = re.match(
        rf"^{re.escape(studio)}\s+({'|'.join(conjugations.keys())})\b(.*)$",
        text,
        re.I,
    )
    if m:
        verb = conjugations[m.group(1).lower()]
        rest = m.group(2).strip()
        return f"we {verb} {rest}".strip()
    if text.lower().startswith(studio.lower()):
        rest = text[len(studio) :].lstrip(" --,")
        if rest:
            return rest[0].lower() + rest[1:] if rest[0].isupper() else rest
    return text[0].lower() + text[1:] if text and text[0].isupper() else text


def _varied_intro(sender: dict, business: Business) -> str:
    studio = (sender.get("name") or settings.studio_name or "Our studio").strip()
    pitch = _short_business_info_pitch(sender)
    we = _we_clause_from_pitch(pitch, studio)
    we_cap = we[0].upper() + we[1:] if we else we

    return _pick(business, studio, "intro", [
        f"I'm {studio}. {we_cap}",
        f"Writing from {studio}: {we}",
        f"{studio} here. {we_cap}",
        f"I run {studio}. {we_cap}",
        f"Quick note from {studio}: {we}",
        f"I'm with {studio}. {we_cap}",
    ])

def _varied_opener(business: Business, city: str, studio: str) -> str:
    biz = business.name
    cat = (business.category or "").strip().lower()
    rating = business.rating
    reviews = business.review_count

    options: list[str] = []
    if rating and reviews:
        options.extend([
            f"I came across {biz} on Google Maps. {rating} stars from {reviews} reviews is impressive.",
            f"While looking at local businesses in {city}, {biz} stood out: {rating}★ across {reviews} Google reviews.",
            f"{biz} caught my eye on Google Maps. {rating} stars from {reviews} reviews is the kind of trust a strong website should match.",
            f"I found {biz} on Google Maps and noticed customers already rate you {rating}/5 ({reviews} reviews).",
        ])
    if cat:
        options.extend([
            f"I came across {biz} on Google Maps. As a {cat} in {city}, you clearly have a strong local presence.",
            f"Looking at {cat} businesses in {city}, {biz} stood out as a place people already know.",
            f"I found {biz} while researching {cat} options in {city}.",
        ])
    options.extend([
        f"I came across {biz} on Google Maps and was impressed by your presence in {city}.",
        f"I was exploring businesses in {city} and {biz} stood out.",
        f"Saw {biz} on Google Maps and wanted to reach out personally.",
    ])
    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq = []
    for o in options:
        if o not in seen:
            seen.add(o)
            uniq.append(o)
    return _pick(business, studio, "opener", uniq)


def _varied_opportunity(business: Business, city: str, studio: str) -> str:
    biz = business.name
    flags = business.website_flags or {}
    no_site = (
        flags.get("no_website")
        or (not business.has_website and not business.website_url)
        or business.no_website_status in ("verified_none", "social_only")
    )
    has_site = bool(business.has_website or business.website_url) and not no_site

    if no_site:
        return _pick(business, studio, "opp", [
            f"I noticed {biz} doesn't currently have a dedicated website. That's a real opportunity, since customers searching in {city} may land on competitors instead.",
            f"{biz} looks busy offline, but without a clear website, people searching in {city} may never find you.",
            f"One gap stood out: {biz} still doesn't have a proper website. A simple site could turn Google interest in {city} into calls and visits.",
            f"You don't seem to have a dedicated website yet. Happy to show how that could help {biz} win more customers in {city}.",
        ])

    if flags.get("outdated_website") or flags.get("not_mobile_friendly") or flags.get("slow_website"):
        return _pick(business, studio, "opp-weak", [
            f"Your online presence for {biz} could work harder. A fresher, mobile-friendly site would better match the quality of your business in {city}.",
            f"I took a quick look at {biz}'s current site. There's a clear chance to modernize it so more people in {city} book with you online.",
            f"A stronger website for {biz} could turn more local searches in {city} into customers. Happy to show what that could look like.",
        ])

    if has_site:
        return _pick(business, studio, "opp-has", [
            f"Even with an online presence, a clearer website concept for {biz} could help you convert more of the interest you're already earning in {city}.",
            f"I put together a few ideas for how {biz} could show up even stronger online in {city}.",
        ])

    return _pick(business, studio, "opp-fallback", [
        f"I think {biz} could win more customers in {city} with a clearer website presence.",
        f"A focused website for {biz} would make it easier for people in {city} to choose you.",
    ])


def _varied_highlight_heading(business: Business, studio: str) -> str:
    return _pick(business, studio, "hl-head", [
        "Here's what stood out about your business:",
        "A few things that caught my attention:",
        "Why I reached out specifically to you:",
        "What stood out about your listing:",
    ])


def _varied_highlights(business: Business, city: str, studio: str) -> list[str]:
    """Build 2-3 unique positive bullets from card details."""
    name = (business.name or "your business").strip()
    cat = (business.category or "").strip()
    pool: list[str] = []

    if business.rating and business.review_count:
        pool.extend([
            f"Customers clearly trust you: {business.rating}★ from {business.review_count} Google reviews.",
            f"{business.review_count} Google reviews at {business.rating} stars is strong social proof.",
            f"Your {business.rating}-star rating across {business.review_count} reviews shows people already recommend {name}.",
        ])
    elif business.rating:
        pool.append(f"A {business.rating}-star Google rating says a lot about the experience you deliver.")
    elif business.review_count and business.review_count > 5:
        pool.append(f"You've built real local proof with {business.review_count} Google reviews.")

    if cat:
        pool.extend([
            f"As a {cat.lower()} in {city}, you're right where neighborhood customers need you.",
            f"Being a local {cat.lower()} in {city} puts you in a great position. A website would make that easier to find.",
            f"{cat} businesses like yours in {city} often grow faster once they show up clearly online.",
        ])
    elif city and city != "your area":
        pool.append(f"You have a solid presence in {city}, the kind of spot people already know and recommend.")

    desc = (business.description or "").strip()
    if desc:
        snippet = desc[:140].rstrip()
        if len(desc) > 140:
            snippet = snippet.rsplit(" ", 1)[0] + "…"
        pool.append(snippet)

    if business.phone:
        pool.append(
            "You're easy to reach by phone. A website could make hours, services, and contact just as clear online."
        )

    products = getattr(business, "products", None) or []
    if products:
        names = [p.name for p in products[:3] if getattr(p, "name", None)]
        if names:
            pool.append("People already look for what you offer: " + ", ".join(names) + ".")

    if business.hours:
        pool.append("Your hours are listed locally. A site could keep that info (and booking) available 24/7.")

    if business.photo_count and business.photo_count >= 8:
        pool.append(
            f"Your Google profile already shows personality. {business.photo_count} photos help people get a feel for {name}."
        )

    bb = getattr(business, "brand_book", None)
    tone = getattr(bb, "tone", None) if bb else None
    if tone:
        pool.append(
            f"Your brand comes across as {tone.lower().rstrip('.')}. A website should reflect that same feel."
        )

    if not pool:
        pool = [
            f"{name} already has the fundamentals of a business people can trust.",
            f"A clear website would make it easier for new customers in {city} to find and choose you.",
        ]

    # Pick 2 unique lines, enough detail without padding the email
    chosen: list[str] = []
    used: set[str] = set()
    for i in range(min(4, len(pool))):
        rotated = pool[i:] + pool[:i]
        line = _pick(business, studio, f"hl-{i}", rotated)
        if line.lower() in used:
            continue
        used.add(line.lower())
        chosen.append(line)
        if len(chosen) >= 2:
            break
    return chosen[:2] if chosen else pool[:2]


def _varied_benefits(business: Business, studio: str) -> tuple[str, list[str]]:
    biz = business.name
    heading = _pick(business, studio, "ben-head", [
        f"A professional website would help {biz}:",
        f"With the right site, {biz} could:",
        f"Here's what a clear website can do for {biz}:",
        f"For {biz}, a strong site would make it easier to:",
    ])
    sets = [
        [
            "Show up when customers search Google",
            "Build trust before they walk through the door",
        ],
        [
            "Turn Google Maps interest into booked visits",
            "Look as professional online as you do in person",
        ],
        [
            "Win customers who currently find competitors first",
            "Make your reviews work harder for new business",
        ],
        [
            "Give locals a simple place to contact you",
            "Present your offer clearly on mobile",
        ],
    ]
    idx = int(hashlib.sha256(_email_seed(business, studio, "ben-set").encode()).hexdigest()[:8], 16) % len(sets)
    return heading, sets[idx]


def _varied_prototype(business: Business, city: str, studio: str, prototype_link: str) -> str:
    biz = business.name
    if prototype_link:
        lead = _pick(business, studio, "proto-lead", [
            f"I put together a website concept specifically for {biz}:",
            f"Here's a working prototype tailored to {biz}:",
            f"I drafted a quick site concept for {biz}. Take a look:",
            f"Built a short prototype so you can see {biz} online:",
        ])
        follow = _pick(business, studio, "proto-follow", [
            f"This is a working prototype. Click through it to see how {biz} could look online with a modern site tailored to your brand.",
            f"Click through when you have a minute. It's meant to feel like {biz}, not a generic template.",
            f"It's interactive, so you can click around and picture how customers in {city} would experience {biz} online.",
            f"No commitment: just a concrete look at what {biz} could present online.",
        ])
        return f"{lead}\n{prototype_link}\n\n{follow}"

    return _pick(business, studio, "proto-none", [
        f"I'd love to sketch a custom website concept for {biz} that matches your quality and helps you win more customers in {city}.",
        f"Happy to put together a short visual concept for {biz}: something clear, modern, and built around your services.",
        f"If useful, I can prepare a simple site mockup for {biz} so you can see the direction before any commitment.",
    ])


def _varied_cta(business: Business, studio: str) -> str:
    biz = business.name
    return _pick(business, studio, "cta", [
        f"I'd love to chat about bringing this to life for {biz}. Would you be open to a quick 15-minute call this week?",
        f"Would you be open to a short 15-minute call to see if this is useful for {biz}?",
        f"If this looks relevant, happy to hop on a quick call and walk through the idea for {biz}.",
        f"Are you free for a brief call this week? I can show how this could work for {biz} in about 15 minutes.",
        f"Curious if a quick call makes sense. 15 minutes to see whether this helps {biz}.",
    ])


def _varied_subject(business: Business, studio: str) -> str:
    biz = business.name
    city = _city_from_address(business.address)
    return _pick(business, studio, "subject", [
        f"Website concept for {biz}: take a look",
        f"Quick idea for {biz}'s online presence",
        f"{biz}: a short website concept from {studio}",
        f"Saw {biz} in {city}: website idea inside",
        f"A site concept tailored to {biz}",
        f"{studio} x {biz}: quick website mockup",
        f"Something for {biz} to review (2 min)",
    ])


def generate_send_email(
    business: Business,
    sender_info: str = "",
    figma_prototype_link: str = "",
    *,
    sender_business_name: str = "",
    sender_business_website: str = "",
    require_sender_info: bool = True,
    template_id: str = "",
    logo_url: str = "",
) -> tuple[str, str, str]:
    """Write a short, unique outreach email. Returns (subject, plain_body, html_body)."""
    if require_sender_info and not sender_info.strip():
        raise ValueError(
            "Add Your Business Info in the sidebar before sending, "
            "it is used to personalize the pitch."
        )
    if require_sender_info and not (sender_business_name or "").strip():
        raise ValueError(
            "Add your business name in the sidebar before sending."
        )

    contact = _first_name_from_business(business.name)
    city = _city_from_address(business.address)
    website = normalize_business_website(sender_business_website)
    sender = parse_sender_info(
        sender_info,
        sender_business_name,
        website,
    ) if sender_info.strip() else {
        "name": (sender_business_name or settings.studio_name or "Our studio").strip(),
        "body": "",
        "url": website or (settings.studio_url or ""),
        "email": settings.studio_email or "",
        "raw": "",
    }
    if (sender_business_name or "").strip():
        sender["name"] = sender_business_name.strip()[:80]
    if website:
        sender["url"] = website

    studio_name = sender["name"]
    studio_url = sender.get("url") or ""
    studio_email = sender.get("email") or ""
    biz = business.name
    theme = get_email_template(template_id)

    intro = _varied_intro(sender, business)
    opener = _varied_opener(business, city, studio_name)
    opportunity = _varied_opportunity(business, city, studio_name)
    hl_heading = _varied_highlight_heading(business, studio_name)
    highlights = _varied_highlights(business, city, studio_name)
    highlight_block = f"{hl_heading}\n" + "\n".join(f"  • {h}" for h in highlights)
    ben_heading, benefits = _varied_benefits(business, studio_name)
    benefits_block = f"{ben_heading}\n\n" + "\n".join(f"  • {b}" for b in benefits)

    prototype_link = figma_prototype_link.strip()
    prototype_plain = _varied_prototype(business, city, studio_name, prototype_link)
    cta = _varied_cta(business, studio_name)
    subject = _varied_subject(business, studio_name)

    sig_lines = ["Best regards,"]
    if studio_email:
        sig_lines.append(studio_email)
    if studio_url:
        sig_lines.append(studio_url)
    signature = "\n".join(sig_lines)

    unsub = (
        'If this email isn\'t relevant, reply with "unsubscribe" '
        "and we won't contact you again."
    )

    plain = f"""Hi {contact},

{intro}

{opener}

{opportunity}

{highlight_block}

{benefits_block}

{prototype_plain}

{cta}

{signature}

{unsub}"""

    # Before prototype button: opener → opportunity → highlights → benefits
    before_cta = [
        _html_p(opener, theme),
        _html_p(opportunity, theme),
        f'<p style="margin:0 0 8px;color:{theme["heading"]};line-height:1.55;font-size:15px;font-weight:600;">{html.escape(hl_heading)}</p>',
        _html_bullets(highlights, theme),
        f'<p style="margin:16px 0 8px;color:{theme["heading"]};line-height:1.55;font-size:15px;font-weight:600;">{html.escape(ben_heading)}</p>',
        _html_bullets(benefits, theme),
    ]
    if not prototype_link:
        before_cta.append(_html_p(prototype_plain, theme))

    # CTA right after the prototype button, goal is a reply / call
    after_cta = [_html_p(cta, theme)]

    proto_lines = [ln.strip() for ln in prototype_plain.split("\n") if ln.strip()]
    proto_lead = proto_lines[0] if proto_lines else ""
    proto_follow = " ".join(
        ln for ln in proto_lines[1:]
        if not ln.startswith("http")
    ).strip()

    html_body = build_outreach_email_html(
        studio_name=studio_name,
        intro=f"Hi {contact},\n\n{intro}",
        body_sections_html="\n".join(before_cta),
        prototype_url=prototype_link,
        studio_email=studio_email,
        studio_url=studio_url,
        business_name=biz,
        prototype_lead=proto_lead if prototype_link else "",
        prototype_follow=proto_follow if prototype_link else "",
        after_cta_html="\n".join(after_cta),
        template_id=template_id,
        logo_url=logo_url,
    )

    return _ban_fancy_dashes(subject), _ban_fancy_dashes(plain.strip()), _ban_fancy_dashes(html_body)


def apply_outreach_to_businesses(
    businesses: list[Business],
    sender_info: str = "",
) -> list[Business]:
    """Deprecated for search, kept for export/compat. Prefer generate_send_email at send time."""
    updated = []
    for biz in businesses:
        updated.append(biz.model_copy(update={
            "outreach_subject": None,
            "outreach_email": None,
        }))
    return updated
