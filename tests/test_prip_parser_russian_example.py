def test_prip_parser_russian_example():
    from scripts.parser import NavwarnMessage

    prip_text = """
ПЕРЕДАВАТЬ 12 СУТОК
ПРИП МУРМАНСК 98 КАРТЫ 10100 10102
БАРЕНЦЕВО МОРЕ

    ПУСКИ РАКЕТНЫЕ 19 ПО 28 МАРТ 1100 ДО 2100
    РАЙОНАХ ОПАСНЫХ ДЛЯ ПЛАВАНИЯ
    А. 70-13.0С 045-43.0В
    70-02.0С 047-12.0В
    68-49.0С 045-50.0В
    69-00.0С 044-27.0В
    Б. 77-52.0С 061-55.0В
    77-37.0С 063-39.0В
    76-39.0С 060-30.0В
    76-56.0С 058-52.0В
    ОТМ ЭТОТ НР 282200 МАРТ=
    161000 МСК ГС-
"""
    expected_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [45.716667, 70.216667],
                            [47.2, 70.033333],
                            [45.833333, 68.816667],
                            [44.45, 69.0],
                            [45.716667, 70.216667],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [61.916667, 77.866667],
                            [63.65, 77.616667],
                            [60.5, 76.65],
                            [58.866667, 76.933333],
                            [61.916667, 77.866667],
                        ]
                    ],
                },
            },
        ],
    }
    # The header is the first non-empty line starting with 'ПРИП'
    lines = [l for l in prip_text.splitlines() if l.strip()]
    header = next(l for l in lines if l.strip().startswith("ПРИП"))
    body = "\n".join(lines[lines.index(header) + 1 :])
    msg = NavwarnMessage.prip_from_text(header, body)
    features = msg.to_geojson_features()

    # Remove properties for comparison
    def round_coords(coords):
        # Recursively round all floats in the coordinates list to 6 decimals
        if isinstance(coords, list):
            if coords and isinstance(coords[0], list):
                return [round_coords(c) for c in coords]
            return [round(c, 6) if isinstance(c, float) else c for c in coords]
        return coords

    def strip_and_round(f):
        return {
            "type": "Feature",
            "geometry": {
                "type": f["geometry"]["type"],
                "coordinates": round_coords(f["geometry"]["coordinates"]),
            },
            "properties": {},
        }

    result_geojson = {
        "type": "FeatureCollection",
        "features": [strip_and_round(f) for f in features],
    }
    expected_geojson_rounded = {
        "type": "FeatureCollection",
        "features": [strip_and_round(f) for f in expected_geojson["features"]],
    }
    assert result_geojson == expected_geojson_rounded
