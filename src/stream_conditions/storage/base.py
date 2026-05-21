"""Abstract Protocol classes for the storage layer.

All repo methods are async. The concrete SQLite implementation lives in
storage.sqlite; swap in a Postgres backend by implementing these protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from stream_conditions.sources.gauge_registry import Gauge

__all__ = [
    "Gauge",
    "Snapshot",
    "Session",
    "Prediction",
    "GaugeRepo",
    "SnapshotRepo",
    "SessionRepo",
    "PredictionRepo",
]


# ── Domain dataclasses ─────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """One fetched moment: stream conditions joined with weather."""

    site_id: str
    fetched_at: datetime
    discharge_cfs: float | None = None
    gauge_height_ft: float | None = None
    water_temp_c: float | None = None
    air_temp_c: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    cloud_cover_pct: float | None = None
    precip_mm: float | None = None
    wind_speed_kmh: float | None = None
    wind_dir_deg: float | None = None
    id: int | None = field(default=None, compare=False)


@dataclass
class Session:
    """A user-logged fishing outing."""

    site_id: str
    started_at: datetime
    ended_at: datetime | None = None
    hatch_order: str | None = None     # 'ephemeroptera', 'trichoptera', …
    hatch_stage: str | None = None     # 'emerger', 'dun', 'spinner', 'nymph', 'pupa'
    hatch_intensity: int | None = None  # 0 none · 1 sparse · 2 moderate · 3 heavy
    fish_count: int | None = None
    fish_species: str | None = None
    notes: str = ""
    id: int | None = field(default=None, compare=False)


@dataclass
class Prediction:
    """A model prediction logged for future scoring."""

    site_id: str
    generated_at: datetime
    target_window_start: datetime
    target_window_end: datetime
    score: float
    model_version: str
    features_json: str = "{}"
    actual_outcome: int | None = None  # backfilled after a real session
    id: int | None = field(default=None, compare=False)


# ── Abstract repository protocols ──────────────────────────────────────────────

@runtime_checkable
class GaugeRepo(Protocol):
    async def upsert(self, gauge: Gauge) -> None: ...
    async def get(self, site_id: str) -> Gauge | None: ...
    async def list(self) -> list[Gauge]: ...
    async def delete(self, site_id: str) -> None: ...


@runtime_checkable
class SnapshotRepo(Protocol):
    async def insert(self, snapshot: Snapshot) -> int: ...
    async def get_range(
        self, site_id: str, since: datetime, until: datetime
    ) -> list[Snapshot]: ...
    async def latest(self, site_id: str) -> Snapshot | None: ...


@runtime_checkable
class SessionRepo(Protocol):
    async def insert(self, session: Session) -> int: ...
    async def get(self, session_id: int) -> Session | None: ...
    async def list_for_site(
        self, site_id: str, since: datetime | None = None
    ) -> list[Session]: ...
    async def update(self, session: Session) -> None: ...
    async def delete(self, session_id: int) -> None: ...


@runtime_checkable
class PredictionRepo(Protocol):
    async def insert(self, prediction: Prediction) -> int: ...
    async def get(self, prediction_id: int) -> Prediction | None: ...
    async def list_for_site(self, site_id: str) -> list[Prediction]: ...
    async def backfill_outcome(self, prediction_id: int, actual_outcome: int) -> None: ...
