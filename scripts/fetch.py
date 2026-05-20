"""Scheduled data fetcher — invoke via cron, Task Scheduler, or GitHub Actions.

Usage:
    python scripts/fetch.py

Environment:
    GAUGE_IDS   Comma-separated USGS site numbers (required)
    DB_PATH     Path to SQLite database (default: data/stream_conditions.db)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Support running from the repo root without `poetry run`
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("stream_conditions.fetch")

from stream_conditions.sources.usgs import USGSClient
from stream_conditions.storage.database import SQLiteRepository


async def main() -> None:
    raw_ids = os.environ.get("GAUGE_IDS", "")
    gauge_ids = [g.strip() for g in raw_ids.split(",") if g.strip()]
    if not gauge_ids:
        logger.error("GAUGE_IDS is not set — nothing to fetch. Exiting.")
        raise SystemExit(1)

    db_path = os.environ.get("DB_PATH", "data/stream_conditions.db")
    repo = SQLiteRepository(db_path)
    usgs = USGSClient()

    for gauge_id in gauge_ids:
        logger.info("Fetching USGS data for gauge %s", gauge_id)
        readings = await usgs.fetch_recent(gauge_id, days=7)
        saved = repo.save_gauge_readings(readings)
        logger.info("  → %d readings saved for gauge %s", saved, gauge_id)

    await usgs.aclose()
    logger.info("Fetch complete.")


if __name__ == "__main__":
    asyncio.run(main())
