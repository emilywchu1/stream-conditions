"""Gauge registry — fetch USGS site metadata and persist to a GaugeStore.

Storage is injected via the GaugeStore protocol. The module-level
_default_store tries to import a configured SQLiteGaugeStore from
storage.gauges; until that module is built it falls back to InMemoryGaugeStore.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

SITE_SERVICE_URL = "https://waterservices.usgs.gov/nwis/site/"


# ── Domain model ───────────────────────────────────────────────────────────────

@dataclass
class Gauge:
    """A USGS stream gauge with location metadata and optional user notes."""

    site_id: str
    name: str
    latitude: float
    longitude: float
    state_cd: str
    huc_cd: str
    drain_area_sqmi: float | None
    notes: str = ""


# ── GaugeStore protocol ────────────────────────────────────────────────────────

@runtime_checkable
class GaugeStore(Protocol):
    """Interface for gauge persistence. storage.gauges will provide SQLiteGaugeStore."""

    def save(self, gauge: Gauge) -> None: ...
    def get(self, site_id: str) -> Gauge | None: ...
    def list(self) -> list[Gauge]: ...


class InMemoryGaugeStore:
    """In-memory GaugeStore used until storage.gauges is wired up."""

    def __init__(self) -> None:
        self._data: dict[str, Gauge] = {}

    def save(self, gauge: Gauge) -> None:
        self._data[gauge.site_id] = gauge

    def get(self, site_id: str) -> Gauge | None:
        return self._data.get(site_id)

    def list(self) -> list[Gauge]:
        return list(self._data.values())


# ── Module-level default store ─────────────────────────────────────────────────

try:
    from stream_conditions.storage.gauges import gauge_store as _default_store
except ImportError:
    _default_store: GaugeStore = InMemoryGaugeStore()


def set_default_store(store: GaugeStore) -> None:
    """Replace the module-level default store (called by storage.gauges on init)."""
    global _default_store
    _default_store = store


# ── USGS Site Service fetch ────────────────────────────────────────────────────

def _is_5xx(exc: BaseException) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code >= 500
    )


@retry(
    retry=retry_if_exception(_is_5xx),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _fetch_site_rdb(site_id: str) -> str:
    """Return raw RDB text from the USGS Site Service for *site_id*."""
    logger.debug("USGS Site Service request for site %s", site_id)
    with httpx.Client(timeout=10.0, follow_redirects=True) as http:
        response = http.get(
            SITE_SERVICE_URL,
            params={"sites": site_id, "format": "rdb", "siteOutput": "expanded"},
        )
        response.raise_for_status()
    return response.text


def _parse_rdb_row(text: str) -> dict[str, str]:
    """Parse the first data row from an RDB response into a flat dict."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("#") and ln.strip()]
    if len(lines) < 3:
        raise ValueError(f"RDB response has too few non-comment lines ({len(lines)})")
    headers = lines[0].split("\t")
    # lines[1] is the format-specifier row (e.g. "5s\t15s\t...") — skip it
    data = lines[2].split("\t")
    return dict(zip(headers, data))


def _gauge_from_rdb(row: dict[str, str], notes: str) -> Gauge:
    drain = row.get("drain_area_va", "").strip()
    return Gauge(
        site_id=row["site_no"].strip(),
        name=row["station_nm"].strip().title(),
        latitude=float(row["dec_lat_va"]),
        longitude=float(row["dec_long_va"]),
        state_cd=row.get("state_cd", "").strip(),
        huc_cd=row.get("huc_cd", "").strip(),
        drain_area_sqmi=float(drain) if drain else None,
        notes=notes,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def register_gauge(
    site_id: str,
    notes: str = "",
    *,
    store: GaugeStore | None = None,
) -> Gauge:
    """Fetch metadata from USGS, persist to *store*, and return the Gauge."""
    _store = store if store is not None else _default_store
    try:
        rdb_text = _fetch_site_rdb(site_id)
    except Exception:
        logger.warning("Failed to fetch USGS site metadata for %s", site_id)
        raise
    row = _parse_rdb_row(rdb_text)
    gauge = _gauge_from_rdb(row, notes)
    _store.save(gauge)
    logger.debug("Registered gauge %s (%s)", gauge.site_id, gauge.name)
    return gauge


def get_gauge(
    site_id: str,
    *,
    store: GaugeStore | None = None,
) -> Gauge:
    """Return the Gauge for *site_id*, fetching from USGS if not cached."""
    _store = store if store is not None else _default_store
    cached = _store.get(site_id)
    if cached is not None:
        return cached
    return register_gauge(site_id, store=_store)


def list_gauges(*, store: GaugeStore | None = None) -> list[Gauge]:
    """Return all registered gauges from *store*."""
    _store = store if store is not None else _default_store
    return _store.list()
