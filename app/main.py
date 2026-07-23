import asyncio
import math
import uuid
from pathlib import Path

import httpx
import stripe
from fastapi import Depends, FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.services.google_auth import is_google_configured
from app.services.google_setup import ensure_google_apis_enabled, diagnose_google_access, parse_google_error
from app.models import (
    SearchRequest, SearchResponse, SearchStatus, Business,
    FigmaConnectRequest, FigmaCreateSiteRequest,
    GmailConnectRequest, SendEmailRequest, OpenAIConnectRequest,
    SignupRequest, SigninRequest, CheckoutRequest, PreviewEmailRequest,
    RenderEmailRequest,
    PipelineStatusRequest, FavoriteBusinessRequest,
    CampaignGenerateRequest,
)
from app.services.outreach_tracker import (
    record_outreach_send, mark_opened, list_deals, update_deal_status,
    get_deals_for_places, PIXEL_GIF, PIPELINE_STATUSES,
)
from app.services.favorites import (
    list_favorites,
    add_favorite,
    remove_favorite,
    get_favorite_keys_for_places,
)
from app.services.figma_service import create_site_for_business
from app.services.brand_book import (
    enrich_businesses_with_brand_books,
    build_brand_book_for_business,
)
from app.services.openai_service import enrich_businesses_with_openai, translate_outreach_email
from app.services.campaign import generate_marketing_campaign
from app.services.search_jobs import (
    create_search_job,
    sync_search_job,
    get_search_job,
    get_active_search_job,
    get_latest_completed_search,
    fail_stale_running_jobs,
)
from app.services import gmail_oauth
from app.services.user_integrations import (
    figma_status as get_user_figma_status,
    connect_figma_for_user,
    disconnect_figma_for_user,
    get_user_figma_token,
    openai_status as get_user_openai_status,
    connect_openai_for_user,
    disconnect_openai_for_user,
    get_user_openai_key,
    gmail_status as get_user_gmail_status,
    connect_gmail_smtp_for_user,
    disconnect_gmail_for_user,
    save_gmail_oauth_for_user,
    user_gmail_connected,
    send_email_as_user,
)
from app.services.email_finder import merge_emails, keep_businesses_with_verified_emails
from app.services.outreach import (
    generate_send_email,
    resolve_from_display_name,
    append_compliance_footer,
    wrap_outreach_plain_as_html,
    parse_sender_info,
    get_available_email_templates,
)
from app.services.geocoder import geocode_address
from app.services.places_api import (
    search_nearby_businesses,
    search_text_businesses,
    candidate_pool_size,
    PLACE_TYPES_SMB,
    PLACE_TYPES_LARGE,
    annotate_distance_from_center,
    sort_businesses_by_distance,
    quota_expand_rings,
)
from app.services.maps_scraper import enrich_businesses
from app.services.outreach_pipeline import find_emails_for_businesses
from app.services.website_verifier import verify_businesses_no_website
from app.services.lead_quality import rank_businesses
from app.services.website_opportunity import score_website_opportunities
from app.services.learning_loop import get_conversion_insights, get_learning_summary
from app.services.exporter import businesses_to_json, businesses_to_csv, generate_filename
from app.services.db import connect_db, close_db
from app.services.auth_service import (
    authenticate_user, require_user, get_current_user_optional,
    serialize_user, set_session_cookie, clear_session_cookie,
)
from app.services.email_verification import start_email_signup, complete_email_signup
from app.services.billing import (
    create_checkout_session, consume_search_credit, get_billing_report,
    get_usage_summary, handle_checkout_completed, handle_invoice_paid,
)
from app.services.plans import get_plan, required_results_for_plan
from app.services import rate_limit

_docs_enabled = not settings.is_production
app = FastAPI(
    title="CH4PP!3 - Business Finder",
    description="Find local businesses without websites within a radius of any address",
    version="1.0.0",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

jobs: dict[str, SearchStatus] = {}
job_owners: dict[str, str] = {}

# Every search result set must include at least this share of businesses with email.
EMAIL_RESULT_MIN_RATIO = 0.60


def _html(name: str) -> HTMLResponse:
    return HTMLResponse(content=(STATIC_DIR / name).read_text(encoding="utf-8"))


@app.on_event("startup")
async def startup():
    settings.assert_secure_config()
    try:
        await connect_db()
        try:
            n = await fail_stale_running_jobs(older_than_hours=6.0)
            if n:
                print(f"[chappie] Marked {n} stale search job(s) as failed")
        except Exception as e:
            print(f"[chappie] Could not clean stale search jobs: {e}")
    except Exception as e:
        print(f"[chappie] MongoDB connection failed: {e}")
    try:
        ensure_google_apis_enabled()
    except Exception:
        pass
    gmail_oauth.init_gmail_oauth()
    from app.services.gmail_sender import init_system_gmail_from_settings
    init_system_gmail_from_settings()
    # OpenAI / Figma / user Gmail are per-account - not auto-connected from .env


@app.on_event("shutdown")
async def shutdown():
    await close_db()


async def _run_search(
    job_id: str,
    request: SearchRequest,
    max_results: int,
    openai_api_key: str | None = None,
    user_id: str | None = None,
    plan_id: str | None = None,
) -> None:
    job = jobs[job_id]
    job.status = "running"

    async def _persist(force: bool = False) -> None:
        try:
            await sync_search_job(job, force=force)
        except Exception as e:
            print(f"[chappie] search job persist failed ({job_id}): {e}")

    async def on_progress(current: int, total: int, message: str) -> None:
        job.progress = current
        job.total = total
        job.message = message
        await _persist(force=False)

    await _persist(force=True)

    try:
        lat, lng, formatted = await geocode_address(request.address)
        job.message = f"Geocoded: {formatted}"
        await _persist(force=False)

        target = required_results_for_plan(plan_id) if plan_id else max(1, int(max_results))
        target = max(target, max(1, int(max_results)))
        is_large = (plan_id or "") == "large"
        # Large includes businesses with websites; other plans prioritize no-website leads
        require_no_website = not is_large
        place_types = PLACE_TYPES_LARGE if is_large else PLACE_TYPES_SMB
        keep_with_website = is_large
        pool_target = candidate_pool_size(target, large_plan=is_large)
        per_type_limit = 20 if is_large else 12
        exhaust_types = True
        email_batch = 12
        # Keep expanding until plan quotas are met (radius can grow to 50 km)
        expand_rings = quota_expand_rings(request.radius_km, hard_max_km=50.0)
        max_refill_rounds = max(16, len(expand_rings) * 2)
        min_emails = max(1, math.ceil(target * EMAIL_RESULT_MIN_RATIO))

        job.message = (
            f"Collecting {target} businesses "
            f"(≥{min_emails} with email / {int(EMAIL_RESULT_MIN_RATIO * 100)}% required). "
            f"Starting near center, expanding up to 50 km if needed..."
        )

        async def _fetch_candidates(
            max_n: int,
            exclude: set[str] | None = None,
            *,
            radius_km: float | None = None,
        ) -> list[Business]:
            return await search_nearby_businesses(
                lat,
                lng,
                radius_km if radius_km is not None else request.radius_km,
                progress_callback=on_progress,
                max_results=max_n,
                exclude_ids=exclude,
                require_no_website=require_no_website,
                place_types=place_types,
                per_type_limit=per_type_limit,
                exhaust_types=exhaust_types,
            )

        def _with_distances(items: list[Business]) -> list[Business]:
            return sort_businesses_by_distance(
                annotate_distance_from_center(items, lat, lng)
            )

        def _dedupe_extend(base: list[Business], extra: list[Business]) -> list[Business]:
            keys = {
                ((b.place_id or ""), (b.name or "").lower(), (b.address or "").lower())
                for b in base
            }
            out = list(base)
            for b in extra:
                key = ((b.place_id or ""), (b.name or "").lower(), (b.address or "").lower())
                if key in keys:
                    continue
                keys.add(key)
                out.append(b)
            return out

        async def _pull_at_radius(
            radius_km: float,
            *,
            need: int,
            exclude: set[str],
        ) -> list[Business]:
            """Nearby + text search at a radius — used for all plans when filling quotas."""
            more = await _fetch_candidates(
                max(need * 2, min(150, need + 40)),
                exclude,
                radius_km=radius_km,
            )
            text_extra = await search_text_businesses(
                lat,
                lng,
                radius_km,
                area_label=formatted,
                require_no_website=require_no_website,
                exclude_ids=exclude,
                max_results=max(need * 2, 80),
                progress_callback=on_progress,
            )
            return _dedupe_extend(more or [], text_extra)

        async def _ensure_candidate_floor(min_count: int) -> list[Business]:
            """Grow from near-center rings out to 50 km until we have min_count candidates."""
            pool: list[Business] = []
            stagnant = 0
            for ring_km in expand_rings:
                if len(pool) >= min_count:
                    break
                exclude = {b.place_id for b in pool if b.place_id}
                remaining = min_count - len(pool)
                job.message = (
                    f"Searching within {ring_km:g} km "
                    f"({len(pool)}/{min_count} candidates for {target} results)..."
                )
                more = await _pull_at_radius(ring_km, need=remaining, exclude=exclude)
                before = len(pool)
                pool = _with_distances(_dedupe_extend(pool, more or []))
                if len(pool) == before:
                    stagnant += 1
                    if stagnant >= 2 and ring_km >= 20:
                        # Still continue outer rings — sparse areas need the full 50 km
                        continue
                else:
                    stagnant = 0
            return _with_distances(pool)

        # Over-collect candidates so email finding can hit the 60% floor
        candidates = await _ensure_candidate_floor(max(pool_target, target * 3, min_emails * 4))

        kept_with_email: list[Business] = []
        scanned = 0

        def _has_email(b: Business) -> bool:
            return bool(
                (b.contact_email and str(b.contact_email).strip())
                or any(str(e).strip() for e in (b.contact_emails or []))
            )

        def _assemble_with_email_quota(
            email_leads: list[Business],
            pool: list[Business],
            limit: int,
        ) -> list[Business]:
            """Build a result list with at least EMAIL_RESULT_MIN_RATIO email leads."""
            emails = _with_distances([b for b in email_leads if _has_email(b)])
            n_email = len(emails)
            if n_email == 0:
                return []
            # total <= n_email / ratio  ⇒  emails/total >= ratio
            max_total = min(limit, max(n_email, math.floor(n_email / EMAIL_RESULT_MIN_RATIO)))
            out: list[Business] = list(emails[:max_total])
            seen = {b.place_id for b in out if b.place_id}
            for c in _with_distances(pool):
                if len(out) >= max_total:
                    break
                if c.place_id and c.place_id in seen:
                    continue
                # Adding a no-email row must not drop below the ratio
                if n_email < EMAIL_RESULT_MIN_RATIO * (len(out) + 1):
                    break
                out.append(c)
                if c.place_id:
                    seen.add(c.place_id)
            return _with_distances(out)

        async def _process_email_batches(*, email_goal: int) -> None:
            nonlocal scanned, kept_with_email
            while len(kept_with_email) < email_goal and scanned < len(candidates):
                batch = candidates[scanned : scanned + email_batch]
                scanned += len(batch)
                job.message = (
                    f"Finding emails: {len(kept_with_email)}/{email_goal} needed "
                    f"(≥{int(EMAIL_RESULT_MIN_RATIO * 100)}% of {target}; "
                    f"scanned {scanned}/{len(candidates)})..."
                )
                work = batch
                if request.enrich_details and work:
                    work = await enrich_businesses(
                        work,
                        progress_callback=on_progress,
                        keep_with_website=keep_with_website,
                    )
                by_id = {b.place_id: b for b in work if b and b.place_id}
                for i, c in enumerate(candidates):
                    if c.place_id and c.place_id in by_id:
                        candidates[i] = by_id[c.place_id]

                if work:
                    work = await find_emails_for_businesses(
                        work,
                        progress_callback=on_progress,
                    )
                    by_id = {b.place_id: b for b in work if b and b.place_id}
                    for i, c in enumerate(candidates):
                        if c.place_id and c.place_id in by_id:
                            candidates[i] = by_id[c.place_id]

                if work:
                    with_email = await keep_businesses_with_verified_emails(work)
                    if with_email:
                        seen_email_ids = {
                            b.place_id for b in kept_with_email if b.place_id
                        }
                        for biz in with_email:
                            if biz.place_id and biz.place_id in seen_email_ids:
                                continue
                            if biz.place_id:
                                seen_email_ids.add(biz.place_id)
                            kept_with_email.append(biz)
                            if len(kept_with_email) >= email_goal:
                                break
                job.message = (
                    f"Email quota {min(len(kept_with_email), email_goal)}/{email_goal} "
                    f"(≥{int(EMAIL_RESULT_MIN_RATIO * 100)}% of results) "
                    f"after {scanned}/{len(candidates)} candidates..."
                )

        # Always run the email pass so every plan can meet the 60% email quota
        if candidates:
            await _process_email_batches(email_goal=max(min_emails, target))

            refill_rounds = 0
            while (
                len(kept_with_email) < target
                and refill_rounds < max_refill_rounds
            ):
                refill_rounds += 1
                ring_km = expand_rings[min(refill_rounds, len(expand_rings) - 1)]
                exclude = {b.place_id for b in candidates if b.place_id}
                more_needed = min(
                    candidate_pool_size(
                        max(target - len(kept_with_email), min_emails) * 2,
                        large_plan=is_large,
                    ),
                    150,
                )
                scope = "all businesses" if is_large else "no-website businesses"
                job.message = (
                    f"Need more emails ({len(kept_with_email)}/{target}, "
                    f"min {min_emails} for {int(EMAIL_RESULT_MIN_RATIO * 100)}% quota) — "
                    f"searching {scope} within {ring_km:g} km "
                    f"(round {refill_rounds})..."
                )
                more = await _pull_at_radius(
                    ring_km, need=more_needed, exclude=exclude
                )
                if not more:
                    # Jump to next outer ring; only stop after the last ring
                    if ring_km >= expand_rings[-1] - 0.05:
                        break
                    continue
                before = len(candidates)
                candidates = _with_distances(_dedupe_extend(candidates, more))
                if len(candidates) == before:
                    if ring_km >= expand_rings[-1] - 0.05:
                        break
                    continue
                await _process_email_batches(email_goal=max(min_emails, target))

            # Prefer closest email leads, then pad without dropping below 60% emails.
            # Keep expanding until we hit the plan's fixed result count when possible.
            candidates = _with_distances(candidates)
            kept_with_email = _with_distances(kept_with_email)
            businesses = _assemble_with_email_quota(
                kept_with_email, candidates, target
            )

            fill_rounds = 0
            while (
                (len(businesses) < target or len(kept_with_email) < min_emails)
                and fill_rounds < max_refill_rounds
            ):
                fill_rounds += 1
                ring_km = expand_rings[min(fill_rounds, len(expand_rings) - 1)]
                job.message = (
                    f"Filling plan quota {len(businesses)}/{target} "
                    f"({len(kept_with_email)}/{min_emails} with email) — "
                    f"expanding to {ring_km:g} km (round {fill_rounds})..."
                )
                exclude = {b.place_id for b in candidates if b.place_id}
                more = await _pull_at_radius(
                    ring_km, need=min(150, target * 3), exclude=exclude
                )
                before = len(candidates)
                candidates = _with_distances(_dedupe_extend(candidates, more or []))
                if len(candidates) == before:
                    if ring_km >= expand_rings[-1] - 0.05:
                        break
                    continue
                await _process_email_batches(email_goal=max(min_emails, target))
                kept_with_email = _with_distances(kept_with_email)
                businesses = _assemble_with_email_quota(
                    kept_with_email, candidates, target
                )
                if len(businesses) >= target and len(kept_with_email) >= min_emails:
                    break

            # Enrich any padded (non-email) rows that skipped enrich during email pass
            if request.enrich_details and businesses:
                need_enrich = [
                    b for b in businesses
                    if not _has_email(b) and (not b.social_profiles or b.hours is None)
                ]
                if need_enrich:
                    job.message = f"Enriching {len(need_enrich)} additional listings..."
                    enriched = await enrich_businesses(
                        need_enrich,
                        progress_callback=on_progress,
                        keep_with_website=keep_with_website,
                    )
                    by_id = {b.place_id: b for b in enriched if b.place_id}
                    businesses = [
                        by_id.get(b.place_id, b) if b.place_id in by_id else b
                        for b in businesses
                    ]

            with_mail = sum(1 for b in businesses if _has_email(b))
            no_site = sum(1 for b in businesses if not b.has_website)
            ratio_pct = int(round(100 * with_mail / len(businesses))) if businesses else 0
            if len(businesses) < target or with_mail < min_emails:
                job.message = (
                    f"Quota shortfall after expanding to {expand_rings[-1]:g} km: "
                    f"{len(businesses)}/{target} businesses "
                    f"({with_mail}/{min_emails} with email = {ratio_pct}%). "
                    f"Scanned {scanned}/{len(candidates)} candidates."
                )
            else:
                job.message = (
                    f"Ready {len(businesses)}/{target} businesses "
                    f"({with_mail} with email = {ratio_pct}%, need ≥{int(EMAIL_RESULT_MIN_RATIO * 100)}% "
                    f"and {min_emails}+ emails; "
                    f"{no_site} with no website; "
                    f"scanned {scanned}/{len(candidates)} candidates)."
                )
        else:
            businesses = []

        if openai_api_key and businesses:
            job.message = "Using OpenAI to fill missing business details..."
            businesses = await enrich_businesses_with_openai(
                businesses,
                progress_callback=on_progress,
                api_key=openai_api_key,
            )

        if businesses:
            job.message = "Verifying website status..."
            businesses = await verify_businesses_no_website(
                businesses, progress_callback=on_progress
            )

        if businesses:
            job.message = "Scoring website opportunities..."
            businesses = await score_website_opportunities(
                businesses, progress_callback=on_progress
            )

        if businesses:
            job.message = "Ranking lead quality (learning loop)..."
            insights = await get_conversion_insights(user_id or "")
            businesses = rank_businesses(
                businesses,
                target_categories=request.target_categories,
                insights=insights,
            )
            # Keep closest-to-center first for all plans (quality scores still on each card)
            businesses = _with_distances(businesses)

        if businesses:
            job.message = "Building brand books (colors, logo, tone, imagery)..."
            businesses = await enrich_businesses_with_brand_books(
                businesses,
                api_key=openai_api_key,
                user_id=user_id,
                progress_callback=on_progress,
            )
            businesses = _with_distances(businesses)

        # Final guard: never return a list below the email ratio
        if businesses:
            with_mail = [b for b in businesses if _has_email(b)]
            without_mail = [b for b in businesses if not _has_email(b)]
            businesses = _assemble_with_email_quota(
                with_mail,
                with_mail + without_mail,
                target,
            )

        job.result = SearchResponse(
            center_address=formatted,
            center_lat=lat,
            center_lng=lng,
            radius_km=request.radius_km,
            total_found=len(businesses),
            businesses=businesses,
        )
        job.status = "completed"
        high = sum(1 for b in businesses if (b.no_website_score or 0) >= 80)
        no_site = sum(1 for b in businesses if not b.has_website)
        great = sum(1 for b in businesses if (b.lead_quality_score or 0) >= 75)
        hot_opp = sum(1 for b in businesses if (b.website_opportunity_score or 0) >= 75)
        boosted = sum(1 for b in businesses if (b.learning_boost or 0) > 0)
        branded = sum(1 for b in businesses if b.brand_book and b.brand_book.status == "ready")
        with_mail = sum(1 for b in businesses if b.contact_email)
        job.message = (
            f"Done! Found {len(businesses)} businesses"
            + (f" ({with_mail} with contact email)" if with_mail else "")
            + (f", {no_site} with no website" if is_large else f", {high} verified no website")
            + f" — {hot_opp} hot website opportunities"
            + f", {great} great leads"
            + (f", {boosted} boosted by learning" if boosted else "")
            + (f", {branded} brand books" if branded else "")
            + "."
        )
        await _persist(force=True)

    except httpx.HTTPStatusError as e:
        job.status = "failed"
        job.error = parse_google_error(e.response) if e.response else str(e)
        job.message = f"Error: {job.error}"
        await _persist(force=True)
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.message = f"Error: {e}"
        await _persist(force=True)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(
        STATIC_DIR / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/", response_class=HTMLResponse)
async def intro():
    return _html("intro.html")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    return _html("privacy.html")


@app.get("/terms", response_class=HTMLResponse)
async def terms_of_service():
    return _html("terms.html")


@app.get("/refunds", response_class=HTMLResponse)
async def refund_policy():
    return _html("refunds.html")


@app.get("/cookies", response_class=HTMLResponse)
async def cookie_policy():
    return _html("cookies.html")


@app.get("/acceptable-use", response_class=HTMLResponse)
async def acceptable_use_policy():
    return _html("acceptable-use.html")


@app.get("/app", response_class=HTMLResponse)
async def app_page(user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse("/?signin=1", status_code=302)
    return _html("index.html")


@app.get("/pipeline", response_class=HTMLResponse)
async def pipeline_page(user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse("/?signin=1", status_code=302)
    plan = get_plan(user.get("plan"))
    if plan.id == "free":
        return RedirectResponse("/account#upgradePanel", status_code=302)
    return _html("pipeline.html")


@app.get("/favorites", response_class=HTMLResponse)
async def favorites_page(user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse("/?signin=1", status_code=302)
    return _html("favorites.html")


@app.get("/account", response_class=HTMLResponse)
async def account_page(user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse("/?signin=1", status_code=302)
    return _html("account.html")


@app.get("/api/favorites")
async def favorites_list(user=Depends(require_user)):
    return await list_favorites(str(user["_id"]))


@app.post("/api/favorites")
async def favorites_add(body: FavoriteBusinessRequest, user=Depends(require_user)):
    try:
        favorite = await add_favorite(str(user["_id"]), body.business.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"favorite": favorite}


@app.delete("/api/favorites/by-place/{place_id}")
async def favorites_remove_by_place(place_id: str, user=Depends(require_user)):
    try:
        removed = await remove_favorite(str(user["_id"]), place_id=place_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="Favorite not found.")
    return {"removed": True}


@app.delete("/api/favorites/{favorite_id}")
async def favorites_remove(favorite_id: str, user=Depends(require_user)):
    try:
        removed = await remove_favorite(str(user["_id"]), favorite_id=favorite_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not removed:
        raise HTTPException(status_code=404, detail="Favorite not found.")
    return {"removed": True}


@app.post("/api/favorites/lookup")
async def favorites_lookup(place_ids: list[str], user=Depends(require_user)):
    return {"favorites": await get_favorite_keys_for_places(str(user["_id"]), place_ids)}


def _require_paid_plan(user: dict):
    plan = get_plan(user.get("plan"))
    if plan.id == "free":
        raise HTTPException(
            status_code=403,
            detail="Pipeline requires Small Biz, Mid Biz, or Large Biz.",
        )
    return plan


@app.get("/api/pipeline")
async def pipeline_list(status: str = "", user=Depends(require_user)):
    _require_paid_plan(user)
    return await list_deals(str(user["_id"]), status=status or None)


@app.get("/api/pipeline/insights")
async def pipeline_insights(user=Depends(require_user)):
    _require_paid_plan(user)
    return await get_learning_summary(str(user["_id"]))


@app.patch("/api/pipeline/{deal_id}")
async def pipeline_update(deal_id: str, body: PipelineStatusRequest, user=Depends(require_user)):
    _require_paid_plan(user)
    try:
        deal = await update_deal_status(
            str(user["_id"]),
            deal_id,
            body.status,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"deal": deal}


@app.post("/api/pipeline/lookup")
async def pipeline_lookup(place_ids: list[str], user=Depends(require_user)):
    # Allow lookup for all plans so search cards can show badges without 403s
    return {"deals": await get_deals_for_places(str(user["_id"]), place_ids)}


@app.get("/api/track/open/{token}")
async def track_open(token: str):
    await mark_opened(token)
    return Response(content=PIXEL_GIF, media_type="image/gif", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    })


# ── Auth ──

@app.post("/api/auth/signup")
async def signup(body: SignupRequest, request: Request):
    """Start signup — sends verification email; account is created only after verify."""
    client = request.client.host if request.client else "unknown"
    if not rate_limit.allow(f"signup:{client}", limit=5, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many signup attempts. Try again shortly.")
    try:
        result = await start_email_signup(
            body.email,
            body.password,
            body.name,
            pending_plan=body.pending_plan,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not start signup: {e}")
    return result


@app.get("/api/auth/verify")
async def verify_email(token: str = ""):
    """Create the account after the user clicks Verify in their email."""
    try:
        user = await complete_email_signup(token)
    except ValueError as e:
        from urllib.parse import quote
        return RedirectResponse(
            f"/?verify_error={quote(str(e)[:200])}",
            status_code=302,
        )

    pending_plan = user.get("_pending_plan")
    if pending_plan in ("small", "mid", "large"):
        redirect = RedirectResponse(
            f"/account?verified=1&upgrade={pending_plan}",
            status_code=302,
        )
    else:
        redirect = RedirectResponse("/app?verified=1", status_code=302)
    set_session_cookie(redirect, str(user["_id"]))
    return redirect


@app.post("/api/auth/signin")
async def signin(body: SigninRequest, request: Request, response: Response):
    client = request.client.host if request.client else "unknown"
    if not rate_limit.allow(f"signin:{client}", limit=10, window_seconds=60):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again shortly.")
    user = await authenticate_user(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    set_session_cookie(response, str(user["_id"]))
    return {"user": serialize_user(user)}


@app.post("/api/auth/signout")
async def signout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user=Depends(get_current_user_optional)):
    if not user:
        return {"user": None}
    return {"user": serialize_user(user)}


# ── Account / billing ──

@app.get("/api/account/usage")
async def account_usage(user=Depends(require_user)):
    return await get_usage_summary(user)


@app.get("/api/account/billing")
async def account_billing(user=Depends(require_user)):
    events = await get_billing_report(str(user["_id"]))
    usage = await get_usage_summary(user)
    return {"usage": usage, "events": events, "user": serialize_user(user)}


@app.post("/api/billing/checkout")
async def billing_checkout(body: CheckoutRequest, user=Depends(require_user)):
    try:
        url = await create_checkout_session(user, body.plan)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"url": url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if not settings.stripe_webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Stripe webhook is not configured (STRIPE_WEBHOOK_SECRET required).",
        )
    if settings.stripe_secret_key:
        stripe.api_key = settings.stripe_secret_key
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.stripe_webhook_secret
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        obj = data if isinstance(data, dict) else {
            "id": data.get("id"),
            "metadata": dict(data.get("metadata") or {}),
            "subscription": data.get("subscription"),
            "amount_total": data.get("amount_total"),
            "payment_status": data.get("payment_status"),
        }
        await handle_checkout_completed(obj)
    elif etype == "invoice.paid":
        obj = data if isinstance(data, dict) else dict(data)
        await handle_invoice_paid(obj)

    return {"received": True}


@app.get("/api/billing/confirm")
async def billing_confirm(session_id: str = "", user=Depends(require_user)):
    """Fallback activation if webhook is delayed (local dev)."""
    if not session_id or not settings.stripe_secret_key:
        return {"user": serialize_user(user)}
    stripe.api_key = settings.stripe_secret_key
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if session.payment_status not in ("paid", "no_payment_required"):
        raise HTTPException(status_code=400, detail="Payment not completed yet.")

    meta = dict(session.metadata or {})
    if str(meta.get("user_id") or "") != str(user["_id"]):
        raise HTTPException(status_code=403, detail="Checkout session does not belong to this account.")

    session_data = {
        "id": session.id,
        "metadata": meta,
        "subscription": session.subscription,
        "amount_total": session.amount_total,
        "payment_status": session.payment_status,
    }
    await handle_checkout_completed(session_data)
    from app.services.auth_service import get_user_by_id
    refreshed = await get_user_by_id(str(user["_id"]))
    return {"user": serialize_user(refreshed)}


@app.post("/api/search")
async def start_search(
    request: SearchRequest,
    background_tasks: BackgroundTasks,
    user=Depends(require_user),
):
    if not is_google_configured():
        raise HTTPException(
            status_code=400,
            detail="Google credentials not configured. Set GOOGLE_APPLICATION_CREDENTIALS in .env",
        )

    plan = get_plan(user.get("plan"))
    try:
        await consume_search_credit(user)
    except ValueError as e:
        raise HTTPException(
            status_code=402,
            detail={
                "message": str(e),
                "exhausted_action": plan.exhausted_action,
                "plan": plan.id,
            },
        )

    max_results = required_results_for_plan(plan.id)
    # Always use the plan's fixed search size (ignore client under-requests)

    job_id = str(uuid.uuid4())
    user_id = str(user["_id"])
    jobs[job_id] = SearchStatus(
        job_id=job_id,
        status="pending",
        message="Starting search...",
    )
    job_owners[job_id] = user_id
    try:
        await create_search_job(
            job_id=job_id,
            user_id=user_id,
            request_payload=request.model_dump(),
            plan_id=plan.id,
        )
    except Exception as e:
        # Don't block search if persist fails; in-memory still works for this process
        print(f"[chappie] create_search_job failed: {e}")

    background_tasks.add_task(
        _run_search,
        job_id,
        request,
        max_results,
        get_user_openai_key(user),
        user_id,
        plan.id,
    )
    return {
        "job_id": job_id,
        "max_results": max_results,
        "user": serialize_user(user),
    }


@app.get("/api/search/active")
async def search_active(user=Depends(require_user)):
    """Return the user's in-progress search, if any."""
    user_id = str(user["_id"])
    stored = await get_active_search_job(user_id)
    if stored and stored.job_id in jobs and job_owners.get(stored.job_id) == user_id:
        return {"job": jobs[stored.job_id]}
    if stored:
        return {"job": stored}
    return {"job": None}


@app.get("/api/search/latest")
async def search_latest(user=Depends(require_user)):
    """Return the user's most recent completed search results."""
    user_id = str(user["_id"])
    job = await get_latest_completed_search(user_id)
    if (
        job
        and job.job_id in jobs
        and jobs[job.job_id].status == "completed"
        and job_owners.get(job.job_id) == user_id
    ):
        return {"job": jobs[job.job_id]}
    return {"job": job}


@app.get("/api/search/{job_id}", response_model=SearchStatus)
async def get_search_status(job_id: str, user=Depends(require_user)):
    user_id = str(user["_id"])
    if job_id in jobs and job_owners.get(job_id) == user_id:
        return jobs[job_id]

    job = await get_search_job(job_id, user_id=user_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    jobs[job_id] = job
    job_owners[job_id] = user_id
    return job


@app.get("/api/download/{job_id}")
async def download_results(job_id: str, format: str = "csv", user=Depends(require_user)):
    user_id = str(user["_id"])
    job = None
    if job_id in jobs and job_owners.get(job_id) == user_id:
        job = jobs[job_id]
    else:
        job = await get_search_job(job_id, user_id=user_id)
        if job:
            jobs[job_id] = job
            job_owners[job_id] = user_id

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "completed" or not job.result:
        raise HTTPException(status_code=400, detail="Search not completed yet")

    if format == "json":
        content = businesses_to_json(job.result)
        filename = generate_filename(job.result.center_address, "json")
        media_type = "application/json"
    else:
        content = businesses_to_csv(job.result.businesses)
        filename = generate_filename(job.result.center_address, "csv")
        media_type = "text/csv"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/diagnose")
async def diagnose(user=Depends(require_user)):
    if not settings.enable_diagnostics:
        raise HTTPException(status_code=404, detail="Not found")
    return await diagnose_google_access()


@app.get("/api/figma/status")
async def figma_status_route(user=Depends(require_user)):
    return get_user_figma_status(user)


@app.post("/api/figma/connect")
async def figma_connect(request: FigmaConnectRequest, user=Depends(require_user)):
    try:
        return await connect_figma_for_user(user, request.token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/figma/disconnect")
async def figma_disconnect(user=Depends(require_user)):
    return await disconnect_figma_for_user(user)


@app.post("/api/figma/create-site")
async def figma_create_site(request: FigmaCreateSiteRequest, user=Depends(require_user)):
    business = request.business
    # Ensure brand book exists (use cache / generate if missing)
    if not business.brand_book or business.brand_book.status != "ready":
        try:
            book = await build_brand_book_for_business(
                business,
                api_key=get_user_openai_key(user),
                user_id=str(user["_id"]),
            )
            business = business.model_copy(update={"brand_book": book})
        except Exception:
            pass
    try:
        return await create_site_for_business(
            business,
            connected=bool(get_user_figma_token(user)),
            openai_api_key=get_user_openai_key(user),
            language=request.language or "English",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/openai/status")
async def openai_status_route(user=Depends(require_user)):
    return get_user_openai_status(user)


@app.post("/api/openai/connect")
async def openai_connect(request: OpenAIConnectRequest, user=Depends(require_user)):
    try:
        return await connect_openai_for_user(user, request.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/openai/disconnect")
async def openai_disconnect(user=Depends(require_user)):
    return await disconnect_openai_for_user(user)


@app.post("/api/campaign/generate")
async def campaign_generate(request: CampaignGenerateRequest, user=Depends(require_user)):
    plan = get_plan(user.get("plan"))
    if plan.id != "large":
        raise HTTPException(
            status_code=403,
            detail="Marketing campaigns are available on the Large Biz plan only.",
        )
    api_key = get_user_openai_key(user)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Connect OpenAI in Integrations before creating a campaign.",
        )
    try:
        campaign = await generate_marketing_campaign(
            request.business,
            api_key=api_key,
            goal=request.goal or "auto",
            user_id=str(user["_id"]),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Campaign generation failed: {str(e)[:240]}")
    return campaign.model_dump()


@app.get("/api/gmail/status")
async def gmail_status_route(user=Depends(require_user)):
    return get_user_gmail_status(user)


@app.post("/api/gmail/connect")
async def gmail_connect(request: GmailConnectRequest, user=Depends(require_user)):
    try:
        return await connect_gmail_smtp_for_user(user, request.email, request.app_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/gmail/disconnect")
async def gmail_disconnect(user=Depends(require_user)):
    return await disconnect_gmail_for_user(user)


@app.get("/api/gmail/oauth/setup")
async def gmail_oauth_setup(user=Depends(require_user)):
    return gmail_oauth.get_oauth_setup_info()


@app.get("/api/gmail/oauth/start")
async def gmail_oauth_start(user=Depends(require_user)):
    try:
        url = gmail_oauth.start_oauth_flow(str(user["_id"]))
        return RedirectResponse(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/gmail/oauth/callback")
async def gmail_oauth_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse("/app?gmail_error=" + error)
    try:
        creds, user_id = gmail_oauth.complete_oauth_flow(code, state)
        if not user_id:
            return RedirectResponse("/app?gmail_error=Missing+user+session.+Try+Connect+with+Google+again.")
        await save_gmail_oauth_for_user(user_id, creds)
        return RedirectResponse("/app?gmail_connected=1")
    except Exception as e:
        return RedirectResponse("/app?gmail_error=" + str(e)[:200])


def _email_design_allowed(user: dict) -> bool:
    plan = get_plan(user.get("plan"))
    return plan.id in ("small", "mid", "large")


def _resolve_email_design(user: dict, template_id: str = "", logo_url: str = "") -> tuple[str, str]:
    """Paid plans can customize; free plan is forced to the default design."""
    if _email_design_allowed(user):
        return (template_id or "midnight_teal").strip(), (logo_url or "").strip()
    return "midnight_teal", ""


@app.get("/api/email/templates")
async def email_templates(user=Depends(require_user)):
    return {
        "templates": get_available_email_templates(),
        "design_unlocked": _email_design_allowed(user),
    }


@app.post("/api/email/render")
async def render_email_design(request: RenderEmailRequest, user=Depends(require_user)):
    if not request.body.strip():
        raise HTTPException(status_code=400, detail="Email body is required.")
    if not _email_design_allowed(user):
        raise HTTPException(
            status_code=403,
            detail="Email design customization requires Small Biz, Mid Biz, or Large Biz.",
        )
    template_id, logo_url = _resolve_email_design(user, request.template_id, request.logo_url)
    sender = parse_sender_info(
        request.sender_business_info,
        request.sender_business_name,
    )
    html_body = wrap_outreach_plain_as_html(
        request.body,
        studio_name=sender.get("name") or resolve_from_display_name(
            request.sender_business_info,
            user_name=user.get("name"),
            business_name=request.sender_business_name,
        ),
        studio_email=sender.get("email") or "",
        studio_url=sender.get("url") or "",
        business_name=request.business_name,
        prototype_url=request.figma_prototype_link,
        template_id=template_id,
        logo_url=logo_url,
    )
    return {
        "html_body": html_body,
        "template_id": template_id,
        "design_unlocked": True,
    }


@app.post("/api/email/preview")
async def preview_email(request: PreviewEmailRequest, user=Depends(require_user)):
    recipients = merge_emails(request.business.contact_emails, request.business.contact_email)
    if not recipients:
        raise HTTPException(status_code=400, detail="This business has no contact email.")

    if not request.sender_business_name.strip():
        raise HTTPException(
            status_code=400,
            detail="Add your business name in the sidebar before sending.",
        )

    if not request.sender_business_info.strip():
        raise HTTPException(
            status_code=400,
            detail="Add Your Business Info in the sidebar before sending.",
        )

    try:
        language = (request.language or "English").strip() or "English"
        template_id, logo_url = _resolve_email_design(
            user, request.template_id, request.logo_url
        )
        subject, body, html_body = generate_send_email(
            request.business,
            sender_info=request.sender_business_info,
            figma_prototype_link=request.figma_prototype_link,
            sender_business_name=request.sender_business_name,
            template_id=template_id,
            logo_url=logo_url,
        )
        subject, body = await translate_outreach_email(
            subject,
            body,
            language,
            api_key=get_user_openai_key(user),
        )
        sender = parse_sender_info(
            request.sender_business_info,
            request.sender_business_name,
        )
        html_body = wrap_outreach_plain_as_html(
            body,
            studio_name=sender.get("name") or resolve_from_display_name(
                request.sender_business_info,
                user_name=user.get("name"),
                business_name=request.sender_business_name,
            ),
            studio_email=sender.get("email") or "",
            studio_url=sender.get("url") or "",
            business_name=request.business.name,
            prototype_url=request.figma_prototype_link,
            template_id=template_id,
            logo_url=logo_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "to": recipients[0],
        "recipients": recipients,
        "subject": subject,
        "body": body,
        "html_body": html_body,
        "business_name": request.business.name,
        "language": language,
        "template_id": template_id,
        "design_unlocked": _email_design_allowed(user),
        "from_name": resolve_from_display_name(
            request.sender_business_info,
            user_name=user.get("name"),
            business_name=request.sender_business_name,
        ),
        "from_email": (
            get_user_gmail_status(user).get("email")
            or ""
        ),
        "templates": get_available_email_templates(),
    }


@app.post("/api/email/send")
async def send_email(request: SendEmailRequest, user=Depends(require_user)):
    recipients = request.recipients or merge_emails(
        request.business.contact_emails, request.business.contact_email
    )
    recipients = [e.strip() for e in recipients if e and e.strip()]
    if not recipients:
        raise HTTPException(status_code=400, detail="This business has no contact email.")

    if not request.sender_business_name.strip():
        raise HTTPException(
            status_code=400,
            detail="Add your business name in the sidebar before sending.",
        )

    if not request.sender_business_info.strip():
        raise HTTPException(
            status_code=400,
            detail="Add Your Business Info in the sidebar before sending.",
        )

    if not user_gmail_connected(user):
        raise HTTPException(
            status_code=400,
            detail="Gmail not connected. Open Integrations and click Connect with Google.",
        )

    try:
        template_id, logo_url = _resolve_email_design(
            user, request.template_id, request.logo_url
        )
        html_body: str | None = None
        if request.subject and request.body:
            subject = request.subject.strip()
            body = request.body.strip()
            sender = parse_sender_info(
                request.sender_business_info,
                request.sender_business_name,
            )
            html_body = wrap_outreach_plain_as_html(
                body,
                studio_name=resolve_from_display_name(
                    request.sender_business_info,
                    user_name=user.get("name"),
                    business_name=request.sender_business_name,
                ),
                studio_email=sender.get("email") or "",
                studio_url=sender.get("url") or "",
                business_name=request.business.name,
                prototype_url=request.figma_prototype_link,
                template_id=template_id,
                logo_url=logo_url,
            )
        else:
            subject, body, html_body = generate_send_email(
                request.business,
                sender_info=request.sender_business_info,
                figma_prototype_link=request.figma_prototype_link,
                sender_business_name=request.sender_business_name,
                template_id=template_id,
                logo_url=logo_url,
            )

        if not subject or not body:
            raise HTTPException(status_code=400, detail="Subject and email body are required.")

        from_name = resolve_from_display_name(
            request.sender_business_info,
            user_name=user.get("name"),
            business_name=request.sender_business_name,
        )
        gmail = get_user_gmail_status(user)
        sender_email = (
            gmail.get("email")
            or (user.get("integrations") or {}).get("gmail_email")
            or (user.get("integrations") or {}).get("gmail_smtp_email")
            or ""
        )

        if request.include_unsubscribe_footer:
            body = append_compliance_footer(
                body,
                sender_name=from_name,
                sender_email=sender_email,
            )

        tracking = await record_outreach_send(
            str(user["_id"]),
            business=request.business.model_dump(),
            recipients=recipients,
            subject=subject,
            body=body,
            include_tracking_pixel=request.include_open_tracking,
            html_body=html_body,
        )
        html_body = tracking["html_body"]

        sent_to: list[str] = []
        errors: list[str] = []
        last_result: dict = {}
        for to_email in recipients:
            try:
                last_result = send_email_as_user(
                    user,
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    html_body=html_body,
                    from_name=from_name,
                    reply_to=sender_email or None,
                    include_list_unsubscribe=request.include_unsubscribe_footer,
                )
                sent_to.append(to_email)
            except ValueError as e:
                errors.append(f"{to_email}: {e}")

        if not sent_to:
            raise HTTPException(
                status_code=400,
                detail="Failed to send to any address. " + "; ".join(errors[:3]),
            )

        return {
            **last_result,
            "sent": True,
            "to": sent_to[0],
            "recipients": sent_to,
            "recipient_count": len(sent_to),
            "failed": errors,
            "subject": subject,
            "body": body,
            "deal": tracking["deal"],
            "open_tracking": request.include_open_tracking,
            "from_name": from_name,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/health")
async def health():
    has_sa_file = bool(settings.google_application_credentials)
    has_sa_json = bool(settings.google_service_account_json)
    if has_sa_file or has_sa_json:
        auth_method = "service_account"
    elif settings.google_maps_api_key:
        auth_method = "api_key"
    else:
        auth_method = "none"
    return {
        "status": "ok",
        "credentials_configured": is_google_configured(),
        "auth_method": auth_method,
        "project": settings.google_cloud_project if auth_method == "service_account" else None,
    }
