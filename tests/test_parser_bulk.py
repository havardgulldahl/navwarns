import pytest
from pathlib import Path

from scripts.parser import parse_navwarns, parse_prips, NavwarnMessage

DATA_DIR = Path(__file__).parent / "test_data"

# Collect all .txt samples
TXT_FILES = sorted(DATA_DIR.glob("test_message_*.txt"))
PRIP_FILES = sorted(DATA_DIR.glob("test_prip_*.txt"))


@pytest.mark.parametrize("path", TXT_FILES, ids=[p.name for p in TXT_FILES])
def test_parse_each_text_file(path):
    text = path.read_text(encoding="utf-8")
    msgs = parse_navwarns(text)
    assert msgs, f"No messages parsed in {path.name}"
    # Basic structural checks per message
    for m in msgs:
        # raw_dtg always stored (first line) even if dtg parsing fails
        assert m.raw_dtg, f"raw_dtg missing for message in {path.name}"
        # If coordinates present, they are (float, float)
        for lat, lon in m.coordinates:
            assert isinstance(lat, float) and isinstance(lon, float)
        # Cancellations should not contain the leading word CANCEL
        for c in m.cancellations:
            assert not c.startswith("CANCEL ")


def test_sample_files_present():
    # Ensure we actually found test files to parametrize
    assert TXT_FILES, "No test_message_*.txt files discovered"


@pytest.mark.parametrize("path", PRIP_FILES, ids=[p.name for p in PRIP_FILES])
def test_parse_each_prip_file(path):
    text = path.read_text(encoding="utf-8")
    msgs = parse_prips([(text.splitlines()[0], text)])
    assert msgs, f"No messages parsed in {path.name}"
    # Basic structural checks per message
    for m in msgs:
        assert isinstance(m, NavwarnMessage)
        # raw_dtg always stored (first line) even if dtg parsing fails
        assert m.raw_dtg, f"raw_dtg missing for message in {path.name}"
        # If coordinates present, they are (float, float)
        for lat, lon in m.coordinates:
            assert isinstance(lat, float) and isinstance(lon, float)
        # Cancellations should not contain the leading word CANCEL
        for c in m.cancellations:
            assert not c.startswith("CANCEL ")
