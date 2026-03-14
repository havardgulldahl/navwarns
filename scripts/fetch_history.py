# Get all history files from the server, or re-parse from stored XML

from datetime import datetime
import time
import pathlib

import scraper

HISTORY_URL = (
    "https://msi.nga.mil/api/publications/broadcast-warn"
    "?navArea={navArea}&status=all"
    "&msgYear={msgYear}&output=xml"
)
HISTORY_DIR = pathlib.Path("history")


def _local_xml_path(area: str, year: int) -> pathlib.Path:
    """Return the path to a locally-stored XML file."""
    return HISTORY_DIR / (
        f"broadcast-warn?navArea={area}" f"&status=all&msgYear={year}&output=xml.xml"
    )


def _fetch_and_store(
    area: str,
    year: int,
    output_dir: pathlib.Path,
) -> None:
    """Fetch XML from NGA API, store XML + JSON features."""
    url = HISTORY_URL.format(navArea=area, msgYear=year)
    print(url)
    try:
        xml_text = scraper.fetch_xml(url)
    except Exception as exc:
        print(f"Error fetching {year}/{area}: {exc}")
        return

    # Save raw XML alongside existing archives
    xml_path = _local_xml_path(area, year)
    xml_path.write_text(xml_text, encoding="utf-8")

    msgs = scraper.parse_broadcast_warn_xml(xml_text)
    output_dir.mkdir(parents=True, exist_ok=True)
    active = scraper.store_messages(
        msgs,
        force=True,
        output_dir=output_dir,
    )
    print(f"{year}/{area}: {len(active)} features" f" (fetched from server)")
    time.sleep(1)


def main() -> None:
    """Re-parse history from local XML or fetch from server."""
    for year in range(2010, datetime.now().year):
        for area in ["A", "B", "C", "D", "E"]:
            output_dir = HISTORY_DIR / str(year) / area
            xml_path = _local_xml_path(area, year)

            if xml_path.exists():
                xml_text = xml_path.read_text(
                    encoding="utf-8",
                )
                msgs = scraper.parse_broadcast_warn_xml(
                    xml_text,
                )
                output_dir.mkdir(parents=True, exist_ok=True)
                active = scraper.store_messages(
                    msgs,
                    force=True,
                    output_dir=output_dir,
                )
                print(f"{year}/{area}: {len(active)}" f" features (from local XML)")
            else:
                _fetch_and_store(area, year, output_dir)


if __name__ == "__main__":
    main()
