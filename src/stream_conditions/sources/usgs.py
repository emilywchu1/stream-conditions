"""USGS National Water Information System (NWIS) instantaneous-values client."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

NWIS_IV_URL = "https://waterservices.usgs.gov/nwis/iv/"

PARAM_DISCHARGE = "00060"     # Discharge, ft³/s
PARAM_GAUGE_HEIGHT = "00065"  # Gage height, ft
PARAM_WATER_TEMP = "00010"    # Water temperature, °C

_KNOWN_PARAMS = {PARAM_DISCHARGE, PARAM_GAUGE_HEIGHT, PARAM_WATER_TEMP}
_MISSING_SENTINEL = "-999999"


@dataclass
class USGSReading:
    """A single instantaneous reading from a USGS stream gauge."""

    site_id: str
    timestamp: datetime          # UTC
    discharge_cfs: float | None
    gauge_height_ft: float | None
    water_temp_c: float | None
    raw: dict[str, Any] = field(default_factory=dict)


def _is_5xx(exc: BaseException) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code >= 500
    )


class USGSClient:
    """Synchronous client for the USGS NWIS Instantaneous Values REST service."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    @retry(
        retry=retry_if_exception(_is_5xx),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch(self, params: dict[str, str]) -> dict[str, Any]:
        logger.debug("USGS NWIS request params=%s", params)
        response = self._http.get(NWIS_IV_URL, params=params)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    def get_current(self, site_id: str) -> USGSReading:
        """Return the most recent reading for *site_id*."""
        params: dict[str, str] = {
            "sites": site_id,
            "parameterCd": ",".join([PARAM_DISCHARGE, PARAM_GAUGE_HEIGHT, PARAM_WATER_TEMP]),
            "period": "PT2H",
            "format": "json",
        }
        try:
            payload = self._fetch(params)
        except Exception:
            logger.warning("Failed to fetch current data for site %s", site_id)
            raise
        readings = self._parse(site_id, payload)
        if not readings:
            raise ValueError(f"No data returned for site {site_id}")
        return readings[-1]

    def get_historical(
        self,
        site_id: str,
        start: date,
        end: date,
    ) -> list[USGSReading]:
        """Return all readings for *site_id* between *start* and *end* (inclusive)."""
        params: dict[str, str] = {
            "sites": site_id,
            "parameterCd": ",".join([PARAM_DISCHARGE, PARAM_GAUGE_HEIGHT, PARAM_WATER_TEMP]),
            "startDT": start.isoformat(),
            "endDT": end.isoformat(),
            "format": "json",
        }
        try:
            payload = self._fetch(params)
        except Exception:
            logger.warning(
                "Failed to fetch historical data for site %s (%s to %s)",
                site_id, start, end,
            )
            raise
        return self._parse(site_id, payload)

    def _parse(self, site_id: str, payload: dict[str, Any]) -> list[USGSReading]:
        time_series: list[dict[str, Any]] = (
            payload.get("value", {}).get("timeSeries", [])
        )

        by_dt: dict[str, dict[str, str | None]] = {}
        for series in time_series:
            try:
                param: str = series["variable"]["variableCode"][0]["value"]
            except (KeyError, IndexError):
                logger.warning("Unexpected timeSeries structure; skipping entry")
                continue
            entries: list[dict[str, Any]] = (
                series.get("values", [{}])[0].get("value", [])
            )
            for entry in entries:
                dt_str: str = entry["dateTime"]
                raw_val: str = entry["value"]
                by_dt.setdefault(dt_str, {})
                by_dt[dt_str][param] = (
                    None if raw_val == _MISSING_SENTINEL else raw_val
                )

        readings: list[USGSReading] = []
        for dt_str, params in sorted(by_dt.items()):
            try:
                dt = datetime.fromisoformat(dt_str).astimezone(timezone.utc)
            except ValueError:
                logger.warning("Unparseable datetime from NWIS: %s", dt_str)
                continue

            extra = {k: v for k, v in params.items() if k not in _KNOWN_PARAMS}
            readings.append(
                USGSReading(
                    site_id=site_id,
                    timestamp=dt,
                    discharge_cfs=_to_float(params.get(PARAM_DISCHARGE)),
                    gauge_height_ft=_to_float(params.get(PARAM_GAUGE_HEIGHT)),
                    water_temp_c=_to_float(params.get(PARAM_WATER_TEMP)),
                    raw=extra,
                )
            )
        return readings

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> USGSClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
