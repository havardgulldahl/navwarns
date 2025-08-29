import importlib
from pathlib import Path
import pytest

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
