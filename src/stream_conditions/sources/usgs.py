"""USGS National Water Information System (NWIS) instantaneous-values client."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NWIS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"

PARAM_DISCHARGE = "00060"   # Discharge, cubic feet per second
PARAM_STAGE = "00065"        # Gage height, feet
PARAM_WATER_TEMP = "00010"   # Water temperature, °C

_MISSING_SENTINEL = "-999999"


@dataclass
class GaugeReading:
    """A single instantaneous reading from a USGS stream gauge."""

    site_no: str
    datetime_utc: datetime
    discharge_cfs: float | None
    stage_ft: float | None
    water_temp_c: float | None


@dataclass
class USGSClient:
    """Async client for the USGS NWIS Instantaneous Values REST service."""

    timeout: float = 30.0
    _http: httpx.AsyncClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=self.timeout)

    async def fetch_recent(
        self,
        site_no: str,
        days: int = 7,
        param_codes: list[str] | None = None,
    ) -> list[GaugeReading]:
        """Fetch instantaneous values for *site_no* over the past *days* days."""
        if param_codes is None:
            param_codes = [PARAM_DISCHARGE, PARAM_STAGE, PARAM_WATER_TEMP]

        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        response = await self._http.get(
            NWIS_IV_URL,
            params={
                "sites": site_no,
                "parameterCd": ",".join(param_codes),
                "startDT": start,
                "format": "json",
            },
        )
        response.raise_for_status()
        return self._parse(site_no, response.json())

    def _parse(self, site_no: str, payload: dict[str, Any]) -> list[GaugeReading]:
        """Flatten NWIS timeSeries JSON into GaugeReading objects."""
        time_series: list[dict[str, Any]] = payload.get("value", {}).get("timeSeries", [])

        # Accumulate values keyed by ISO datetime string so we can merge parameters.
        by_dt: dict[str, dict[str, Any]] = {}
        for series in time_series:
            param = series["variable"]["variableCode"][0]["value"]
            values: list[dict[str, Any]] = series.get("values", [{}])[0].get("value", [])
            for entry in values:
                dt_str: str = entry["dateTime"]
                raw: str = entry["value"]
                by_dt.setdefault(dt_str, {})
                by_dt[dt_str][param] = None if raw == _MISSING_SENTINEL else raw

        readings: list[GaugeReading] = []
        for dt_str, params in sorted(by_dt.items()):
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except ValueError:
                logger.warning("Unparseable datetime from NWIS: %s", dt_str)
                continue
            readings.append(
                GaugeReading(
                    site_no=site_no,
                    datetime_utc=dt,
                    discharge_cfs=_to_float(params.get(PARAM_DISCHARGE)),
                    stage_ft=_to_float(params.get(PARAM_STAGE)),
                    water_temp_c=_to_float(params.get(PARAM_WATER_TEMP)),
                )
            )
        return readings

    async def aclose(self) -> None:
        await self._http.aclose()


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
