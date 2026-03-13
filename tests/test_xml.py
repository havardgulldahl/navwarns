import importlib
from pathlib import Path
import pytest

from scripts.scraper import parse_broadcast_warn_xml, serialize_message_features

# Base directory for this test file
HERE = Path(__file__).parent
DATA_DIR = HERE / "test_data"


@pytest.fixture(scope="session")
def scraper_module():
    return importlib.import_module("scripts.scraper")


@pytest.fixture(scope="session")
def xml_files():
    files = sorted(DATA_DIR.glob("*.xml"))
    return files


def test_two_xml_files_present(xml_files):
    # The prompt specifies there are two XML files
    assert (
        len(xml_files) == 2
    ), f"Expected 2 XML files in {DATA_DIR}, found {len(xml_files)}: {xml_files}"


def _find_parser(mod):
    # Try common function names
    candidates = [
        "parse_file",
        "parse",
        "scrape_file",
        "scrape",
        "load_file",
        "load",
        "parse_xml",
    ]
    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise AssertionError(
        "No suitable parser function found in scripts.scraper (tried: {})".format(
            candidates
        )
    )


@pytest.fixture(scope="session")
def parser(scraper_module):
    return _find_parser(scraper_module)


@pytest.mark.parametrize("as_path_obj", [True, False])
def test_parse_each_xml_returns_data(xml_files, parser, as_path_obj):
    for xml_path in xml_files:
        arg = xml_path if as_path_obj else str(xml_path)
        result = parser(arg)
        assert result is not None, f"Parser returned None for {xml_path}"
        # Basic structural assertions
        if isinstance(result, (list, tuple, set)):
            assert len(result) > 0, f"Empty collection returned for {xml_path}"
            assert all(
                item is not None for item in result
            ), f"Collection contains None for {xml_path}"
        elif isinstance(result, dict):
            assert len(result) > 0, f"Empty dict returned for {xml_path}"
        else:
            # Fallback: ensure stringifiable and non-empty when stripped
            rep = str(result).strip()
            assert rep, f"Result string representation empty for {xml_path}"


def test_idempotent_parsing(xml_files, parser):
    for xml_path in xml_files:
        r1 = parser(str(xml_path))
        r2 = parser(str(xml_path))
        # Compare representations to avoid requiring deep equality on custom objects
        assert str(r1) == str(r2), f"Parsing not idempotent for {xml_path}"


def test_all_xml_files_unique_outputs(xml_files, parser):
    outputs = []
    for xml_path in xml_files:
        outputs.append((xml_path.name, str(parser(str(xml_path)))))
    # Ensure different files do not all collapse to identical (weak heuristic)
    unique_payloads = {payload for _, payload in outputs}
    assert len(unique_payloads) == len(
        outputs
    ), "Different XML files produced identical outputs (heuristic failed)"


def test_parser_accepts_path_and_string(xml_files, parser):
    xml_path = xml_files[0]
    res_path = parser(xml_path)
    res_str = parser(str(xml_path))
    assert str(res_path) == str(
        res_str
    ), "Parser produced different results for Path vs str input"


# --- Tests for broadcast-warn XML geometry classification ---

BROADCAST_WARN_AREA_BOUND = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<broadcast-warn>
    <broadcastWarnCancelledEntity>
        <msgYear>2017</msgYear>
        <msgNumber>421</msgNumber>
        <navArea>C</navArea>
        <subregion>42</subregion>
        <text>BARENTS SEA.
RUSSIA.
DNC 22.
1. GUNNERY AND MISSILE OPERATIONS 0300Z TO 2100Z
   DAILY 26 THRU 29 DEC IN AREA BOUND BY
   72-20.0N 035-40.0E, 72-10.0N 038-00.0E,
   71-00.0N 041-35.0E, 70-15.0N 042-00.0E,
   69-15.0N 035-09.4E, 69-20.2N 034-24.2E,
   69-20.5N 033-58.0E, 69-35.0N 033-38.0E,
   69-58.0N 033-38.0E.
2. CANCEL THIS MSG 292200Z DEC 17.
</text>
        <status>C</status>
        <issueDate>220632Z DEC 2017</issueDate>
        <authority>NAVAREA XX 214/17 220530Z DEC 17.</authority>
        <cancelDate>292200Z DEC 2017</cancelDate>
        <cancelNavArea>C</cancelNavArea>
        <cancelMsgYear>2017</cancelMsgYear>
        <cancelMsgNumber>421</cancelMsgNumber>
    </broadcastWarnCancelledEntity>
</broadcast-warn>"""


def test_broadcast_warn_polygon_geometry():
    """parse_broadcast_warn_xml should classify 'AREA BOUND BY'
    messages as polygon, not point."""
    messages = parse_broadcast_warn_xml(BROADCAST_WARN_AREA_BOUND)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.msg_id == "HYDROARC 421/17(42)"
    assert msg.geometry == "polygon"
    assert len(msg.coordinates) == 9

    feats = serialize_message_features(msg)
    assert len(feats) == 1
    feat = feats[0]
    assert feat["geometry"]["type"] == "Polygon"
    ring = feat["geometry"]["coordinates"][0]
    # Ring should be closed
    assert ring[0][0] == pytest.approx(ring[-1][0], abs=1e-6)
    assert ring[0][1] == pytest.approx(ring[-1][1], abs=1e-6)
