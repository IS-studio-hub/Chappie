import csv
import io
import json
from datetime import datetime

from app.models import Business, SearchResponse


def businesses_to_json(response: SearchResponse) -> str:
    return json.dumps(response.model_dump(), indent=2, ensure_ascii=False)


def businesses_to_csv(businesses: list[Business]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "name", "contact_email", "contact_emails", "email_source", "outreach_subject", "outreach_email",
        "website_opportunity_score", "website_opportunity_label", "website_opportunity_breakdown",
        "flag_no_website", "flag_outdated_website", "flag_not_mobile_friendly", "flag_slow_website",
        "flag_missing_https", "flag_missing_booking", "flag_missing_online_store",
        "flag_has_email", "flag_has_decision_maker",
        "lead_quality_score", "lead_quality_label", "lead_quality_signals", "learning_boost",
        "brand_colors", "brand_tone", "brand_pricing", "brand_imagery", "brand_source",
        "no_website_score", "no_website_label", "no_website_status", "suspected_website", "no_website_signals",
        "has_website", "website_url",
        "rating", "review_count", "category", "address", "phone",
        "hours", "is_open", "areas_served", "province", "located_in",
        "description", "appointment_url", "photo_count", "google_maps_url",
        "latitude", "longitude", "place_id", "categories", "products",
        "reviews", "social_profiles",
    ]
    writer.writerow(headers)

    for b in businesses:
        writer.writerow([
            b.name,
            b.contact_email or "",
            "; ".join(b.contact_emails) if b.contact_emails else "",
            b.email_source or "",
            b.outreach_subject or "",
            b.outreach_email or "",
            b.website_opportunity_score if b.website_opportunity_score is not None else "",
            b.website_opportunity_label or "",
            "; ".join(b.website_opportunity_breakdown or b.website_opportunity_signals or []),
            "yes" if (b.website_flags or {}).get("no_website") else "no",
            "yes" if (b.website_flags or {}).get("outdated_website") else "no",
            "yes" if (b.website_flags or {}).get("not_mobile_friendly") else "no",
            "yes" if (b.website_flags or {}).get("slow_website") else "no",
            "yes" if (b.website_flags or {}).get("missing_https") else "no",
            "yes" if (b.website_flags or {}).get("missing_booking") else "no",
            "yes" if (b.website_flags or {}).get("missing_online_store") else "no",
            "yes" if (b.website_flags or {}).get("has_email") else "no",
            "yes" if (b.website_flags or {}).get("has_decision_maker") else "no",
            b.lead_quality_score if b.lead_quality_score is not None else "",
            b.lead_quality_label or "",
            "; ".join(b.lead_quality_signals) if b.lead_quality_signals else "",
            b.learning_boost if b.learning_boost else "",
            "; ".join(b.brand_book.colors) if b.brand_book and b.brand_book.colors else "",
            (b.brand_book.tone if b.brand_book else "") or "",
            (b.brand_book.pricing_positioning if b.brand_book else "") or "",
            (b.brand_book.imagery_style if b.brand_book else "") or "",
            (b.brand_book.source if b.brand_book else "") or "",
            b.no_website_score if b.no_website_score is not None else "",
            b.no_website_label or "",
            b.no_website_status or "",
            b.suspected_website or "",
            "; ".join(b.no_website_signals) if b.no_website_signals else "",
            "yes" if b.has_website else "no",
            b.website_url or "",
            b.rating or "",
            b.review_count or "",
            b.category or "",
            b.address or "",
            b.phone or "",
            b.hours or "",
            b.is_open if b.is_open is not None else "",
            b.areas_served or "",
            b.province or "",
            b.located_in or "",
            b.description or "",
            b.appointment_url or "",
            b.photo_count or "",
            b.google_maps_url or "",
            b.latitude or "",
            b.longitude or "",
            b.place_id or "",
            "; ".join(b.categories),
            " | ".join(f"{p.name} ({p.price or 'N/A'})" for p in b.products),
            " | ".join(f"{r.author}: {r.text[:100] if r.text else ''}" for r in b.reviews[:3]),
            json.dumps(b.social_profiles) if b.social_profiles else "",
        ])

    return output.getvalue()


def generate_filename(address: str, ext: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "" for c in address)[:40].strip()
    safe = safe.replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"businesses_outreach_{safe}_{timestamp}.{ext}"
