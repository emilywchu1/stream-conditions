#!/usr/bin/env python
"""Scheduled snapshot fetcher — run every 15 minutes via Windows Task Scheduler.

Usage:
    python scripts/fetch_snapshot.py

Environment (via .env or system):
    DB_PATH   Path to SQLite database (default: data/stream_conditions.db)

Exit codes:
    0   All gauges succeeded (or were skipped as fresh)
    1   One or more gauges failed
    2   No gauges are registered in the database
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow running from repo root without `poetry run`.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("stream_conditions.fetch_snapshot")


async def main() -> int:
    from stream_conditions.ingest import DEFAULT_DB, fetch_all

    db_path = Path(os.environ.get("DB_PATH", str(DEFAULT_DB)))
    logger.info("fetch_snapshot starting — db=%s", db_path)

    results = await fetch_all(db_path)

    if not results:
        logger.error("No gauges registered. Add gauges first via `stream-conditions register`.")
        return 2

    failed = [sid for sid, ok in results.items() if not ok]
    if failed:
        logger.error("Failed gauges: %s", ", ".join(failed))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
