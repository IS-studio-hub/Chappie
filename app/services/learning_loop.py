"""Learning loop: which categories/cities convert → boost those in search.

Aggregates each user's outreach pipeline (sent → opened → replied → booked → closed)
into conversion strengths by category and city, then boosts lead quality ranking.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.services.db import get_db

# Pipeline outcome weights (closed wins hardest)
STATUS_WEIGHTS = {
    "sent": 0.4,
    "opened": 1.5,
    "replied": 4.0,
    "booked": 8.0,
    "closed": 10.0,
}

MIN_SAMPLES = 2
MAX_CATEGORY_BOOST = 7.0
MAX_CITY_BOOST = 5.0
MAX_COMBO_BOOST = 12.0
MAX_TOTAL_BOOST = 12


def _norm_key(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_city(address: str | None) -> str:
    """Best-effort city from a Maps-style address."""
    if not address or not str(address).strip():
        return ""
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) >= 3:
        # street, city, province/postal, [country]
        city = parts[1]
        # Skip if this looks like a province+postal code
        if re.match(r"^[A-Za-z]{2}\s+\w", city) and len(city) < 12:
            city = parts[0]
        return city
    if len(parts) == 2:
        # "Mississauga, ON" or "Street, City"
        right = parts[1]
        if re.match(r"^[A-Za-z]{2}\b", right) and len(right) <= 15:
            return parts[0]
        return parts[0]
    return parts[0]


def extract_province(address: str | None, province_field: str | None = None) -> str:
    if province_field and str(province_field).strip():
        return str(province_field).strip()
    if not address:
        return ""
    parts = [p.strip() for p in str(address).split(",") if p.strip()]
    for part in parts:
        m = re.match(r"^([A-Za-z]{2})\b", part)
        if m and len(part) <= 20:
            return m.group(1).upper()
    return ""


def deal_dimensions_from_business(business: dict[str, Any]) -> dict[str, str]:
    category = (business.get("category") or "").strip()
    address = business.get("address")
    city = extract_city(address)
    province = extract_province(address, business.get("province"))
    return {
        "category": category,
        "city": city,
        "province": province,
        "category_key": _norm_key(category),
        "city_key": _norm_key(city),
    }


@dataclass
class BucketStats:
    key: str
    label: str
    deals: int = 0
    weighted: float = 0.0
    by_status: dict[str, int] = field(default_factory=dict)

    @property
    def avg_weight(self) -> float:
        return self.weighted / self.deals if self.deals else 0.0

    @property
    def strength(self) -> float:
        """0–1 conversion strength (10 = all closed)."""
        return min(1.0, self.avg_weight / 10.0)

    def boost(self, max_boost: float) -> float:
        if self.deals < MIN_SAMPLES:
            return 0.0
        # Require some signal beyond cold sends
        if self.avg_weight < 1.0:
            return 0.0
        return round(self.strength * max_boost, 2)


@dataclass
class ConversionInsights:
    categories: dict[str, BucketStats] = field(default_factory=dict)
    cities: dict[str, BucketStats] = field(default_factory=dict)
    combos: dict[str, BucketStats] = field(default_factory=dict)
    deal_count: int = 0

    def empty(self) -> bool:
        return self.deal_count < MIN_SAMPLES

    def boost_for(self, category: str | None, city: str | None) -> tuple[float, list[str]]:
        """Return (boost points 0–12, signals)."""
        if self.empty():
            return 0.0, []

        cat_key = _norm_key(category)
        city_key = _norm_key(city)
        signals: list[str] = []
        boost = 0.0

        combo_key = f"{cat_key}|{city_key}" if cat_key and city_key else ""
        if combo_key and combo_key in self.combos:
            combo = self.combos[combo_key]
            cboost = combo.boost(MAX_COMBO_BOOST)
            if cboost > 0:
                boost = max(boost, cboost)
                signals.append(
                    f"learning_win:{combo.label} ({combo.deals} deals)"
                )

        if cat_key and cat_key in self.categories:
            cat = self.categories[cat_key]
            cboost = cat.boost(MAX_CATEGORY_BOOST)
            if cboost > 0:
                boost = max(boost, cboost) if not signals else boost + min(cboost, 4.0)
                if not any(s.startswith("learning_win:") for s in signals):
                    signals.append(f"learning_category:{cat.label}")

        if city_key and city_key in self.cities:
            city_s = self.cities[city_key]
            cboost = city_s.boost(MAX_CITY_BOOST)
            if cboost > 0:
                boost = min(MAX_TOTAL_BOOST, boost + cboost)
                if not any("learning_city:" in s or "learning_win:" in s for s in signals):
                    signals.append(f"learning_city:{city_s.label}")
                elif cboost >= 2 and not any(s.startswith("learning_city:") for s in signals):
                    signals.append(f"learning_city:{city_s.label}")

        boost = min(float(MAX_TOTAL_BOOST), max(0.0, boost))
        if boost >= 1:
            signals.append(f"learning_boost:+{int(round(boost))}")
        return boost, signals[:4]

    def top_insights(self, limit: int = 5) -> list[dict[str, Any]]:
        """Human-readable winners for UI."""
        rows: list[tuple[float, dict]] = []

        for b in self.combos.values():
            if b.deals >= MIN_SAMPLES and b.avg_weight >= 1.0:
                rows.append((b.avg_weight * 1.2 + b.deals * 0.05, {
                    "type": "category_city",
                    "label": b.label,
                    "deals": b.deals,
                    "strength": round(b.strength * 100),
                    "by_status": dict(b.by_status),
                }))
        for b in self.categories.values():
            if b.deals >= MIN_SAMPLES and b.avg_weight >= 1.0:
                rows.append((b.avg_weight + b.deals * 0.05, {
                    "type": "category",
                    "label": b.label,
                    "deals": b.deals,
                    "strength": round(b.strength * 100),
                    "by_status": dict(b.by_status),
                }))
        for b in self.cities.values():
            if b.deals >= MIN_SAMPLES and b.avg_weight >= 1.0:
                rows.append((b.avg_weight * 0.9 + b.deals * 0.05, {
                    "type": "city",
                    "label": b.label,
                    "deals": b.deals,
                    "strength": round(b.strength * 100),
                    "by_status": dict(b.by_status),
                }))

        rows.sort(key=lambda x: x[0], reverse=True)
        # Dedupe similar labels preferring higher score
        seen: set[str] = set()
        out: list[dict] = []
        for _, row in rows:
            key = f"{row['type']}:{_norm_key(row['label'])}"
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
            if len(out) >= limit:
                break
        return out


def _bump(bucket: BucketStats, status: str, open_count: int = 0) -> None:
    weight = STATUS_WEIGHTS.get(status, 0.4)
    # Extra open signal without double-counting opened status
    if status == "sent" and open_count > 0:
        weight = max(weight, STATUS_WEIGHTS["opened"])
    elif open_count > 1 and status in ("opened", "sent"):
        weight += min(1.0, 0.2 * (open_count - 1))
    bucket.deals += 1
    bucket.weighted += weight
    bucket.by_status[status] = bucket.by_status.get(status, 0) + 1


async def get_conversion_insights(user_id: str) -> ConversionInsights:
    """Build per-user conversion strengths from outreach deals."""
    insights = ConversionInsights()
    if not user_id:
        return insights

    cursor = get_db().outreach_deals.find({"user_id": user_id}).limit(500)
    async for doc in cursor:
        insights.deal_count += 1
        status = doc.get("status") or "sent"
        open_count = int(doc.get("open_count") or 0)

        category = (doc.get("category") or "").strip()
        city = (doc.get("city") or "").strip()
        if not city:
            city = extract_city(doc.get("business_address"))

        cat_key = doc.get("category_key") or _norm_key(category)
        city_key = doc.get("city_key") or _norm_key(city)

        if cat_key:
            if cat_key not in insights.categories:
                insights.categories[cat_key] = BucketStats(
                    key=cat_key, label=category or cat_key.title()
                )
            _bump(insights.categories[cat_key], status, open_count)

        if city_key:
            if city_key not in insights.cities:
                insights.cities[city_key] = BucketStats(
                    key=city_key, label=city or city_key.title()
                )
            _bump(insights.cities[city_key], status, open_count)

        if cat_key and city_key:
            combo_key = f"{cat_key}|{city_key}"
            label = f"{category or cat_key.title()} in {city or city_key.title()}"
            if combo_key not in insights.combos:
                insights.combos[combo_key] = BucketStats(key=combo_key, label=label)
            _bump(insights.combos[combo_key], status, open_count)

    return insights


async def get_learning_summary(user_id: str) -> dict[str, Any]:
    insights = await get_conversion_insights(user_id)
    tops = insights.top_insights(6)
    return {
        "deal_count": insights.deal_count,
        "ready": not insights.empty() and len(tops) > 0,
        "message": (
            "CH4PP!3 is boosting categories and cities that convert for you."
            if tops
            else "Send outreach and mark replies/booked/closed — CH4PP!3 will learn what converts."
        ),
        "winners": tops,
    }
