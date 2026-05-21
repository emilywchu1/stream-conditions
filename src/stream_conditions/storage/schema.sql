-- stream-conditions schema
-- All timestamps stored as ISO-8601 strings in UTC.

CREATE TABLE IF NOT EXISTS gauges (
    site_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    state           TEXT NOT NULL DEFAULT '',
    river_name      TEXT NOT NULL DEFAULT '',
    huc_cd          TEXT NOT NULL DEFAULT '',
    drain_area_sqmi REAL,
    notes           TEXT NOT NULL DEFAULT '',
    registered_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- One row per fetch: stream conditions joined with weather at that moment.
CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id         TEXT    NOT NULL REFERENCES gauges(site_id) ON DELETE CASCADE,
    fetched_at      TEXT    NOT NULL,
    discharge_cfs   REAL,
    gauge_height_ft REAL,
    water_temp_c    REAL,
    air_temp_c      REAL,
    humidity_pct    REAL,
    pressure_hpa    REAL,
    cloud_cover_pct REAL,
    precip_mm       REAL,
    wind_speed_kmh  REAL,
    wind_dir_deg    REAL
);

CREATE INDEX IF NOT EXISTS ix_snapshots_site_fetched
    ON snapshots (site_id, fetched_at);

-- User-logged fishing sessions with optional hatch and catch data.
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id         TEXT    NOT NULL REFERENCES gauges(site_id) ON DELETE CASCADE,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    hatch_order     TEXT,
    hatch_stage     TEXT,
    hatch_intensity INTEGER CHECK (hatch_intensity IS NULL OR hatch_intensity BETWEEN 0 AND 3),
    fish_count      INTEGER,
    fish_species    TEXT,
    notes           TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_sessions_site_started
    ON sessions (site_id, started_at);

-- Every model prediction is logged so we can score it later.
CREATE TABLE IF NOT EXISTS predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id             TEXT    NOT NULL REFERENCES gauges(site_id) ON DELETE CASCADE,
    generated_at        TEXT    NOT NULL,
    target_window_start TEXT    NOT NULL,
    target_window_end   TEXT    NOT NULL,
    score               REAL    NOT NULL,
    model_version       TEXT    NOT NULL,
    features_json       TEXT    NOT NULL DEFAULT '{}',
    actual_outcome      INTEGER             -- backfilled from a real session
);

CREATE INDEX IF NOT EXISTS ix_predictions_site_generated
    ON predictions (site_id, generated_at);
