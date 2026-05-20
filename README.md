# Stream Conditions

> Real-time hydrological ingestion and ML-powered fly-fishing window prediction.

*Full write-up coming — project in active development.*

## Quick Start

```bash
poetry install
cp .env.example .env          # populate GAUGE_IDS at minimum
poetry run stream-conditions fetch 12345678
poetry run uvicorn stream_conditions.api.app:app --reload
```

## Stack

Python 3.12 · pandas · scikit-learn · statsmodels · FastAPI · HTMX · SQLite

## Data Sources

| Source | Endpoint | Key required |
|--------|----------|-------------|
| USGS NWIS | `waterservices.usgs.gov` | No |
| Open-Meteo | `api.open-meteo.com` | No (non-commercial) |
