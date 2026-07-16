#!/usr/bin/env python3
"""Entry point to run Chappie Business Finder."""
import os

import uvicorn

from app.config import settings

if __name__ == "__main__":
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=port,
        reload=os.environ.get("CHAPPIE_RELOAD", "").lower() in ("1", "true", "yes"),
    )
