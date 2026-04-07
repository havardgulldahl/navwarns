import pytest
from pathlib import Path

from scripts import parser as navparser
from scripts.scraper_navtex_sweden import (
    _year_from_msg_id,
    extract_warnings,
    normalize_dtg,
    serialize_message,
)

HTML_PATH = Path("history/2026/NAVTEX_SE/NAVTEX_SE_2026-04-07.html")


def _run_pipeline(warnings, msg_id):
    """Return the GeoJSON Feature dict for a single warning id."""
    w = next(x for x in warnings if x["msg_id"] == msg_id)
    year_hint = _year_from_msg_id(w["msg_id"])
    dtg_norm = normalize_dtg(w["dtg"], year_hint)
    navmsgs = navparser.parse_navwarns(f"{dtg_norm}\n{w['msg_id']}\n{w['body']}")
    assert len(navmsgs) == 1
    msg = navmsgs[0]
    if w["area"] and not msg.body.startswith(w["area"]):
        msg.body = f"[{w['area']}] {msg.body}"
    return serialize_message(msg)


@pytest.fixture(scope="module")
def warnings():
    return extract_warnings(HTML_PATH.read_bytes())


# ── 1. Polygon with explicit "BOUNDED BY PSN" ─────────────────────────────────
def test_explicit_bounded_by_polygon_closed(warnings):
    """11-coordinate polygon (POLISH NAV WARN 072/26) must be closed."""
    feat = _run_pipeline(warnings, "POLISH NAV WARN 072/26")
    geom = feat["geometry"]

    assert feat["id"] == "POLISH NAV WARN 072/26"
    assert geom is not None
    assert geom["type"] == "Polygon"

    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1], "ring must be closed"
    # Source already closes the ring (first coord repeated at end),
    # so 10 unique points + 1 closing point = 11 ring positions.
    assert len(ring) == 11


# ── 2. Polygon with "WITHIN AREA BOUNDED BY" ─────────────────────────────────
def test_within_area_bounded_by_polygon_closed(warnings):
    """7-coordinate polygon (LITHUANIAN NAV WARN 018/26) must be closed."""
    feat = _run_pipeline(warnings, "LITHUANIAN NAV WARN 018/26")
    geom = feat["geometry"]

    assert geom is not None
    assert geom["type"] == "Polygon"

    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1], "ring must be closed"
    assert len(ring) >= 4


# ── 3. Single position → Point ────────────────────────────────────────────────
def test_single_position_warning_yields_point(warnings):
    """Warning with one PSN coordinate (SWEDISH NAV WARN 057/26) → Point."""
    feat = _run_pipeline(warnings, "SWEDISH NAV WARN 057/26")
    geom = feat["geometry"]

    assert geom is not None
    assert geom["type"] == "Point"
    # 57-03.6N 012-09.8E → [lon, lat] ≈ [12.163, 57.06]
    lon, lat = geom["coordinates"]
    assert lon == pytest.approx(12.163, abs=0.001)
    assert lat == pytest.approx(57.060, abs=0.001)


# ── 4. Text-only advisory → null geometry ─────────────────────────────────────
def test_text_only_warning_yields_null_geometry(warnings):
    """Informational warning with no coordinates (BALTIC SEA NAV WARN 020/26)."""
    feat = _run_pipeline(warnings, "BALTIC SEA NAV WARN 020/26")
    assert feat["geometry"] is None
    assert feat["properties"]["summary"] is None


# ── 5. "TEMPORARILY DANGEROUS" area → Polygon ───────────────────────────────
def test_area_temporarily_dangerous_yields_polygon(warnings):
    """7-coordinate area 'IN AREA TEMPORARILY DANGEROUS TO SHIPPING'
    must be classified as a closed Polygon, not a LineString."""
    feat = _run_pipeline(warnings, "KALININGRAD NAV WARN 052/26")
    geom = feat["geometry"]

    assert geom is not None
    assert geom["type"] == "Polygon"

    ring = geom["coordinates"][0]
    assert ring[0] == ring[-1], "ring must be closed"
    # 7 unique coords, source is not closed → _ensure_closed_ring appends first
    assert len(ring) == 8
