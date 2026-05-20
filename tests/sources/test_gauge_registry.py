"""Unit tests for gauge_registry — USGS site metadata fetch and GaugeStore."""

from __future__ import annotations

import pytest
import vcr as vcrpy

from stream_conditions.sources.gauge_registry import (
    Gauge,
    GaugeStore,
    InMemoryGaugeStore,
    _gauge_from_rdb,
    _parse_rdb_row,
    get_gauge,
    list_gauges,
    register_gauge,
)

CASSETTE_DIR = "tests/sources/cassettes"
_vcr = vcrpy.VCR(cassette_library_dir=CASSETTE_DIR)

# Minimal RDB text matching the USGS siteOutput=expanded format
_SAMPLE_RDB = """\
# This is a comment line
agency_cd\tsite_no\tstation_nm\tsite_tp_cd\tdec_lat_va\tdec_long_va\tstate_cd\thuc_cd\tdrain_area_va
5s\t15s\t50s\t7s\t16s\t16s\t2s\t16s\t8s
USGS\t02334430\tCHATTAHOOCHEE RIVER AT BUFORD DAM, NEAR BUFORD, GA\tST\t34.15666667\t-84.0784167\t13\t03130001\t1040.00
"""


# ── _parse_rdb_row ─────────────────────────────────────────────────────────────

def test_parse_rdb_row_returns_dict() -> None:
    row = _parse_rdb_row(_SAMPLE_RDB)
    assert row["site_no"] == "02334430"
    assert row["station_nm"] == "CHATTAHOOCHEE RIVER AT BUFORD DAM, NEAR BUFORD, GA"
    assert row["dec_lat_va"] == "34.15666667"
    assert row["huc_cd"] == "03130001"
    assert row["drain_area_va"] == "1040.00"


def test_parse_rdb_row_skips_comment_lines() -> None:
    row = _parse_rdb_row(_SAMPLE_RDB)
    assert "# This is a comment line" not in row


def test_parse_rdb_row_too_few_lines_raises() -> None:
    with pytest.raises(ValueError, match="too few"):
        _parse_rdb_row("# only a comment\n")


# ── _gauge_from_rdb ────────────────────────────────────────────────────────────

def test_gauge_from_rdb_fields() -> None:
    row = _parse_rdb_row(_SAMPLE_RDB)
    g = _gauge_from_rdb(row, notes="my note")
    assert g.site_id == "02334430"
    assert g.latitude == pytest.approx(34.15666667)
    assert g.longitude == pytest.approx(-84.0784167)
    assert g.state_cd == "13"
    assert g.huc_cd == "03130001"
    assert g.drain_area_sqmi == pytest.approx(1040.0)
    assert g.notes == "my note"


def test_gauge_from_rdb_name_is_title_case() -> None:
    row = _parse_rdb_row(_SAMPLE_RDB)
    g = _gauge_from_rdb(row, notes="")
    assert g.name == "Chattahoochee River At Buford Dam, Near Buford, Ga"


def test_gauge_from_rdb_missing_drain_area_is_none() -> None:
    row = _parse_rdb_row(_SAMPLE_RDB)
    row["drain_area_va"] = ""
    g = _gauge_from_rdb(row, notes="")
    assert g.drain_area_sqmi is None


# ── InMemoryGaugeStore ─────────────────────────────────────────────────────────

def _sample_gauge(site_id: str = "00000001") -> Gauge:
    return Gauge(
        site_id=site_id,
        name="Test Gauge",
        latitude=34.0,
        longitude=-84.0,
        state_cd="13",
        huc_cd="03130001",
        drain_area_sqmi=100.0,
    )


def test_inmemory_store_save_and_get() -> None:
    store = InMemoryGaugeStore()
    g = _sample_gauge()
    store.save(g)
    assert store.get(g.site_id) == g


def test_inmemory_store_get_missing_returns_none() -> None:
    assert InMemoryGaugeStore().get("99999999") is None


def test_inmemory_store_list() -> None:
    store = InMemoryGaugeStore()
    store.save(_sample_gauge("00000001"))
    store.save(_sample_gauge("00000002"))
    assert len(store.list()) == 2


def test_gauge_store_protocol() -> None:
    assert isinstance(InMemoryGaugeStore(), GaugeStore)


# ── register_gauge / get_gauge / list_gauges ───────────────────────────────────

@_vcr.use_cassette("gauge_register_02334430.yaml")
def test_register_gauge_buford() -> None:
    store = InMemoryGaugeStore()
    g = register_gauge("02334430", notes="buford dam", store=store)
    assert isinstance(g, Gauge)
    assert g.site_id == "02334430"
    assert g.latitude == pytest.approx(34.15666667)
    assert g.drain_area_sqmi == pytest.approx(1040.0)
    assert g.notes == "buford dam"


@_vcr.use_cassette("gauge_register_01646500.yaml")
def test_register_gauge_potomac() -> None:
    store = InMemoryGaugeStore()
    g = register_gauge("01646500", store=store)
    assert g.site_id == "01646500"
    assert g.latitude == pytest.approx(38.94977778)
    assert g.drain_area_sqmi == pytest.approx(11560.0)
    assert g.state_cd == "24"


@_vcr.use_cassette("gauge_register_02334430.yaml")
def test_register_gauge_persists_to_store() -> None:
    store = InMemoryGaugeStore()
    register_gauge("02334430", store=store)
    assert store.get("02334430") is not None


@_vcr.use_cassette("gauge_register_02334430.yaml")
def test_get_gauge_cache_hit_skips_network(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryGaugeStore()
    store.save(_sample_gauge("02334430"))

    # If the network were hit, the cassette would record a real call.
    # With a cache hit, no HTTP request is made — we just get the stored value.
    g = get_gauge("02334430", store=store)
    assert g.site_id == "02334430"
    assert g.name == "Test Gauge"  # returns the cached value, not the USGS name


@_vcr.use_cassette("gauge_register_02334430.yaml")
def test_get_gauge_cache_miss_fetches_usgs() -> None:
    store = InMemoryGaugeStore()
    g = get_gauge("02334430", store=store)
    assert g.site_id == "02334430"
    assert "Chattahoochee" in g.name


@_vcr.use_cassette("gauge_register_02334430.yaml")
def test_list_gauges_returns_all() -> None:
    store = InMemoryGaugeStore()
    register_gauge("02334430", store=store)
    gauges = list_gauges(store=store)
    assert len(gauges) == 1
    assert gauges[0].site_id == "02334430"
