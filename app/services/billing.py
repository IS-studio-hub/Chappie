from datetime import datetime, timedelta, timezone
from typing import Any

import stripe
from bson import ObjectId

from app.config import settings
from app.services.db import get_db
from app.services.plans import PLANS, get_plan, is_upgrade

PERIOD_DAYS = 30


def _configure_stripe() -> None:
    if not settings.stripe_secret_key:
        raise ValueError(
            "Stripe is not configured. Add STRIPE_SECRET_KEY to your .env file."
        )
    stripe.api_key = settings.stripe_secret_key


async def log_billing_event(
    user_id: str,
    event_type: str,
    amount_cad: float,
    description: str,
    meta: dict | None = None,
) -> None:
    await get_db().billing_events.insert_one({
        "user_id": user_id,
        "type": event_type,
        "amount_cad": round(amount_cad, 2),
        "description": description,
        "meta": meta or {},
        "created_at": datetime.now(timezone.utc),
    })


async def activate_plan(user_id: str, plan_id: str, *, stripe_subscription_id: str | None = None) -> dict[str, Any]:
    plan = get_plan(plan_id)
    now = datetime.now(timezone.utc)
    period_end = None if plan.id == "free" else now + timedelta(days=PERIOD_DAYS)

    update: dict[str, Any] = {
        "plan": plan.id,
        "credit_balance_cad": float(plan.price_cad),
        "searches_used_period": 0,
        "period_start": now,
        "period_end": period_end,
        "updated_at": now,
    }
    if stripe_subscription_id is not None:
        update["stripe_subscription_id"] = stripe_subscription_id

    db = get_db()
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update})
    await log_billing_event(
        user_id,
        "plan_change",
        plan.price_cad,
        f"Activated {plan.name} plan — billing period started",
        {"plan": plan.id},
    )
    if plan.price_cad > 0:
        await log_billing_event(
            user_id,
            "credit_grant",
            plan.price_cad,
            f"Monthly credits granted for {plan.name}",
            {"plan": plan.id},
        )
    return await db.users.find_one({"_id": ObjectId(user_id)})


async def ensure_stripe_customer(user: dict[str, Any]) -> str:
    _configure_stripe()
    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]

    customer = stripe.Customer.create(
        email=user["email"],
        name=user.get("name") or None,
        metadata={"user_id": str(user["_id"])},
    )
    await get_db().users.update_one(
        {"_id": user["_id"]},
        {"$set": {"stripe_customer_id": customer.id, "updated_at": datetime.now(timezone.utc)}},
    )
    return customer.id


def _price_id_for_plan(plan_id: str) -> str | None:
    mapping = {
        "small": settings.stripe_price_small,
        "mid": settings.stripe_price_mid,
        "large": settings.stripe_price_large,
    }
    return mapping.get(plan_id) or None


async def create_checkout_session(user: dict[str, Any], plan_id: str) -> str:
    _configure_stripe()
    plan = get_plan(plan_id)
    if plan.id == "free":
        raise ValueError("Free plan does not require payment.")

    current = user.get("plan") or "free"
    if PLAN_ORDER_INDEX(current) > PLAN_ORDER_INDEX(plan.id):
        raise ValueError("Downgrades are not supported here. Contact IS Studio for help.")

    # Same plan: only allow repurchase when credits/searches are exhausted
    if current == plan.id:
        searches_used = int(user.get("searches_used_period") or 0)
        credit = float(user.get("credit_balance_cad") or 0)
        still_active = searches_used < plan.max_searches and credit >= plan.search_cost_cad
        if still_active:
            raise ValueError("You are already on this plan with remaining search credits.")
    elif not is_upgrade(current, plan.id):
        raise ValueError("Invalid plan selection.")

    customer_id = await ensure_stripe_customer(user)
    base = settings.app_base_url.rstrip("/")

    price_id = _price_id_for_plan(plan.id)
    if price_id:
        line_items = [{"price": price_id, "quantity": 1}]
    else:
        # Fallback if price IDs are not configured
        line_items = [{
            "price_data": {
                "currency": "cad",
                "unit_amount": int(round(plan.price_cad * 100)),
                "recurring": {"interval": "month"},
                "product_data": {
                    "name": f"CH4PP!3 {plan.name}",
                    "description": (
                        f"{plan.max_searches} searches / month, "
                        f"up to {plan.max_results} businesses per search"
                    ),
                },
            },
            "quantity": 1,
        }]

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        success_url=f"{base}/account?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/account?checkout=cancelled",
        line_items=line_items,
        metadata={
            "user_id": str(user["_id"]),
            "plan": plan.id,
        },
        subscription_data={
            "metadata": {
                "user_id": str(user["_id"]),
                "plan": plan.id,
            },
        },
    )
    return session.url


def PLAN_ORDER_INDEX(plan_id: str | None) -> int:
    order = ["free", "small", "mid", "large"]
    try:
        return order.index(plan_id or "free")
    except ValueError:
        return 0


async def consume_search_credit(user: dict[str, Any]) -> dict[str, Any]:
    """Deduct one search from the user's plan. Raises ValueError if not allowed."""
    plan = get_plan(user.get("plan"))
    searches_used = int(user.get("searches_used_period") or 0)
    credit = float(user.get("credit_balance_cad") or 0)

    if searches_used >= plan.max_searches:
        raise ValueError(_exhausted_message(plan))

    if plan.id != "free" and credit < plan.search_cost_cad:
        raise ValueError(_exhausted_message(plan))

    cost = 0.0 if plan.id == "free" else plan.search_cost_cad
    new_credit = round(credit - cost, 2) if plan.id != "free" else credit
    new_used = searches_used + 1

    db = get_db()
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "credit_balance_cad": new_credit,
                "searches_used_period": new_used,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    await log_billing_event(
        str(user["_id"]),
        "search",
        -cost,
        f"Search on {plan.name} plan",
        {
            "plan": plan.id,
            "search_cost_cad": cost,
            "searches_used": new_used,
            "credit_remaining": new_credit,
        },
    )
    user["credit_balance_cad"] = new_credit
    user["searches_used_period"] = new_used
    return user


def _exhausted_message(plan) -> str:
    if plan.exhausted_action == "contact":
        return (
            "You've used your Large Biz search budget for this period. "
            "Contact IS Studio for a special offer."
        )
    return (
        "You've used your search budget for this period. "
        "Upgrade your plan to continue searching."
    )


async def handle_checkout_completed(session: dict[str, Any]) -> None:
    meta = session.get("metadata") or {}
    user_id = meta.get("user_id")
    plan_id = meta.get("plan")
    if not user_id or not plan_id:
        return

    subscription_id = session.get("subscription")
    user = await get_db().users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return

    # Cancel previous subscription when upgrading
    old_sub = user.get("stripe_subscription_id")
    if old_sub and subscription_id and old_sub != subscription_id:
        try:
            _configure_stripe()
            stripe.Subscription.cancel(old_sub)
        except Exception:
            pass

    amount = (session.get("amount_total") or 0) / 100.0
    await log_billing_event(
        user_id,
        "payment",
        amount,
        f"Stripe payment for {get_plan(plan_id).name}",
        {"session_id": session.get("id"), "plan": plan_id},
    )
    await activate_plan(user_id, plan_id, stripe_subscription_id=subscription_id)


async def handle_invoice_paid(invoice: dict[str, Any]) -> None:
    """Renew monthly credits when Stripe renews the subscription."""
    subscription_id = invoice.get("subscription")
    if not subscription_id:
        return
    # Skip the first invoice — checkout.session.completed already activated
    billing_reason = invoice.get("billing_reason")
    if billing_reason == "subscription_create":
        return

    db = get_db()
    user = await db.users.find_one({"stripe_subscription_id": subscription_id})
    if not user:
        return

    plan = get_plan(user.get("plan"))
    now = datetime.now(timezone.utc)
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "credit_balance_cad": float(plan.price_cad),
                "searches_used_period": 0,
                "period_start": now,
                "period_end": now + timedelta(days=PERIOD_DAYS),
                "updated_at": now,
            }
        },
    )
    amount = (invoice.get("amount_paid") or 0) / 100.0
    await log_billing_event(
        str(user["_id"]),
        "payment",
        amount,
        f"Monthly renewal — {plan.name}",
        {"invoice_id": invoice.get("id"), "plan": plan.id},
    )
    await log_billing_event(
        str(user["_id"]),
        "credit_grant",
        plan.price_cad,
        f"Monthly credits renewed for {plan.name}",
        {"plan": plan.id},
    )


async def get_billing_report(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    cursor = get_db().billing_events.find(
        {"user_id": user_id}
    ).sort("created_at", -1).limit(limit)
    events = []
    async for doc in cursor:
        events.append({
            "id": str(doc["_id"]),
            "type": doc.get("type"),
            "amount_cad": doc.get("amount_cad"),
            "description": doc.get("description"),
            "meta": doc.get("meta") or {},
            "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
        })
    return events


async def get_usage_summary(user: dict[str, Any]) -> dict[str, Any]:
    plan = get_plan(user.get("plan"))
    user_id = str(user["_id"])
    events = await get_billing_report(user_id, limit=500)
    search_spend = sum(
        abs(e["amount_cad"]) for e in events if e["type"] == "search" and e["amount_cad"] < 0
    )
    payments = sum(e["amount_cad"] for e in events if e["type"] == "payment" and e["amount_cad"] > 0)
    return {
        "plan": plan.id,
        "plan_name": plan.name,
        "price_cad": plan.price_cad,
        "credit_balance_cad": round(float(user.get("credit_balance_cad") or 0), 2),
        "search_cost_cad": plan.search_cost_cad,
        "searches_used": int(user.get("searches_used_period") or 0),
        "max_searches": plan.max_searches,
        "max_results": plan.max_results,
        "period_start": user.get("period_start").isoformat() if user.get("period_start") else None,
        "period_end": user.get("period_end").isoformat() if user.get("period_end") else None,
        "total_search_spend_cad": round(search_spend, 2),
        "total_payments_cad": round(payments, 2),
        "can_search": (
            int(user.get("searches_used_period") or 0) < plan.max_searches
            and (plan.id == "free" or float(user.get("credit_balance_cad") or 0) >= plan.search_cost_cad)
        ),
        "exhausted_action": plan.exhausted_action,
        "plans": [
            {
                "id": p.id,
                "name": p.name,
                "price_cad": p.price_cad,
                "max_searches": p.max_searches,
                "max_results": p.max_results,
                "search_cost_cad": p.search_cost_cad,
                "exhausted_action": p.exhausted_action,
            }
            for p in PLANS.values()
        ],
        "studio_email": settings.studio_email,
        "studio_url": settings.studio_url,
        "studio_name": settings.studio_name,
    }
