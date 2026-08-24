#!/usr/bin/env python3
"""Downloader for Kystverket navigation warnings via the JSON API.

Covers two endpoints on https://api.kystverket.no:

  * ``/data/navigationwarnings/navareaxix/`` -- NAVAREA XIX warnings
  * ``/data/navigationwarnings/coastal/``    -- Norwegian coastal warnings (NAVCO)

Both return structured records with a WKT ``position`` field, so geometry no
longer has to be regex-extracted from the message text.  The message body is
still fed through the shared parser to derive hazard type, cancellations and
validity dates, keeping the output schema compatible with the other scrapers.

Replaces the HTML scraping in ``scraper_navareaxix.py``.
"""

import argparse
import datetime
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping

try:
    from . import parser as navparser  # type: ignore
    from . import cleanup  # type: ignore
except ImportError:  # running as a script
    import importlib.util
    import pathlib

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
API_BASE = "https://api.kystverket.no"
NAVAREAXIX_URL = f"{API_BASE}/data/navigationwarnings/navareaxix/"
COASTAL_URL = f"{API_BASE}/data/navigationwarnings/coastal/"
CURRENT_DIR = Path("current")
HISTORY_DIR = Path("history")
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 4
RETRY_BACKOFF = 2.0
SOURCE_TAG = "kystverket-api"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36",
    "Accept": "application/json",
}
# ------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

session = requests.Session()
session.headers.update(HEADERS)


@dataclass(frozen=True)
class SourceConfig:
    """Per-endpoint settings; cleanup globs must stay disjoint between sources."""

    key: str
    url: str
    id_prefix: str
    eventclass: str
    cleanup_glob: str


NAVAREAXIX = SourceConfig(
    key="NAVAREAXIX",
    url=NAVAREAXIX_URL,
    id_prefix="NAVAREA XIX",
    eventclass="NA19",
    cleanup_glob="NAVAREA_XIX_*.json",
)

NAVCO = SourceConfig(
    key="NAVCO",
    url=COASTAL_URL,
    id_prefix="NAVCO",
    eventclass="NAVCO",
    cleanup_glob="NAVCO_*.json",
)

SOURCES: Dict[str, SourceConfig] = {
    "navareaxix": NAVAREAXIX,
    "coastal": NAVCO,
}

GEOM_KIND_MAP = {
    "Point": "point",
    "MultiPoint": "multipoint",
    "LineString": "linestring",
    "MultiLineString": "linestring",
    "Polygon": "polygon",
    "MultiPolygon": "polygon",
}


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
                "Request error on %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, e
            )
        if attempt < MAX_RETRIES:
            time.sleep((RETRY_BACKOFF ** (attempt - 1)) + (0.1 * attempt))
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


def fetch_records(source: SourceConfig) -> List[dict]:
    """Fetch and decode the JSON array for one endpoint."""
    logging.info("Fetching %s from %s", source.key, source.url)
    payload = fetch(source.url).json()
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON array from {source.url}")
    return payload


def save_raw_json(records: Sequence[dict], source: SourceConfig, history_dir: Path) -> Path:
    """Save a timestamped copy of the raw API response to the history directory."""
    now = datetime.datetime.now(datetime.timezone.utc)
    out_dir = history_dir / f"{now:%Y}" / source.key
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{source.key}_{now:%Y-%m-%d}.json"
    path.write_text(
        json.dumps(list(records), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logging.info("Saved raw JSON to %s", path)
    return path


def parse_api_timestamp(value: Optional[str]) -> Optional[datetime.datetime]:
    """Parse a naive API timestamp as UTC."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def iso_utc(value: Optional[str]) -> Optional[str]:
    dt = parse_api_timestamp(value)
    return dt.isoformat() if dt else None


def dtg_from_timestamp(value: Optional[str]) -> str:
    """Convert an API timestamp to the parser's DDHHMMZ MON YY form."""
    dt = parse_api_timestamp(value)
    if dt is None:
        return ""
    return dt.strftime("%d%H%MZ %b %y").upper()


def wkt_to_geojson(position: Optional[str]) -> Optional[dict]:
    """Convert the API's WKT ``position`` to a GeoJSON geometry, or None."""
    if not position or not position.strip():
        return None
    try:
        geom = shapely_wkt.loads(position.strip())
    except Exception as e:  # shapely raises several unrelated exception types
        logging.warning("Unparseable WKT %r: %s", position[:80], e)
        return None
    if geom is None or geom.is_empty:
        return None
    return navparser._to_geojson_lists(mapping(geom))


def wkt_to_latlon_coords(position: Optional[str]) -> List[Tuple[float, float]]:
    """Flatten a WKT geometry into the parser's (lat, lon) coordinate order."""
    geom_json = wkt_to_geojson(position)
    if not geom_json:
        return []
    coords: List[Tuple[float, float]] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and all(isinstance(v, (int, float)) for v in node[:2])
        ):
            coords.append((float(node[1]), float(node[0])))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geom_json.get("coordinates"))
    return coords


def message_body(record: dict) -> str:
    """English message text, falling back to Norwegian when absent."""
    return (record.get("message_en") or record.get("message_no") or "").strip()


def message_id(record: dict, source: SourceConfig) -> str:
    number = (record.get("warningnumber") or "").strip()
    return f"{source.id_prefix} {number}".strip()


def _year_from_warningnumber(number: Optional[str]) -> Optional[int]:
    m = re.search(r"/(\d{2,4})\s*$", (number or "").strip())
    if not m:
        return None
    raw = m.group(1)
    return int(raw) if len(raw) == 4 else 2000 + int(raw)


def build_message(record: dict, source: SourceConfig):
    """Build a NavwarnMessage from an API record, with authoritative id/DTG."""
    # The API returns mixed-case text; the parser's patterns expect the upper-case
    # form the legacy NAVTEX/HTML sources used.
    body = message_body(record).upper()
    dtg_str = dtg_from_timestamp(record.get("dtg_timestamp"))
    msg = navparser.NavwarnMessage.from_text(dtg_str, body)

    msg.msg_id = message_id(record, source) or msg.msg_id
    year = _year_from_warningnumber(record.get("warningnumber"))
    if year:
        msg.year = year
    if msg.dtg is None:
        msg.dtg = parse_api_timestamp(record.get("dtg_timestamp")) or parse_api_timestamp(
            record.get("updated_at")
        )
    if dtg_str:
        msg.raw_dtg = dtg_str

    wkt_coords = wkt_to_latlon_coords(record.get("position"))
    if wkt_coords:
        msg.coordinates = wkt_coords
    return msg


def record_to_feature(record: dict, source: SourceConfig) -> dict:
    """Convert one API record into a GeoJSON Feature."""
    msg = build_message(record, source)
    feature = msg.to_geojson_feature()
    props = feature["properties"]
    props["body"] = message_body(record)

    api_geom = wkt_to_geojson(record.get("position"))
    if api_geom:
        feature["geometry"] = api_geom
        props["geometry_kind"] = GEOM_KIND_MAP.get(
            api_geom.get("type", ""), props.get("geometry_kind")
        )
        props["radius_nm"] = None
        props["geometry_source"] = "api-wkt"
    else:
        props["geometry_source"] = "parsed-text"

    status = record.get("status")
    props.update(
        {
            "eventid": record.get("eventid"),
            "status": status,
            "eventclass": record.get("eventclass") or source.eventclass,
            "updated_at": iso_utc(record.get("updated_at")),
            "source": SOURCE_TAG,
            "summary": None,
        }
    )
    if source is NAVCO:
        props.update(
            {
                "location": record.get("location"),
                "norwegianchart": record.get("norwegianchart"),
                "fdv_objectid": record.get("fdv_objectid") or record.get("fdv_id"),
                "message_no": record.get("message_no"),
            }
        )

    # A non-active warning has been withdrawn; its last update is the end of validity.
    if status and status.lower() != "active" and not props.get("valid_until"):
        props["valid_until"] = props["updated_at"]

    return feature


def safe_filename(msg_id: Optional[str]) -> str:
    return re.sub(r"[^\w\-]", "_", msg_id) if msg_id else "unknown_id"


def write_features(features: Sequence[dict], out_dir: Path) -> set:
    """Write each feature to its own file, rewriting only on change."""
    out_dir.mkdir(parents=True, exist_ok=True)
    active: set = set()
    for feature in features:
        filename = f"{safe_filename(feature.get('id'))}.json"
        active.add(filename)
        path = out_dir / filename
        payload = json.dumps(feature, ensure_ascii=False, indent=2) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == payload:
            continue
        path.write_text(payload, encoding="utf-8")
        logging.info("Wrote %s", path)
    return active


def run_source(
    source: SourceConfig,
    records: Optional[Sequence[dict]] = None,
    current_dir: Optional[Path] = None,
    history_dir: Optional[Path] = None,
) -> List[dict]:
    """Fetch (or accept) records for one endpoint and materialise feature files."""
    current_dir = Path(current_dir) if current_dir else CURRENT_DIR
    history_dir = Path(history_dir) if history_dir else HISTORY_DIR

    if records is None:
        records = fetch_records(source)
    save_raw_json(records, source, history_dir)

    features = [record_to_feature(r, source) for r in records]
    out_dir = current_dir / "navwarns"
    active = write_features(features, out_dir)

    cleanup.cleanup(active, out_dir, source.cleanup_glob, history_dir)

    current_dir.mkdir(parents=True, exist_ok=True)
    ts_file = current_dir / f".scrape_timestamp_{source.key}"
    ts_file.write_text(
        f"{datetime.datetime.now(datetime.timezone.utc).isoformat()}\n", encoding="utf-8"
    )

    logging.info("Processed %d %s warnings, %d active files", len(features), source.key, len(active))
    return features


def main(argv: Optional[Sequence[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        choices=["navareaxix", "coastal", "all"],
        default="all",
        help="Which endpoint(s) to scrape (default: all)",
    )
    args = ap.parse_args(argv)

    selected = SOURCES.values() if args.source == "all" else [SOURCES[args.source]]
    for source in selected:
        run_source(source)


if __name__ == "__main__":
    main()
