import hashlib
import html
import re

from app.config import settings
from app.models import Business


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


def parse_sender_info(sender_info: str, business_name: str = "") -> dict:
    """Public wrapper for sender Business Info parsing."""
    parsed = _parse_sender_info(sender_info)
    name = (business_name or "").strip()
    if name:
        parsed = {**parsed, "name": name[:80]}
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
    working = re.sub(r"\s+", " ", working).strip(" ,.-–")

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Prefer a short first line as the studio name
    name = ""
    body = working
    if lines:
        first = lines[0].strip()
        first_clean = re.sub(r"https?://\S+", "", first)
        first_clean = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", "", first_clean).strip(" ,.-–")
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
                ln2 = re.sub(r"\s+", " ", ln2).strip(" ,.-–")
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
        # Fallback: first 1–3 capitalized tokens
        tokens = re.findall(r"[A-Za-z][\w&.\'’-]*", working)
        name = " ".join(tokens[:2]) if tokens else (settings.studio_name or "Our studio")
        if len(name) > 40:
            name = (settings.studio_name or "Our studio").strip()

    if name and body.lower().startswith(name.lower()):
        body = body[len(name) :].lstrip(" -–,|:;")

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
        text = text[len(name) :].lstrip(" -–,|:;")
    text = re.sub(r"\s+", " ", text).strip(" ,.-–")
    return text


def _intro_from_business_info(sender: dict) -> str:
    """Short explanation of the sender's business — no links."""
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
            intro = f"{studio} — {intro}"
    # Prefer a clean sentence cut over mid-word ellipsis
    if len(intro) > 320 and len(sentences) >= 2:
        first = sentences[0].strip()
        if not first.endswith((".", "!", "?")):
            first += "."
        if not re.match(rf"^{re.escape(studio)}\b", first, re.I):
            if re.match(r"^(helps|builds|creates|designs|offers|provides)\b", first, re.I):
                first = f"{studio} {first[0].lower()}{first[1:]}"
            else:
                first = f"{studio} — {first}"
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
    text = re.sub(r"\s+", " ", text).strip(" ,.-–")
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
        f"{pitch} — idea for {biz}.",
        f"Quick note for {biz} from {studio}.",
        f"{studio}: a short site idea for {biz}.",
        f"Saw {biz} in {city} — {pitch}",
        f"For {biz}: {pitch}",
        f"{pitch} Worth a look for {biz}.",
        f"{studio} × {biz} — {pitch}",
        f"One idea for {biz} ({cat}) from {studio}.",
        f"{biz}: {pitch}",
        f"About {biz}'s site — {pitch}",
    ]
    line = _stable_pick(seed, options)
    if len(line) > 78:
        line = line[:75].rsplit(" ", 1)[0].rstrip(" ,.-") + "…"
    return line


def _positive_highlights(business: Business, city: str) -> list[str]:
    """Warm, specific standouts — not dry Category/Location bullets."""
    name = (business.name or "your business").strip()
    cat = (business.category or "").strip()
    highlights: list[str] = []

    if business.rating and business.review_count:
        highlights.append(
            f"Customers clearly trust you — {business.rating}★ from "
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
            f"You have a solid presence in {city} — the kind of spot people already know and recommend."
        )

    desc = (business.description or "").strip()
    if desc:
        snippet = desc[:150].rstrip()
        if len(desc) > 150:
            snippet = snippet.rsplit(" ", 1)[0] + "…"
        highlights.append(snippet)

    if business.photo_count and business.photo_count >= 8:
        highlights.append(
            f"Your Google profile already shows personality — {business.photo_count} photos help people get a feel for {name} before they visit."
        )

    products = getattr(business, "products", None) or []
    if products:
        names = [p.name for p in products[:3] if getattr(p, "name", None)]
        if names:
            highlights.append(
                "You're already offering things people look for — "
                + ", ".join(names)
                + ("…" if len(products) > 3 else "")
                + "."
            )

    bb = getattr(business, "brand_book", None)
    tone = getattr(bb, "tone", None) if bb else None
    if tone:
        highlights.append(f"Your brand comes across as {tone.lower().rstrip('.')} — a website should reflect that same feel.")

    # Deduplicate and keep 2–4
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
    return (settings.studio_name or "Chappie").strip()[:80]


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


def build_outreach_email_html(
    *,
    studio_name: str,
    intro: str,
    body_sections_html: str,
    prototype_url: str = "",
    studio_email: str = "",
    studio_url: str = "",
    business_name: str = "",
) -> str:
    """Dark card HTML matching the verification email look, with CTA button."""
    studio = html.escape(studio_name or "Our studio")
    eyebrow = studio
    title = html.escape(
        f"Website concept for {business_name}" if business_name else "Website concept"
    )
    intro_html = html.escape(intro).replace("\n", "<br>\n")

    cta = ""
    if prototype_url.strip():
        href = html.escape(prototype_url.strip(), quote=True)
        cta = f"""
              <p style="margin:0 0 8px;color:#eef3f8;line-height:1.55;font-size:15px;">
                I put together a website concept specifically for {html.escape(business_name or "your business")}:
              </p>
              <p style="margin:0 0 16px;text-align:center;">
                <a href="{href}"
                   style="display:inline-block;background:#3d9a7a;color:#04110c;text-decoration:none;font-weight:700;padding:14px 28px;border-radius:999px;font-size:15px;">
                  Your New Website
                </a>
              </p>
              <p style="margin:0 0 20px;color:#8fa0b5;line-height:1.55;font-size:14px;">
                This is a working prototype — click through it to see how {html.escape(business_name or "your business")} could look online with a modern, professional site tailored to your brand.
              </p>"""

    sig_bits = []
    if studio_email:
        sig_bits.append(
            f'<a href="mailto:{html.escape(studio_email, quote=True)}" style="color:#c4a35a;text-decoration:none;">{html.escape(studio_email)}</a>'
        )
    if studio_url:
        sig_bits.append(
            f'<a href="{html.escape(studio_url, quote=True)}" style="color:#c4a35a;word-break:break-all;">{html.escape(studio_url)}</a>'
        )
    sig_html = "<br>\n".join(sig_bits)

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#0c1118;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0c1118;padding:40px 16px;">
    <tr>
      <td align="center">
        <table width="100%" style="max-width:520px;background:#172231;border:1px solid #2a3a4f;border-radius:14px;padding:32px;">
          <tr>
            <td style="color:#eef3f8;">
              <p style="margin:0 0 8px;color:#3d9a7a;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;font-weight:600;">{eyebrow}</p>
              <h1 style="margin:0 0 16px;font-size:24px;font-weight:600;line-height:1.25;">{title}</h1>
              <p style="margin:0 0 18px;color:#c5d0dc;line-height:1.55;font-size:15px;">{intro_html}</p>
              {body_sections_html}
              {cta}
              <p style="margin:24px 0 0;color:#8fa0b5;font-size:13px;line-height:1.5;">Best regards,</p>
              <p style="margin:6px 0 0;color:#8fa0b5;font-size:13px;line-height:1.6;">{sig_html}</p>
              <p style="margin:24px 0 0;color:#6b7c90;font-size:12px;line-height:1.5;">
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
) -> str:
    """Wrap an edited plain-text body in the verification-style card; turn Figma links into a CTA."""
    text = (plain_body or "").strip()
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
        r"Best regards,.*$",
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
                '<ul style="margin:0 0 18px;padding-left:18px;color:#c5d0dc;line-height:1.5;font-size:15px;">'
                + "".join(items)
                + "</ul>"
            )
        else:
            sections.append(
                '<p style="margin:0 0 16px;color:#c5d0dc;line-height:1.55;font-size:15px;">'
                + html.escape(block).replace("\n", "<br>\n")
                + "</p>"
            )

    return build_outreach_email_html(
        studio_name=studio_name,
        intro=intro_clean or intro,
        body_sections_html="\n".join(sections),
        prototype_url=proto,
        studio_email=studio_email,
        studio_url=studio_url,
        business_name=business_name,
    )


def _html_p(text: str) -> str:
    return (
        '<p style="margin:0 0 16px;color:#c5d0dc;line-height:1.55;font-size:15px;">'
        + html.escape(text)
        + "</p>"
    )


def _html_bullets(items: list[str]) -> str:
    lis = "".join(
        f'<li style="margin:0 0 6px;">{html.escape(item)}</li>' for item in items
    )
    return (
        '<ul style="margin:0 0 18px;padding-left:18px;color:#c5d0dc;line-height:1.5;font-size:15px;">'
        f"{lis}</ul>"
    )


def generate_outreach(business: Business, sender_info: str = "") -> tuple[str, str]:
    """Return (subject, body) for a personalized outreach email."""
    subject, body, _html = generate_send_email(
        business,
        sender_info=sender_info or (settings.studio_name or "Our studio"),
        figma_prototype_link="",
        require_sender_info=False,
    )
    return subject, body


def generate_send_email(
    business: Business,
    sender_info: str = "",
    figma_prototype_link: str = "",
    *,
    sender_business_name: str = "",
    require_sender_info: bool = True,
) -> tuple[str, str, str]:
    """Write a shorter outreach email. Returns (subject, plain_body, html_body)."""
    if require_sender_info and not sender_info.strip():
        raise ValueError(
            "Add Your Business Info in the sidebar before sending — "
            "it is used to personalize the pitch."
        )
    if require_sender_info and not (sender_business_name or "").strip():
        raise ValueError(
            "Add your business name in the sidebar before sending."
        )

    contact = _first_name_from_business(business.name)
    city = _city_from_address(business.address)
    sender = parse_sender_info(sender_info, sender_business_name) if sender_info.strip() else {
        "name": (sender_business_name or settings.studio_name or "Our studio").strip(),
        "body": "",
        "url": settings.studio_url or "",
        "email": settings.studio_email or "",
        "raw": "",
    }
    if (sender_business_name or "").strip():
        sender["name"] = sender_business_name.strip()[:80]

    studio_name = sender["name"]
    studio_url = sender.get("url") or ""
    studio_email = sender.get("email") or ""
    intro = _intro_from_business_info(sender)
    biz = business.name

    if business.rating and business.review_count:
        opener = (
            f"I came across {biz} on Google Maps — "
            f"{business.rating} stars from {business.review_count} reviews is impressive."
        )
    elif business.category:
        opener = (
            f"I came across {biz} on Google Maps — "
            f"as a {business.category.lower()} in {city}, you clearly have a strong local presence."
        )
    else:
        opener = (
            f"I came across {biz} on Google Maps and was impressed "
            f"by your local presence in {city}."
        )

    opportunity = (
        f"I noticed {biz} doesn't currently have a dedicated website. "
        f"That's a real opportunity — customers searching online in {city} "
        f"may be finding your competitors instead."
    )

    highlights = _positive_highlights(business, city)
    highlight_block = "Here's what stood out about your business:\n" + "\n".join(
        f"  • {h}" for h in highlights
    )

    benefits = [
        "Show up when customers search Google",
        "Build trust before they walk through the door",
        "Showcase your services, hours, and contact info 24/7",
        "Turn your great Google reviews into new customers",
    ]
    benefits_block = (
        f"A professional website would help {biz}:\n\n"
        + "\n".join(f"  • {b}" for b in benefits)
    )

    prototype_link = figma_prototype_link.strip()
    if prototype_link:
        prototype_plain = (
            f"I put together a website concept specifically for {biz}:\n"
            f"{prototype_link}\n\n"
            f"This is a working prototype — click through it to see how {biz} "
            f"could look online with a modern, professional site tailored to your brand."
        )
    else:
        prototype_plain = (
            f"I'd love to design a custom website concept for {biz} "
            f"that reflects the quality of your business and helps you win more customers in {city}."
        )

    subject = f"Website concept for {biz} — take a look"

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

{prototype_plain}

{benefits_block}

I'd love to chat about bringing this to life for {biz}. Would you be open to a quick 15-minute call this week?

{signature}

{unsub}"""

    # HTML sections (CTA handled separately in build_outreach_email_html)
    html_sections = [
        _html_p(opener),
        _html_p(opportunity),
        '<p style="margin:0 0 8px;color:#eef3f8;line-height:1.55;font-size:15px;font-weight:600;">'
        "Here's what stood out about your business:</p>",
        _html_bullets(highlights),
    ]
    if not prototype_link:
        html_sections.append(_html_p(prototype_plain))
    html_sections.append(
        '<p style="margin:0 0 8px;color:#eef3f8;line-height:1.55;font-size:15px;font-weight:600;">'
        f"A professional website would help {html.escape(biz)}:</p>"
    )
    html_sections.append(_html_bullets(benefits))
    html_sections.append(
        _html_p(
            f"I'd love to chat about bringing this to life for {biz}. "
            "Would you be open to a quick 15-minute call this week?"
        )
    )

    html_body = build_outreach_email_html(
        studio_name=studio_name,
        intro=f"Hi {contact},\n\n{intro}",
        body_sections_html="\n".join(html_sections),
        prototype_url=prototype_link,
        studio_email=studio_email,
        studio_url=studio_url,
        business_name=biz,
    )

    return subject, plain.strip(), html_body


def apply_outreach_to_businesses(
    businesses: list[Business],
    sender_info: str = "",
) -> list[Business]:
    """Deprecated for search — kept for export/compat. Prefer generate_send_email at send time."""
    updated = []
    for biz in businesses:
        updated.append(biz.model_copy(update={
            "outreach_subject": None,
            "outreach_email": None,
        }))
    return updated
