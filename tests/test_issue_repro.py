import pytest
from scripts.parser import NavwarnMessage, analyze_geometry, parse_coordinate_groups

ISSUE_TEXT = """
ЗНАКИ НЕПРИГОДНЫ ДЛЯ НАВИГАЦИОННЫХ ЦЕЛЕЙ
1. ОРАНИЕМИ 69-35.4С 031-18.7В НР 38
2. ЗЕМЛЯНОЙ-СРЕДНИЙ СВЕТЯЩИЙ 69-49.8С 031-46.9В НР 60
3. КОРОВИЙ СВЕТЯЩИЙ 69-51.1С 031-57.0В НР 62
4. ЗУБОВСКИЙ 69-48.0С 032-37.7В НР 85
5. ЗУБОВСКИЙ ЛАЗАРЬ 69-47.5С 032-41.5В НР 90
   НА ВЕРШИНЕ РЛП
6. ПУШКА 69-23.0С 033-28.6В НР 280
7. СТВОР ГОРЯЧИНСКИЙ 69-10.5С 033-28.0В НР 438 439
8. РУЧЬЕВОЙ 69-18.4С 034-03.7В НР 815
9. КИЛЬДИНСКИЙ ЯКОРНЫЙ ЗАПАДНЫЙ 69-18.9С 034-13.7В НР 835.5
10. КИЛЬДИНСКИЙ ЯКОРНЫЙ 69-19.3С 034-16.8В НР 836
11. ЗАРУБИХА СВЕТЯЩИЙ 69-18.1С 034-18.0В НР 840
12. ЖИЛОЙ СВЕТЯЩИЙ 69-12.1С 035-07.9В НР 910
13. ЛОДЕЙНЫЙ СВЕТЯЩИЙ 69-10.7С 035-07.8В НР 915
14. ЗЕЛЕНЕЦКИЙ-ПРИЧАЛЬНЫЙ 69-07.1С 036-04.3В НР 964
15. СТВОР ЛОПСКИЙ 68-07.6С 039-47.3В НР 1200 1201
16. ОТМ 5/23 17/23 156/23 И ЭТОТ ПУНКТ=
161000 МСК  ГС-
"""


ISSUE_TEXT_NO_ENUM = """
ЗНАКИ НЕПРИГОДНЫ ДЛЯ НАВИГАЦИОННЫХ ЦЕЛЕЙ

ОРАНИЕМИ 69-35.4С 031-18.7В НР 38
ЗЕМЛЯНОЙ-СРЕДНИЙ СВЕТЯЩИЙ 69-49.8С 031-46.9В НР 60
КОРОВИЙ СВЕТЯЩИЙ 69-51.1С 031-57.0В НР 62
ЗУБОВСКИЙ 69-48.0С 032-37.7В НР 85
ЗУБОВСКИЙ ЛАЗАРЬ 69-47.5С 032-41.5В НР 90
НА ВЕРШИНЕ РЛП
ПУШКА 69-23.0С 033-28.6В НР 280
СТВОР ГОРЯЧИНСКИЙ 69-10.5С 033-28.0В НР 438 439
РУЧЬЕВОЙ 69-18.4С 034-03.7В НР 815
КИЛЬДИНСКИЙ ЯКОРНЫЙ ЗАПАДНЫЙ 69-18.9С 034-13.7В НР 835.5
КИЛЬДИНСКИЙ ЯКОРНЫЙ 69-19.3С 034-16.8В НР 836
ЗАРУБИХА СВЕТЯЩИЙ 69-18.1С 034-18.0В НР 840
ЖИЛОЙ СВЕТЯЩИЙ 69-12.1С 035-07.9В НР 910
ЛОДЕЙНЫЙ СВЕТЯЩИЙ 69-10.7С 035-07.8В НР 915
ЗЕЛЕНЕЦКИЙ-ПРИЧАЛЬНЫЙ 69-07.1С 036-04.3В НР 964
СТВОР ЛОПСКИЙ 68-07.6С 039-47.3В НР 1200 1201
ОТМ 5/23 17/23 156/23 И ЭТОТ ПУНКТ=
161000 МСК ГС
"""


def test_issue_repro_parsing():
    msg = NavwarnMessage.from_text("000000Z FEB 26", ISSUE_TEXT)

    print(f"Geometry type: {msg.geometry}")
    print(f"Number of groups: {len(msg.groups)}")

    # Check if groups are correctly parsed
    # We expect 15 groups with coordinates (item 16 has no coordinates)
    # Actually, items 1-15 have 1 coordinate each.

    # Let's count groups with coordinates
    groups_with_coords = [g for g in msg.groups if g]
    print(f"Groups with coords: {len(groups_with_coords)}")

    # Assertions that are expected to fail currently
    assert len(groups_with_coords) >= 15
    assert msg.geometry != "linestring"

    features = msg.to_geojson_features()
    print(f"Number of features: {len(features)}")

    assert len(features) >= 15
    for f in features:
        if f["geometry"]:
            assert f["geometry"]["type"] == "Point"


def test_issue_repro_no_enum_is_multipoint():
    msg = NavwarnMessage.prip_from_text("ПРИП МУРМАНСК 999/26", ISSUE_TEXT_NO_ENUM)

    assert len(msg.coordinates) == 15
    assert msg.geometry == "multipoint"

    feature = msg.to_geojson_feature()
    assert feature["geometry"]["type"] == "MultiPoint"


if __name__ == "__main__":
    test_issue_repro_parsing()
