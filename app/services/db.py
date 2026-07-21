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
    await db.outreach_deals.create_index("open_token", unique=True, sparse=True)
    await db.brand_books.create_index("place_id", unique=True)
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
