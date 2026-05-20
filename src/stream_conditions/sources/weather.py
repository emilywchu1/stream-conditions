"""Open-Meteo weather client — current conditions, forecast, and historical archive."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

_WEATHER_VARS = ",".join([
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "cloud_cover",
    "precipitation",
    "wind_speed_10m",
    "wind_direction_10m",
])


@dataclass
class WeatherReading:
    """Instantaneous current weather conditions at a location."""

    latitude: float
    longitude: float
    timestamp: datetime          # UTC
    air_temp_c: float | None
    humidity_pct: float | None
    pressure_hpa: float | None
    cloud_cover_pct: float | None
    precipitation_mm: float | None
    wind_speed_kmh: float | None
    wind_direction_deg: float | None


@dataclass
class HourlyForecast:
    """One hour of forecast or historical weather data."""

    timestamp: datetime          # UTC
    air_temp_c: float | None
    humidity_pct: float | None
    pressure_hpa: float | None
    cloud_cover_pct: float | None
    precipitation_mm: float | None
    wind_speed_kmh: float | None
    wind_direction_deg: float | None


def _is_5xx(exc: BaseException) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code >= 500
    )


class WeatherClient:
    """Synchronous client for the Open-Meteo forecast and archive APIs."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    @retry(
        retry=retry_if_exception(_is_5xx),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.debug("Open-Meteo request url=%s params=%s", url, params)
        response = self._http.get(url, params=params)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    def get_current(self, latitude: float, longitude: float) -> WeatherReading:
        """Return current weather conditions at (latitude, longitude)."""
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "current": _WEATHER_VARS,
            "timezone": "UTC",
        }
        try:
            payload = self._fetch(FORECAST_URL, params)
        except Exception:
            logger.warning(
                "Failed to fetch current weather at (%.4f, %.4f)", latitude, longitude
            )
            raise
        return _parse_current(payload)

    def get_forecast(self, latitude: float, longitude: float) -> list[HourlyForecast]:
        """Return hourly forecasts for the next 48 hours."""
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": _WEATHER_VARS,
            "forecast_days": 2,
            "timezone": "UTC",
        }
        try:
            payload = self._fetch(FORECAST_URL, params)
        except Exception:
            logger.warning(
                "Failed to fetch forecast at (%.4f, %.4f)", latitude, longitude
            )
            raise
        return _parse_hourly(payload)

    def get_historical(
        self,
        latitude: float,
        longitude: float,
        start: date,
        end: date,
    ) -> list[HourlyForecast]:
        """Return hourly weather from the archive API between start and end (inclusive)."""
        params: dict[str, Any] = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": _WEATHER_VARS,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "timezone": "UTC",
        }
        try:
            payload = self._fetch(ARCHIVE_URL, params)
        except Exception:
            logger.warning(
                "Failed to fetch historical weather at (%.4f, %.4f) (%s to %s)",
                latitude, longitude, start, end,
            )
            raise
        return _parse_hourly(payload)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> WeatherClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_current(payload: dict[str, Any]) -> WeatherReading:
    current: dict[str, Any] = payload.get("current", {})
    # Open-Meteo returns naive timestamps when timezone=UTC
    ts = datetime.fromisoformat(current["time"]).replace(tzinfo=timezone.utc)
    return WeatherReading(
        latitude=float(payload["latitude"]),
        longitude=float(payload["longitude"]),
        timestamp=ts,
        air_temp_c=_to_float(current.get("temperature_2m")),
        humidity_pct=_to_float(current.get("relative_humidity_2m")),
        pressure_hpa=_to_float(current.get("surface_pressure")),
        cloud_cover_pct=_to_float(current.get("cloud_cover")),
        precipitation_mm=_to_float(current.get("precipitation")),
        wind_speed_kmh=_to_float(current.get("wind_speed_10m")),
        wind_direction_deg=_to_float(current.get("wind_direction_10m")),
    )


def _parse_hourly(payload: dict[str, Any]) -> list[HourlyForecast]:
    hourly: dict[str, Any] = payload.get("hourly", {})
    times: list[str] = hourly.get("time", [])
    n = len(times)

    def col(key: str) -> list[Any]:
        return hourly.get(key, [None] * n)

    temps = col("temperature_2m")
    humidity = col("relative_humidity_2m")
    pressure = col("surface_pressure")
    cloud = col("cloud_cover")
    precip = col("precipitation")
    wind_speed = col("wind_speed_10m")
    wind_dir = col("wind_direction_10m")

    result: list[HourlyForecast] = []
    for i, t in enumerate(times):
        try:
            ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("Unparseable datetime from Open-Meteo: %s", t)
            continue
        result.append(
            HourlyForecast(
                timestamp=ts,
                air_temp_c=_to_float(temps[i] if i < len(temps) else None),
                humidity_pct=_to_float(humidity[i] if i < len(humidity) else None),
                pressure_hpa=_to_float(pressure[i] if i < len(pressure) else None),
                cloud_cover_pct=_to_float(cloud[i] if i < len(cloud) else None),
                precipitation_mm=_to_float(precip[i] if i < len(precip) else None),
                wind_speed_kmh=_to_float(wind_speed[i] if i < len(wind_speed) else None),
                wind_direction_deg=_to_float(wind_dir[i] if i < len(wind_dir) else None),
            )
        )
    return result
