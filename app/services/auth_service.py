from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt
from bson import ObjectId
from fastapi import Cookie, HTTPException, Response

from app.config import settings
from app.services.db import get_db
from app.services.plans import get_plan

COOKIE_NAME = "chappie_session"
TOKEN_DAYS = 30


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_token(user_id),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=TOKEN_DAYS * 24 * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def serialize_user(doc: dict[str, Any]) -> dict[str, Any]:
    plan = get_plan(doc.get("plan"))
    credit = float(doc.get("credit_balance_cad") or 0)
    searches_used = int(doc.get("searches_used_period") or 0)
    can_search = _can_search(doc, plan)
    return {
        "id": str(doc["_id"]),
        "email": doc["email"],
        "name": doc.get("name") or "",
        "plan": plan.id,
        "plan_name": plan.name,
        "credit_balance_cad": round(credit, 2),
        "search_cost_cad": plan.search_cost_cad,
        "searches_used": searches_used,
        "max_searches": plan.max_searches,
        "searches_remaining": max(0, plan.max_searches - searches_used),
        "max_results": plan.max_results,
        "period_start": doc.get("period_start").isoformat() if doc.get("period_start") else None,
        "period_end": doc.get("period_end").isoformat() if doc.get("period_end") else None,
        "can_search": can_search,
        "exhausted_action": plan.exhausted_action if not can_search else None,
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
    }


def _can_search(doc: dict[str, Any], plan) -> bool:
    searches_used = int(doc.get("searches_used_period") or 0)
    if searches_used >= plan.max_searches:
        return False
    if plan.id == "free":
        return True
    credit = float(doc.get("credit_balance_cad") or 0)
    return credit + 1e-9 >= plan.search_cost_cad


async def create_user(email: str, password: str, name: str = "") -> dict[str, Any]:
    return await create_user_from_hash(
        email=email,
        password_hash=hash_password(password),
        name=name,
    )


async def create_user_from_hash(
    email: str,
    password_hash: str,
    name: str = "",
) -> dict[str, Any]:
    db = get_db()
    email_norm = email.strip().lower()
    existing = await db.users.find_one({"email": email_norm})
    if existing:
        raise ValueError("An account with this email already exists.")

    now = datetime.now(timezone.utc)
    doc = {
        "email": email_norm,
        "password_hash": password_hash,
        "name": name.strip(),
        "plan": "free",
        "credit_balance_cad": 0.0,
        "searches_used_period": 0,
        "period_start": now,
        "period_end": None,
        "stripe_customer_id": None,
        "stripe_subscription_id": None,
        "email_verified": True,
        "integrations": {},
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def authenticate_user(email: str, password: str) -> dict[str, Any] | None:
    db = get_db()
    doc = await db.users.find_one({"email": email.strip().lower()})
    if not doc or not verify_password(password, doc.get("password_hash", "")):
        return None
    return doc


async def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    if not ObjectId.is_valid(user_id):
        return None
    return await get_db().users.find_one({"_id": ObjectId(user_id)})


async def get_current_user_optional(
    chappie_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> dict[str, Any] | None:
    if not chappie_session:
        return None
    user_id = decode_token(chappie_session)
    if not user_id:
        return None
    return await get_user_by_id(user_id)


async def require_user(
    chappie_session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> dict[str, Any]:
    user = await get_current_user_optional(chappie_session)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user
