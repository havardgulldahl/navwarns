#!/usr/bin/env python3
"""Scraper for Swedish NAVTEX warnings from Sjöfartsverket.

Source page:
    https://navvarn.sjofartsverket.se/en/Navigationsvarningar/Navtex

The page lists active NAVTEX warnings grouped by Baltic Sea sub-areas.
Each warning block contains a DTG, a message identifier (e.g.
"BALTIC SEA NAV WARN 001/26") and a free-text body.

Warnings are parsed with BeautifulSoup, converted to NavwarnMessage
objects via the shared parser, and written as GeoJSON Feature files
to current/navwarns/ — identical to the other scrapers.
"""

import datetime
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

try:
    from . import parser as navparser  # type: ignore
    from . import cleanup  # type: ignore
except ImportError:  # running as a script
    import importlib.util
    import pathlib

    this_dir = pathlib.Path(__file__).resolve().parent
    parser_path = this_dir / "parser.py"
    spec = importlib.util.spec_from_file_location(
        "navparser",
        parser_path,
    )
    navparser = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(navparser)  # type: ignore

    cleanup_path = this_dir / "cleanup.py"
    spec_clean = importlib.util.spec_from_file_location(
        "cleanup",
        cleanup_path,
    )
    cleanup = importlib.util.module_from_spec(spec_clean)  # type: ignore
    assert spec_clean and spec_clean.loader
    spec_clean.loader.exec_module(cleanup)  # type: ignore


# ---------------- Configuration ----------------
PAGE_URL = "https://navvarn.sjofartsverket.se" "/en/Navigationsvarningar/Navtex"
OUT_DIR = Path(
    f"history/{datetime.datetime.now().strftime('%Y')}/NAVTEX_SE",
)
CURRENT_DIR = Path("current")
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 4
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
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
            logging.warning(
                "HTTP %s for %s",
                resp.status_code,
                url,
            )
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
    raise RuntimeError(
        f"Failed to fetch {url} after {MAX_RETRIES} attempts",
    )


def save_raw_html(content: bytes) -> None:
    """Save a timestamped copy of the raw HTML to history."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = datetime.datetime.now().strftime(
        "NAVTEX_SE_%Y-%m-%d.html",
    )
    path = OUT_DIR / filename
    path.write_bytes(content)
    logging.info("Saved raw HTML to %s", path)


def normalize_dtg(
    raw_dtg: str,
    year_hint: str | None = None,
) -> str:
    """Normalise NAVTEX DTG to standard DDHHMMZ MON YY.

    Input examples (from the page, year is missing):
        '051250 UTC JAN'  -> '051250Z JAN <YY>'
        '121139 UTC MAR'  -> '121139Z MAR <YY>'

    *year_hint* is a 2-digit year string extracted from the
    accompanying message identifier (e.g. "26" from "001/26").
    When not available the current UTC year is used.
    """
    s = raw_dtg.strip().upper()
    s = re.sub(r"\s+UTC\s+", "Z ", s)
    # Match DDHHMMZ MON [YY]
    m = re.match(r"(\d{6})Z\s+([A-Z]{3})(?:\s+(\d{2}))?", s)
    if m:
        ddhhmm = m.group(1)
        mon = m.group(2)
        yr = m.group(3)
        if not yr:
            yr = year_hint or datetime.datetime.now(
                datetime.timezone.utc,
            ).strftime("%y")
        return f"{ddhhmm}Z {mon} {yr}"
    return s


def extract_warnings(
    html: bytes,
) -> List[Dict[str, str]]:
    """Parse the NAVTEX page and extract unique warnings.

    Returns a list of dicts:
        area  – sub-area name (e.g. "Skagerrak")
        dtg   – raw DTG string
        msg_id – e.g. "BALTIC SEA NAV WARN 001/26"
        body  – free-text warning body
    """
    soup = BeautifulSoup(html, "html.parser")
    warnings: List[Dict[str, str]] = []
    seen_ids: set[str] = set()

    container = soup.find("div", id="warnings_by_area")
    if not container:
        logging.warning("Could not find warnings_by_area div")
        return warnings

    for area_div in container.find_all(
        "div",
        class_="nav-area-div",
    ):
        area_h5 = area_div.find("h5")
        area_name = area_h5.get_text(strip=True) if area_h5 else "Unknown"

        for p_tag in area_div.find_all("p"):
            # DTG is the first text node before <br>
            dtg_text = ""
            for child in p_tag.children:
                if isinstance(child, str):
                    dtg_text += child.strip()
                elif child.name == "br":
                    break
                else:
                    break

            # Message ID from <b> tag
            b_tag = p_tag.find("b")
            msg_id_raw = ""
            if b_tag:
                msg_id_raw = " ".join(
                    b_tag.get_text().split(),
                )

            # Body from <span>
            span_tag = p_tag.find(
                "span",
                style=re.compile("white-space"),
            )
            body = ""
            if span_tag:
                body = span_tag.get_text().strip()

            if not msg_id_raw:
                continue

            # Deduplicate: same warning appears in multiple areas
            dedup_key = msg_id_raw.upper()
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            warnings.append(
                {
                    "area": area_name,
                    "dtg": dtg_text.strip(),
                    "msg_id": msg_id_raw,
                    "body": body,
                }
            )

    logging.info(
        "Extracted %d unique warnings from page",
        len(warnings),
    )
    return warnings


def _year_from_msg_id(msg_id: str) -> str | None:
    """Extract 2-digit year from ID like 'DANISH NAV WARN 154/26'."""
    m = re.search(r"\d+/(\d{2})\s*$", msg_id)
    return m.group(1) if m else None


def serialize_message(msg: Any) -> dict:
    """Convert NavwarnMessage to a GeoJSON Feature dict."""
    if hasattr(msg, "to_geojson_feature"):
        feat = msg.to_geojson_feature()
        feat["properties"]["summary"] = None
        return feat
    coords = getattr(msg, "coordinates", []) or []
    geometry: dict = {"type": "Point", "coordinates": []}
    if coords:
        lat, lon = coords[0]
        geometry = {"type": "Point", "coordinates": [lon, lat]}
    return {
        "type": "Feature",
        "id": getattr(msg, "msg_id", None),
        "geometry": geometry,
        "properties": {"raw": str(msg)},
    }


def main() -> None:
    """Fetch and process Swedish NAVTEX warnings."""
    logging.info("Fetching NAVTEX Sweden from %s", PAGE_URL)
    resp = fetch(PAGE_URL)
    raw_html = resp.content

    # Save raw HTML to history
    save_raw_html(raw_html)

    warnings = extract_warnings(raw_html)
    if not warnings:
        logging.info("No NAVTEX Sweden warnings extracted.")
        return

    navwarns_out = CURRENT_DIR / "navwarns"
    navwarns_out.mkdir(parents=True, exist_ok=True)
    active_filenames: set[str] = set()

    for warn in warnings:
        year_hint = _year_from_msg_id(warn["msg_id"])
        dtg_norm = normalize_dtg(warn["dtg"], year_hint)
        body = warn["body"]
        full_text = f"{warn['msg_id']}\n{body}"

        # Parse through the standard parser pipeline
        navmsgs = navparser.parse_navwarns(
            f"{dtg_norm}\n{full_text}",
        )

        for m in navmsgs:
            # If parser couldn't extract msg_id, use the one
            # from the HTML <b> tag
            if not m.msg_id:
                m.msg_id = warn["msg_id"]

            if m.dtg is None:
                m.dtg = datetime.datetime.now(
                    datetime.timezone.utc,
                )
                if not m.raw_dtg or m.raw_dtg.startswith(m.msg_id or ""):
                    m.raw_dtg = m.dtg.strftime(
                        "%d%H%MZ %b %y",
                    ).upper()

            # Add area context to properties via body prefix
            if warn["area"] and not m.body.startswith(
                warn["area"],
            ):
                m.body = f"[{warn['area']}] {m.body}"

            safe_id = "unknown_id"
            if msg_id := getattr(m, "msg_id", None):
                safe_id = re.sub(r"[^\w\-]", "_", msg_id)

            filename = f"{safe_id}.json"
            active_filenames.add(filename)
            filepath = navwarns_out / filename

            if filepath.exists():
                logging.debug(
                    "Skipping existing file: %s",
                    filename,
                )
                continue

            with filepath.open("w", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        serialize_message(m),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
            logging.info("Wrote %s", filepath)

    # Clean up warnings no longer active on the page
    cleanup.cleanup(
        active_filenames,
        navwarns_out,
        "*_NAV_WARN_*.json",
    )

    # Write scrape timestamp
    ts_file = CURRENT_DIR / ".scrape_timestamp_NAVTEX_SE"
    with open(ts_file, "w", encoding="utf-8") as f:
        f.write(
            datetime.datetime.now(
                datetime.timezone.utc,
            ).isoformat()
            + "\n"
        )

    logging.info(
        "Processed %d NAVTEX Sweden warnings, %d active files",
        len(warnings),
        len(active_filenames),
    )


if __name__ == "__main__":
    main()
