"""Open-Meteo weather client — forecast and historical archive, no API key required."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

_HOURLY_VARS = "temperature_2m,precipitation,windspeed_10m,cloudcover"


@dataclass
class WeatherReading:
    """Hourly weather observation or forecast at a gauge location."""

    datetime_utc: datetime
    air_temp_c: float | None
    precipitation_mm: float | None
    wind_speed_ms: float | None
    cloud_cover_pct: float | None


@dataclass
class OpenMeteoClient:
    """Client for Open-Meteo forecast and historical-archive APIs."""

    timeout: float = 30.0
    _http: httpx.AsyncClient = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=self.timeout)

    async def fetch_forecast(
        self,
        latitude: float,
        longitude: float,
        days: int = 7,
    ) -> list[WeatherReading]:
        """Fetch hourly forecast data for *days* ahead."""
        response = await self._http.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": _HOURLY_VARS,
                "forecast_days": days,
                "timezone": "UTC",
            },
        )
        response.raise_for_status()
        return _parse_hourly(response.json())

    async def fetch_historical(
        self,
        latitude: float,
        longitude: float,
        days: int = 30,
    ) -> list[WeatherReading]:
        """Fetch hourly historical weather for the past *days* days."""
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        response = await self._http.get(
            ARCHIVE_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": _HOURLY_VARS,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "timezone": "UTC",
            },
        )
        response.raise_for_status()
        return _parse_hourly(response.json())

    async def aclose(self) -> None:
        await self._http.aclose()


def _parse_hourly(payload: dict[str, Any]) -> list[WeatherReading]:
    hourly: dict[str, Any] = payload.get("hourly", {})
    times: list[str] = hourly.get("time", [])
    temps: list[float | None] = hourly.get("temperature_2m", [])
    precip: list[float | None] = hourly.get("precipitation", [])
    wind: list[float | None] = hourly.get("windspeed_10m", [])
    cloud: list[float | None] = hourly.get("cloudcover", [])

    def _get(lst: list[float | None], i: int) -> float | None:
        try:
            v = lst[i]
            return float(v) if v is not None else None
        except (IndexError, TypeError, ValueError):
            return None

    return [
        WeatherReading(
            datetime_utc=datetime.fromisoformat(t),
            air_temp_c=_get(temps, i),
            precipitation_mm=_get(precip, i),
            wind_speed_ms=_get(wind, i),
            cloud_cover_pct=_get(cloud, i),
        )
        for i, t in enumerate(times)
    ]
