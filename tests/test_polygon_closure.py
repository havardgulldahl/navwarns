"""
Test polygon closure for real-world navwarns.

This test validates that polygons from navwarns are properly closed
in the GeoJSON output, even when the source text doesn't explicitly
repeat the first coordinate at the end.
"""
import pytest
from scripts.parser import NavwarnMessage


# Real-world PRIP example from test_prip_001.txt
# This has a prohibited navigation area ("РАЙОНЕ ЗАПРЕТНОМ ДЛЯ ПЛАВАНИЯ")
# but the coordinates don't close the polygon explicitly
PRIP_UNCLOSED_POLYGON = """ПРИП МУРМАНСК 289/25 КАРТА 10100
БАРЕНЦЕВО И БЕЛОЕ МОРЯ
ПЕРЕДАВАТЬ 9 СУТОК
ПРИП МУРМАНСК 289 КАРТА 10100
БАРЕНЦЕВО И БЕЛОЕ МОРЯ
1. СПЕЦИАЛЬНЫЕ РАБОТЫ 23 ПО 26 СЕНТ 0700 ДО 1200
И 1600 ДО 2000 РАЙОНЕ ЗАПРЕТНОМ ДЛЯ ПЛАВАНИЯ
68-30.0С 041-35.0В
68-01.0С 044-12.0В
ДАЛЕЕ ПО БЕРЕГОВОЙ ЛИНИИ ДО
67-45.0С 044-10.0В
66-00.0С 040-40.0В
66-05.0С 040-25.0В
66-30.0C 040-36.0В
ДАЛЕЕ ПО БЕРЕГОВОЙ ЛИНИИ ДО
67-27.0С 041-02.0В
2. ОТМ ЭТОТ НР 262100 СЕНТ=
171000 МСК  ГС-
"""

# Real-world PRIP that explicitly closes the polygon
PRIP_CLOSED_POLYGON = """ПРИП МУРМАНСК 288/25 КАРТА 15004
КОЛЬСКИЙ ЗАЛИВ
МУРМАНСК 01 16/09 1500=
ПЕРЕДАВАТЬ 12 СУТОК
ПРИП МУРМАНСК 288 КАРТА 15004
КОЛЬСКИЙ ЗАЛИВ
1. СПЕЦИАЛЬНЫЕ РАБОТЫ 221700 ПО 230500
231700 ПО 240500 И 241700 ПО 250500 И
251700 ПО 260500 И 261700 ПО 270500 И
271700 ПО 280500 И 281700 ПО 290500 И
291700 ПО 292100 СЕНТ
РАЙОНЕ ЗАПРЕТНОМ ДЛЯ ПЛАВАНИЯ
69-18.4С 033-28.6В
69-17.8С 033-34.5В
ДАЛЕЕ ПО БЕРЕГОВОЙ ЛИНИИ ДО
69-05.0С 033-28.3В
69-05.0С 033-13.9В
ДАЛЕЕ ПО БЕРЕГОВОЙ ЛИНИИ ДО
69-18.4С 033-28.6В
2. ОТМ ЭТОТ НР 292200 СЕНТ=
171000 МСК  ГС-
"""


def test_unclosed_polygon_should_be_recognized_and_closed():
    """
    Test that a polygon with Russian keywords like 'РАЙОНЕ ЗАПРЕТНОМ ДЛЯ ПЛАВАНИЯ'
    (prohibited area for navigation) is correctly identified as a polygon,
    even if the coordinates don't explicitly close the ring.
    """
    lines = PRIP_UNCLOSED_POLYGON.strip().split('\n')
    header = lines[0]
    body = '\n'.join(lines[1:])
    
    msg = NavwarnMessage.prip_from_text(header, body)
    
    # Should have parsed coordinates
    assert len(msg.coordinates) >= 3, "Should have at least 3 coordinates for a polygon"
    
    # First and last coordinates should be different in the source
    first_coord = msg.coordinates[0]
    last_coord = msg.coordinates[-1]
    assert first_coord != last_coord, "Test data should have unclosed polygon"
    
    # Should be classified as polygon (not linestring) due to Russian area keywords
    assert msg.geometry == "polygon", f"Should be classified as polygon, got {msg.geometry}"
    
    # GeoJSON should have closed polygon
    geom = msg.geojson_geometry()
    assert geom is not None, "Should have geometry"
    assert geom["type"] == "Polygon", "Should be Polygon type in GeoJSON"
    
    ring = geom["coordinates"][0]
    assert len(ring) >= 4, "Polygon ring should have at least 4 points (3 unique + closure)"
    
    # First and last points in the ring should be identical (closed)
    assert ring[0] == ring[-1], "Polygon ring should be closed (first point == last point)"
    
    # The first point should correspond to the first coordinate
    lat, lon = first_coord
    assert ring[0] == [lon, lat], "First point should match first coordinate (GeoJSON format is [lon, lat])"


def test_explicitly_closed_polygon_stays_closed():
    """
    Test that a polygon that's already explicitly closed in the source
    remains properly closed in the GeoJSON output.
    """
    lines = PRIP_CLOSED_POLYGON.strip().split('\n')
    header = lines[0]
    body = '\n'.join(lines[1:])
    
    msg = NavwarnMessage.prip_from_text(header, body)
    
    # Should have parsed coordinates
    assert len(msg.coordinates) >= 3, "Should have at least 3 coordinates"
    
    # First and last coordinates should be the same in source (explicitly closed)
    first_coord = msg.coordinates[0]
    last_coord = msg.coordinates[-1]
    assert first_coord == last_coord, "Test data should have explicitly closed polygon"
    
    # Should be classified as polygon
    assert msg.geometry == "polygon", f"Should be classified as polygon, got {msg.geometry}"
    
    # GeoJSON should have closed polygon
    geom = msg.geojson_geometry()
    assert geom is not None, "Should have geometry"
    assert geom["type"] == "Polygon", "Should be Polygon type in GeoJSON"
    
    ring = geom["coordinates"][0]
    
    # First and last points should be identical
    assert ring[0] == ring[-1], "Polygon ring should be closed"


def test_polygon_closure_with_area_bounded_keyword():
    """
    Test that polygons with 'AREA BOUNDED' keyword are properly closed.
    """
    msg = NavwarnMessage(
        dtg=None,
        raw_dtg="TEST",
        msg_id="TEST/00",
        coordinates=[
            (60.0, 5.0),
            (61.0, 5.0),
            (61.0, 6.0),
            (60.0, 6.0),
            # Not closed - last != first
        ],
        cancellations=[],
        hazard_type="general",
        geometry="polygon",  # Would be set by analyze_geometry with "AREA BOUNDED" keyword
        radius=None,
        body="AREA BOUNDED BY 60-00N 005-00E 61-00N 005-00E 61-00N 006-00E 60-00N 006-00E",
    )
    
    geom = msg.geojson_geometry()
    assert geom["type"] == "Polygon"
    ring = geom["coordinates"][0]
    
    # Should be closed
    assert ring[0] == ring[-1], "Polygon should be auto-closed"
    # Should have 5 points (4 corners + closure)
    assert len(ring) == 5, f"Should have 5 points, got {len(ring)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
