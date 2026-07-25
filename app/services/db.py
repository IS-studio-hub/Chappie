from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        if not settings.mongodb_uri:
            raise RuntimeError("MONGODB_URI is not configured")
        _client = AsyncIOMotorClient(settings.mongodb_uri)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongodb_db_name]


async def connect_db() -> None:
    client = get_client()
    await client.admin.command("ping")
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.billing_events.create_index([("user_id", 1), ("created_at", -1)])
    await db.pending_signups.create_index("email", unique=True)
    await db.pending_signups.create_index("token", unique=True)
    await db.pending_signups.create_index("expires_at", expireAfterSeconds=0)
    await db.outreach_deals.create_index([("user_id", 1), ("updated_at", -1)])
    await db.outreach_deals.create_index([("user_id", 1), ("place_id", 1)])
    # Unique open_token must be sparse and must not index null (Mongo unique + null = one row).
    try:
        await db.outreach_deals.drop_index("open_token_1")
    except Exception:
        pass
    # Clear legacy null tokens so unique sparse rebuild can succeed
    try:
        await db.outreach_deals.update_many(
            {"open_token": None},
            {"$unset": {"open_token": ""}},
        )
    except Exception:
        pass
    await db.outreach_deals.create_index("open_token", unique=True, sparse=True)
    # Brand books are per-user so accounts never share generated brand systems
    try:
        await db.brand_books.drop_index("place_id_1")
    except Exception:
        pass
    # Remove legacy global cache rows (no user_id) to prevent cross-account reuse
    try:
        await db.brand_books.delete_many({"user_id": {"$exists": False}})
        await db.brand_books.delete_many({"user_id": None})
    except Exception:
        pass
    await db.brand_books.create_index(
        [("user_id", 1), ("place_id", 1)],
        unique=True,
    )
    await db.favorites.create_index([("user_id", 1), ("created_at", -1)])
    await db.favorites.create_index(
        [("user_id", 1), ("favorite_key", 1)],
        unique=True,
    )
    await db.favorites.create_index([("user_id", 1), ("place_id", 1)])
    await db.search_jobs.create_index("job_id", unique=True)
    await db.search_jobs.create_index([("user_id", 1), ("updated_at", -1)])
    await db.search_jobs.create_index([("user_id", 1), ("status", 1), ("updated_at", -1)])
    await db.search_jobs.create_index([("user_id", 1), ("completed_at", -1)])


async def close_db() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
