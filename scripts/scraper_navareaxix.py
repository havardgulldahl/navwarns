#!/usr/bin/env python3
"""Downloader for NAVAREA XIX warnings from Kystverket.

Source page: https://kyvreports.kystverket.no/NavcoReport/navareaxixvarsler.aspx

The page is a classic ASP.NET WebForms page that renders warnings in nested
HTML tables.  Each warning block contains Number, Date and Warning fields.
We parse them with BeautifulSoup, convert to NavwarnMessage objects via the
shared parser, and write GeoJSON Feature files to current/navwarns/ —
identical to the other scrapers.
"""

import datetime
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, List

import requests
from bs4 import BeautifulSoup

try:
    from . import parser as navparser  # type: ignore
    from . import cleanup  # type: ignore
except ImportError:  # running as a script
    import importlib.util, pathlib

    this_dir = pathlib.Path(__file__).resolve().parent
    parser_path = this_dir / "parser.py"
    spec = importlib.util.spec_from_file_location("navparser", parser_path)
    navparser = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(navparser)  # type: ignore

    cleanup_path = this_dir / "cleanup.py"
    spec_clean = importlib.util.spec_from_file_location("cleanup", cleanup_path)
    cleanup = importlib.util.module_from_spec(spec_clean)  # type: ignore
    assert spec_clean and spec_clean.loader
    spec_clean.loader.exec_module(cleanup)  # type: ignore


# ---------------- Configuration ----------------
PAGE_URL = "https://kyvreports.kystverket.no/NavcoReport/navareaxixvarsler.aspx"
OUT_DIR = Path(f"history/{datetime.datetime.now().strftime('%Y')}/NAVAREAXIX")
CURRENT_DIR = Path("current")
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 4
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36"
}
# ------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url: str) -> requests.Response:
    """Fetch URL with retries and exponential backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if 200 <= resp.status_code < 300:
                return resp
            logging.warning("HTTP %s for %s", resp.status_code, url)
        except requests.RequestException as e:
            logging.warning(
                "Request error on %s (attempt %d/%d): %s",
                url,
                attempt,
                MAX_RETRIES,
                e,
            )
        if attempt < MAX_RETRIES:
            sleep_s = (RETRY_BACKOFF ** (attempt - 1)) + (0.1 * attempt)
            time.sleep(sleep_s)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


def save_raw_html(content: bytes) -> None:
    """Save a timestamped copy of the raw HTML to the history directory."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = datetime.datetime.now().strftime("NAVAREAXIX_%Y-%m-%d.html")
    path = OUT_DIR / filename
    path.write_bytes(content)
    logging.info("Saved raw HTML to %s", path)


def normalize_dtg(raw_dtg: str) -> str:
    """Normalize NAVAREA XIX DTG format to standard DDHHMMZ MON YY.

    Input examples:
        '061830 UTC mar 26'  -> '061830Z MAR 26'
        '271830 UTC feb 26'  -> '271830Z FEB 26'
        '160630 UTC feb 26'  -> '160630Z FEB 26'
    """
    # Strip and uppercase
    s = raw_dtg.strip().upper()
    # Replace ' UTC ' with 'Z '
    s = re.sub(r"\s+UTC\s+", "Z ", s)
    # Ensure standard format: DDHHMMZ MON YY
    m = re.match(r"(\d{6})Z\s+([A-Z]{3})\s+(\d{2})", s)
    if m:
        return f"{m.group(1)}Z {m.group(2)} {m.group(3)}"
    return s


def extract_warnings(html: bytes) -> List[dict]:
    """Parse the ASPX page and extract warning records.

    Returns a list of dicts with keys: number, date, body.
    """
    soup = BeautifulSoup(html, "html.parser")
    warnings: List[dict] = []

    # The page has a main GridView table.  Each row contains its own nested
    # <table> with Number, Date and Warning cells.
    grid = soup.find("table", id="GridView1")
    if not grid:
        logging.warning("Could not find GridView1 table in page")
        return warnings

    for row in grid.find_all("tr", class_="Item"):
        inner_table = row.find("table")
        if not inner_table:
            continue
        cells = inner_table.find_all("td")
        # Expect at least 6 cells: label+value for Number, Date, Warning
        if len(cells) < 6:
            continue

        number = cells[1].get_text(strip=True)
        date_str = cells[3].get_text(strip=True)
        # Warning cell may contain <BR/> tags — get text with newlines
        warning_cell = cells[5]
        # Replace <BR> tags with newlines for text extraction
        for br in warning_cell.find_all("br"):
            br.replace_with("\n")
        body = warning_cell.get_text().strip()

        warnings.append(
            {
                "number": number,
                "date": date_str,
                "body": body,
            }
        )

    logging.info("Extracted %d warnings from page", len(warnings))
    return warnings


def serialize_message(msg: Any) -> dict:
    if hasattr(msg, "to_geojson_feature"):
        feat = msg.to_geojson_feature()
        feat["properties"]["summary"] = None
        return feat
    coords = getattr(msg, "coordinates", []) or []
    geometry = {"type": "Point", "coordinates": []}
    if coords:
        lat, lon = coords[0]
        geometry = {"type": "Point", "coordinates": [lon, lat]}
    return {
        "type": "Feature",
        "id": getattr(msg, "msg_id", None),
        "geometry": geometry,
        "properties": {"raw": str(msg)},
    }


def main():
    logging.info("Fetching NAVAREA XIX from %s", PAGE_URL)
    resp = fetch(PAGE_URL)
    raw_html = resp.content

    # Save raw HTML to history
    save_raw_html(raw_html)

    warnings = extract_warnings(raw_html)
    if not warnings:
        logging.info("No NAVAREA XIX warnings extracted.")
        return

    navwarns_out_dir = CURRENT_DIR / "navwarns"
    navwarns_out_dir.mkdir(parents=True, exist_ok=True)
    active_filenames: set[str] = set()

    for warn in warnings:
        dtg_normalized = normalize_dtg(warn["date"])
        body = warn["body"]

        # Parse as standard NAVWARN message
        navmsgs = navparser.parse_navwarns(f"{dtg_normalized}\n{body}")

        for m in navmsgs:
            # If message doesn't have DTG, assign current timestamp
            if m.dtg is None:
                m.dtg = datetime.datetime.now(datetime.timezone.utc)
                if not m.raw_dtg or m.raw_dtg.startswith(m.msg_id or ""):
                    m.raw_dtg = m.dtg.strftime("%d%H%MZ %b %y").upper()

            safe_id = "unknown_id"
            if msg_id := getattr(m, "msg_id", None):
                safe_id = re.sub(r"[^\w\-]", "_", msg_id)

            filename = f"{safe_id}.json"
            active_filenames.add(filename)
            filepath = navwarns_out_dir / filename

            # Skip existing files to preserve original DTG
            if filepath.exists():
                logging.debug("Skipping existing file: %s", filename)
                continue

            with filepath.open("w", encoding="utf-8") as f_geo:
                f_geo.write(
                    json.dumps(serialize_message(m), ensure_ascii=False, indent=2)
                    + "\n"
                )
            logging.info("Wrote %s", filepath)

    cleanup.cleanup(
        active_filenames,
        CURRENT_DIR / "navwarns",
        "NAVAREA_XIX_*.json",
    )

    # Write scrape timestamp
    ts_file = CURRENT_DIR / ".scrape_timestamp_NAVAREAXIX"
    with open(ts_file, "w", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now(datetime.timezone.utc).isoformat()}\n")

    logging.info(
        "Processed %d NAVAREA XIX warnings, %d active files",
        len(warnings),
        len(active_filenames),
    )


if __name__ == "__main__":
    main()
