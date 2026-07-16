"""Lead quality ranking: quality over quantity.

Weights (0–100):
  no site confirmed  35
  phone + email      30
  category fit       20
  reviews            10
  freshness           5
Plus learning-loop boost (up to +12) from categories/cities that convert.
"""

from __future__ import annotations

import re
from typing import Any

from app.models import Business
from app.services.learning_loop import ConversionInsights, extract_city

# SMB types that typically buy websites (when no user targets set)
HIGH_INTENT_CATEGORIES = {
    "restaurant", "cafe", "bar", "bakery", "dentist", "doctor", "physiotherapist",
    "veterinary care", "beauty salon", "hair care", "spa", "gym", "lawyer",
    "accounting", "insurance agency", "real estate agency", "car repair",
    "electrician", "plumber", "roofing contractor", "painter", "locksmith",
    "moving company", "florist", "clothing store", "furniture store",
    "pet store", "lodging", "pharmacy", "laundry",
}


def parse_target_categories(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    parts = re.split(r"[,;\n|/]+", str(raw))
    return [p.strip().lower() for p in parts if p.strip()]


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower()).strip()


def _business_category_blob(business: Business) -> str:
    bits = [business.category or ""]
    bits.extend(business.categories or [])
    bits.append(business.name or "")
    return _norm(" ".join(bits))


def _score_no_site(business: Business) -> tuple[float, list[str]]:
    """Max 35 — confirmed no website is the core product claim."""
    signals: list[str] = []
    raw = business.no_website_score
    status = business.no_website_status or ""

    if status == "suspected_site":
        signals.append("suspected_live_website")
        return 3.0, signals

    if raw is None:
        signals.append("google_no_site_unverified")
        return 18.0, signals

    pts = (max(0, min(100, int(raw))) / 100.0) * 35.0
    if raw >= 80:
        signals.append("no_site_verified")
    elif raw >= 55:
        signals.append("no_site_likely")
    else:
        signals.append("no_site_uncertain")
    return pts, signals


def _score_contact(business: Business) -> tuple[float, list[str]]:
    """Max 30 — email 20, phone 10."""
    signals: list[str] = []
    pts = 0.0
    emails = list(business.contact_emails or [])
    if business.contact_email and business.contact_email not in emails:
        emails.insert(0, business.contact_email)
    emails = [e for e in emails if e]

    if emails:
        pts += 20.0
        signals.append(f"email_found:{len(emails)}")
    if business.phone:
        pts += 10.0
        signals.append("phone_found")
    if not emails and not business.phone:
        signals.append("no_contact")
    return pts, signals


def _score_category_fit(
    business: Business,
    targets: list[str],
) -> tuple[float, list[str]]:
    """Max 20 — match user niches when set; else high-intent SMB types."""
    signals: list[str] = []
    blob = _business_category_blob(business)
    primary = _norm(business.category)

    if not blob.strip() and not primary:
        signals.append("no_category")
        return 4.0, signals

    if targets:
        best = 0.0
        matched = None
        for t in targets:
            t_norm = _norm(t)
            if not t_norm:
                continue
            if t_norm == primary or t_norm in primary.split():
                best = 20.0
                matched = t
                break
            if t_norm in blob:
                best = max(best, 16.0)
                matched = t
            elif any(tok and tok in blob for tok in t_norm.split() if len(tok) > 2):
                best = max(best, 12.0)
                matched = matched or t
        if matched:
            signals.append(f"category_fit:{matched}")
            return best, signals
        signals.append("category_mismatch")
        return 4.0, signals

    for hint in HIGH_INTENT_CATEGORIES:
        if hint in blob or hint == primary:
            signals.append(f"high_intent_category:{hint}")
            return 16.0, signals

    if primary or business.categories:
        signals.append("generic_category")
        return 10.0, signals

    return 4.0, signals


def _score_reviews(business: Business) -> tuple[float, list[str]]:
    """Max 10 — volume first, rating as soft boost."""
    signals: list[str] = []
    count = int(business.review_count or 0)
    rating = float(business.rating or 0)

    if count <= 0:
        signals.append("no_reviews")
        return 1.0, signals

    if count >= 80:
        volume = 7.0
    elif count >= 25:
        volume = 8.5
    elif count >= 8:
        volume = 7.5
    elif count >= 3:
        volume = 5.0
    else:
        volume = 3.0

    rating_boost = 0.0
    if rating >= 4.5:
        rating_boost = 1.5
    elif rating >= 4.0:
        rating_boost = 1.0
    elif rating >= 3.5:
        rating_boost = 0.5

    pts = min(10.0, volume + rating_boost)
    signals.append(f"reviews:{count}")
    if rating:
        signals.append(f"rating:{rating}")
    return pts, signals


def _score_freshness(business: Business) -> tuple[float, list[str]]:
    """Max 5 — proxy for active listing."""
    signals: list[str] = []
    pts = 0.0

    if business.hours:
        pts += 1.5
        signals.append("has_hours")
    if business.is_open is True:
        pts += 1.0
        signals.append("open_now")
    if business.social_profiles:
        pts += 1.0
        signals.append("social_active")
    if (business.review_count or 0) >= 5:
        pts += 1.0
        signals.append("review_activity")
    if business.photo_count and business.photo_count >= 3:
        pts += 0.5
        signals.append("has_photos")
    if business.reviews:
        pts += 0.5
        signals.append("review_text")

    pts = min(5.0, pts)
    if pts < 1:
        signals.append("stale_or_thin_listing")
    return pts, signals


def _tier_from_score(score: int) -> tuple[str, str]:
    if score >= 75:
        return "Great", "great"
    if score >= 55:
        return "Good", "good"
    if score >= 35:
        return "Fair", "fair"
    return "Weak", "weak"


def score_business(
    business: Business,
    target_categories: list[str] | None = None,
    insights: ConversionInsights | None = None,
) -> dict[str, Any]:
    targets = target_categories or []
    parts: list[tuple[float, list[str]]] = [
        _score_no_site(business),
        _score_contact(business),
        _score_category_fit(business, targets),
        _score_reviews(business),
        _score_freshness(business),
    ]

    total = sum(p[0] for p in parts)
    signals: list[str] = []
    for _, sigs in parts:
        signals.extend(sigs)

    learning_boost = 0.0
    if insights and not insights.empty():
        city = extract_city(business.address)
        boost, learn_signals = insights.boost_for(business.category, city)
        learning_boost = boost
        signals.extend(learn_signals)

    score = max(0, min(100, int(round(total + learning_boost))))
    label, tier = _tier_from_score(score)

    return {
        "lead_quality_score": score,
        "lead_quality_label": label,
        "lead_quality_tier": tier,
        "lead_quality_signals": signals[:12],
        "learning_boost": int(round(learning_boost)) if learning_boost else 0,
    }


def rank_businesses(
    businesses: list[Business],
    target_categories: str | list[str] | None = None,
    insights: ConversionInsights | None = None,
) -> list[Business]:
    if isinstance(target_categories, str):
        targets = parse_target_categories(target_categories)
    else:
        targets = list(target_categories or [])

    ranked: list[Business] = []
    for biz in businesses:
        data = biz.model_dump()
        data.update(score_business(biz, targets, insights=insights))
        ranked.append(Business(**data))

    ranked.sort(
        key=lambda b: (
            b.lead_quality_score or 0,
            b.learning_boost or 0,
            b.no_website_score or 0,
            b.review_count or 0,
        ),
        reverse=True,
    )
    return ranked
