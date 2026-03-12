"""Tests for NavwarnMessage._compute_valid_from / _compute_valid_until.

These exercise the parser-level methods added for the unified
timeline feature, complementing the JS-mirroring tests in
test_timeline_filtering.py.
"""

from datetime import datetime, timezone

import pytest

from scripts.parser import NavwarnMessage


def _msg(**kwargs) -> NavwarnMessage:
    """Create a minimal NavwarnMessage with overrides."""
    defaults = {
        "dtg": None,
        "raw_dtg": "",
        "msg_id": "TEST 1/25",
        "body": "test body",
    }
    defaults.update(kwargs)
    return NavwarnMessage(**defaults)


# ------------------------------------------------------------------
# _compute_valid_from
# ------------------------------------------------------------------


class TestNavwarnValidFrom:
    """Parser-level valid_from computation."""

    def test_from_dtg(self) -> None:
        msg = _msg(dtg=datetime(2025, 8, 19, 23, 59))
        result = msg._compute_valid_from()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt.year == 2025
        assert dt.month == 8
        assert dt.day == 19

    def test_from_dtg_with_tz(self) -> None:
        msg = _msg(
            dtg=datetime(
                2025,
                3,
                1,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )
        result = msg._compute_valid_from()
        assert result is not None
        assert "+00:00" in result

    def test_falls_back_to_year(self) -> None:
        msg = _msg(year=2018)
        result = msg._compute_valid_from()
        assert result == "2018-01-01T00:00:00+00:00"

    def test_returns_none_when_empty(self) -> None:
        msg = _msg()
        assert msg._compute_valid_from() is None

    def test_dtg_takes_priority_over_year(self) -> None:
        msg = _msg(
            dtg=datetime(2025, 6, 15, 0, 0),
            year=2025,
        )
        result = msg._compute_valid_from()
        assert result is not None
        assert "06-15" in result


# ------------------------------------------------------------------
# _compute_valid_until
# ------------------------------------------------------------------


class TestNavwarnValidUntil:
    """Parser-level valid_until computation."""

    def test_full_dtg_z(self) -> None:
        msg = _msg(
            cancellations=["THIS MSG 222359Z AUG 25"],
        )
        result = msg._compute_valid_until()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2025,
            8,
            22,
            23,
            59,
            tzinfo=timezone.utc,
        )

    def test_full_dtg_utc(self) -> None:
        msg = _msg(
            cancellations=[
                "THIS MSG 010900 UTC MAR 19",
            ],
        )
        result = msg._compute_valid_until()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt.month == 3 and dt.hour == 9

    def test_date_only(self) -> None:
        msg = _msg(
            cancellations=[
                "THIS MESSAGE 01 JAN 20",
            ],
        )
        result = msg._compute_valid_until()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2020,
            1,
            1,
            tzinfo=timezone.utc,
        )

    def test_no_self_cancellation(self) -> None:
        msg = _msg(
            cancellations=["101/24", "HYDROARC 119/25"],
        )
        assert msg._compute_valid_until() is None

    def test_empty_cancellations(self) -> None:
        msg = _msg(cancellations=[])
        assert msg._compute_valid_until() is None

    def test_skips_non_self_returns_self(self) -> None:
        msg = _msg(
            cancellations=[
                "101/24",
                "THIS MSG 050000Z JUL 26",
            ],
        )
        result = msg._compute_valid_until()
        assert result is not None
        dt = datetime.fromisoformat(result)
        assert dt == datetime(
            2026,
            7,
            5,
            0,
            0,
            tzinfo=timezone.utc,
        )

    def test_none_entry_in_list(self) -> None:
        """None items in cancellations list do not crash."""
        msg = _msg(cancellations=[None, ""])
        assert msg._compute_valid_until() is None


# ------------------------------------------------------------------
# GeoJSON output includes validity fields
# ------------------------------------------------------------------


class TestGeoJSONValidity:
    """Ensure to_geojson_feature includes the fields."""

    def test_feature_has_valid_fields(self) -> None:
        msg = _msg(
            dtg=datetime(2025, 1, 10, 0, 0),
            cancellations=["THIS MSG 200000Z JAN 25"],
            coordinates=[(60.0, 10.0)],
        )
        feat = msg.to_geojson_feature()
        props = feat["properties"]
        assert "valid_from" in props
        assert "valid_until" in props
        assert props["valid_from"] is not None
        assert props["valid_until"] is not None

    def test_feature_without_cancellation(self) -> None:
        msg = _msg(
            dtg=datetime(2025, 1, 10, 0, 0),
            coordinates=[(60.0, 10.0)],
        )
        feat = msg.to_geojson_feature()
        props = feat["properties"]
        assert "valid_from" in props
        assert props["valid_from"] is not None
        assert props["valid_until"] is None

    def test_multi_features_have_valid_fields(self) -> None:
        msg = _msg(
            dtg=datetime(2025, 1, 10, 0, 0),
            geometry="multipoint",
            groups=[
                [(60.0, 10.0)],
                [(61.0, 11.0)],
            ],
        )
        feats = msg.to_geojson_features()
        for f in feats:
            assert "valid_from" in f["properties"]
            assert "valid_until" in f["properties"]
