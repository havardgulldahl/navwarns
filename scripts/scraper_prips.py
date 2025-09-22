#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import math
import logging
from typing import Any, List, Tuple
from urllib.parse import urljoin, urlparse
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import datetime

"""
Downloader for Russian PRIPs  - Coastal Warnings

Configure BASE_URL and START_PATH below as needed.
"""

try:
    from . import parser as navparser  # type: ignore
except ImportError:  # running as a script
    import importlib.util, pathlib

    this_dir = pathlib.Path(__file__).resolve().parent
    parser_path = this_dir / "parser.py"
    spec = importlib.util.spec_from_file_location("navparser", parser_path)
    navparser = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(navparser)  # type: ignore

# ---------------- Configuration ----------------
PRIP_MURMANSK = "https://www.mapm.ru/Prip"
PRIP_ARKHANGELSK = "https://www.mapm.ru/PripAr"
PRIP_WEST = "https://www.mapm.ru/PripW"

OUT_DIR = f"history/{datetime.datetime.now().strftime('%Y')}/PRIP"  # output directory for downloaded pages
CURRENT_DIR = "current"
REQUEST_TIMEOUT = 20  # seconds
MAX_RETRIES = 4
RETRY_BACKOFF = 2.0  # exponential backoff factor
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36"
}
# ------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)


class Prip(BaseModel):
    header: str
    text: str


session = requests.Session()
session.headers.update(HEADERS)


def fetch(url: str) -> requests.Response:
    """Fetch URL with retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if 200 <= resp.status_code < 300:
                return resp
            else:
                logging.warning("HTTP %s for %s", resp.status_code, url)
        except requests.RequestException as e:
            logging.warning(
                "Request error on %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, e
            )
        if attempt < MAX_RETRIES:
            sleep_s = (RETRY_BACKOFF ** (attempt - 1)) + (0.1 * attempt)
            time.sleep(sleep_s)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


def save_content(content: bytes, filename: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, filename)
    with open(path, "wb") as f:
        f.write(content)
    logging.info("Saved %s", path)


def filename_from_url(url: str) -> str:
    stub = os.path.basename(urlparse(url).path)
    return stub + datetime.datetime.now().strftime("_%Y-%m-%d") + ".html"


def extract_prips_from_html(html: bytes) -> List[Prip]:
    """
    Extract individual navwarns from the HTML content.
    """
    soup = BeautifulSoup(html, "html.parser")
    prips = []
    # Example: assuming navwarns are in <div class="col-md-12">...</div>
    for div in soup.find_all("div", class_="col-md-12"):
        try:
            header = div.find("span").get_text(strip=True)
            text = div.find("pre").get_text(strip=True)
            prips.append(Prip(header=header, text=text))
        except Exception as e:
            logging.debug(f"Error getting Prip from {repr(div)}: {str(e)}")
    return prips


def serialize_message(msg: Any) -> dict:
    if hasattr(msg, "to_geojson_feature"):
        feat = msg.to_geojson_feature()
        # Preserve summary field placeholder for backward compatibility
        feat["properties"]["summary"] = None
        return feat
    # Fallback (should not usually happen)
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


def main(parse_files: List = []):
    # Discover all page URLs via pager numbers
    page_urls = (PRIP_MURMANSK, PRIP_ARKHANGELSK, PRIP_WEST)

    raw_prips: List[Prip] = []
    # Download each page
    if len(parse_files) > 0:
        # use local files
        for _file in parse_files:
            with open(_file) as f:
                raw_prips.extend(extract_prips_from_html(f.read()))
    else:
        for url in page_urls:
            try:
                resp = fetch(url)
                save_content(resp.content, filename_from_url(url))
                raw_prips.extend(extract_prips_from_html(resp.content))
            except Exception as e:
                logging.error("Failed to download %s: %s", url, e)

        logging.info("Done. Files saved in: %s", os.path.abspath(OUT_DIR))

    logging.info("Got %d raw prips", len(raw_prips))

    # Save prips to a file
    if raw_prips:
        parsed_prips = navparser.parse_prips([(p.header, p.text) for p in raw_prips])
        prips_file_raw = os.path.join(
            OUT_DIR, datetime.datetime.now().date().isoformat(), "prips_raw.txt"
        )
        os.makedirs(os.path.dirname(prips_file_raw), exist_ok=True)
        for m in parsed_prips:
            safe_id = "unknown_id"
            if msg_id := getattr(m, "msg_id", None):
                safe_id = re.sub(r"[^\w\-]", "_", msg_id)

            # print(json.dumps(serialize_message(m), ensure_ascii=False))
            filename = f"{safe_id}.json"
            with open(
                os.path.join(CURRENT_DIR, "prips", filename),
                "w",
                encoding="utf-8",
            ) as f_geo:
                f_geo.write(json.dumps(serialize_message(m), ensure_ascii=False) + "\n")
        logging.info(
            "Extracted %d prips from %d raw prips to %s",
            len(parsed_prips),
            len(raw_prips),
            CURRENT_DIR,
        )

    else:
        logging.info("No PRIPs extracted.")


if __name__ == "__main__":
    # Allow optional overrides from command line:
    import sys

    main(parse_files=sys.argv[1:])
