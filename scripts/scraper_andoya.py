#!/usr/bin/env python3
"""Downloader and parser for Andøya firing range danger zones.

Source: BarentsWatch anonymous OLEX export (gzipped .olx).
URL: https://www.barentswatch.no/bwapi/v1/geodata/download/
     anonymous-andoya-firing-range-danger-zone/?format=OLEX

The .olx file uses OLEX chart-plotter format with coordinates
stored as decimal minutes from the equator (latitude) and
Greenwich (longitude).  Each route block defines a polygon
(danger area) with associated metadata text.

Output: GeoJSON Feature files in current/navwarns/, one per
danger zone, following the same conventions as the other
scrapers.
"""

from __future__ import annotations

import datetime
import gzip
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from shapely.geometry import Polygon, mapping

try:
    from . import cleanup  # type: ignore
except ImportError:  # running as a script
    import importlib.util as _ilu

    _cleanup_path = Path(__file__).resolve().parent / "cleanup.py"
    _spec_clean = _ilu.spec_from_file_location("cleanup", _cleanup_path)
    cleanup = _ilu.module_from_spec(_spec_clean)  # type: ignore
    assert _spec_clean and _spec_clean.loader
    _spec_clean.loader.exec_module(cleanup)  # type: ignore

# ---------------- Configuration ----------------
API_URL = (
    "https://www.barentswatch.no/bwapi/v1/geodata/download/"
    "anonymous-andoya-firing-range-danger-zone/?format=OLEX"
)
CURRENT_DIR = Path("current")
OUTPUT_DIR = CURRENT_DIR / "navwarns"
HISTORY_DIR = Path(f"history/{datetime.datetime.now().strftime('%Y')}/ANDOYA")
REQUEST_TIMEOUT = 60
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

# Regex for coordinate lines:  LAT_MINUTES LON_MINUTES TIMESTAMP SYMBOL
COORD_LINE_RE = re.compile(r"^(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+\d+\s+\S+$")
# Regex for MTekst lines
MTEKST_RE = re.compile(r"^MTekst \d+:\s*(.*)$")


@dataclass
class OlexRoute:
    """A parsed OLEX route block (danger area polygon)."""

    name: str = ""
    area_name: str = ""
    description_no: str = ""
    description_en: str = ""
    coordinates: List[Tuple[float, float]] = field(default_factory=list)


def olex_to_decimal_degrees(
    lat_minutes: float, lon_minutes: float
) -> Tuple[float, float]:
    """Convert OLEX decimal-minutes to decimal degrees.

    OLEX stores coordinates as total decimal minutes from equator
    (latitude) and Greenwich meridian (longitude).
    """
    return lat_minutes / 60.0, lon_minutes / 60.0


def parse_olx(text: str) -> List[OlexRoute]:
    """Parse OLEX .olx text into a list of route blocks.

    Each route block begins with 'Rute ukjent' and contains
    coordinate lines, a name, and MTekst metadata lines.
    Duplicate route blocks (same area_name and coordinates)
    are deduplicated.
    """
    routes: List[OlexRoute] = []
    current: Optional[OlexRoute] = None
    mtekst_lines: List[Tuple[int, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line == "Rute ukjent":
            # Flush previous route
            if current and current.coordinates:
                _apply_mtekst(current, mtekst_lines)
                routes.append(current)
            current = OlexRoute()
            mtekst_lines = []
            continue

        if current is None:
            continue

        # Skip header lines
        if line in (
            "Ferdig forenklet",
            "Fikspos",
            "Rutetype Areal",
            "",
        ) or line.startswith("Linjefarge"):
            continue

        # Coordinate line
        m = COORD_LINE_RE.match(line)
        if m:
            lat_min = float(m.group(1))
            lon_min = float(m.group(2))
            lat, lon = olex_to_decimal_degrees(lat_min, lon_min)
            current.coordinates.append((lat, lon))
            continue

        # Name line
        if line.startswith("Navn "):
            current.name = line[5:].strip()
            continue

        # MTekst line
        mt = MTEKST_RE.match(line)
        if mt:
            idx_match = re.match(r"^MTekst (\d+):", raw_line.strip())
            idx = int(idx_match.group(1)) if idx_match else 0
            mtekst_lines.append((idx, mt.group(1)))
            continue

    # Flush last route
    if current and current.coordinates:
        _apply_mtekst(current, mtekst_lines)
        routes.append(current)

    # Deduplicate: keep only unique (area_name, coordinates) combos
    seen: set[str] = set()
    unique: List[OlexRoute] = []
    for r in routes:
        key = f"{r.area_name}|{r.coordinates}"
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _apply_mtekst(
    route: OlexRoute,
    lines: List[Tuple[int, str]],
) -> None:
    """Extract area name and descriptions from MTekst lines.

    The text block contains Norwegian text first (indices 0-15
    typically) then English text (indices 16+).  The area name is
    extracted from lines containing 'Navn:' or 'Name:'.
    MTekst blocks may be repeated; deduplicate by (index, text).
    """
    seen: set[Tuple[int, str]] = set()
    no_parts: List[str] = []
    en_parts: List[str] = []
    for idx, text in lines:
        if (idx, text) in seen:
            continue
        seen.add((idx, text))

        # Detect area name
        if text.startswith("Navn: "):
            route.area_name = text[6:].strip()
        elif text.startswith("Name: "):
            route.area_name = route.area_name or text[6:].strip()

        # Split Norwegian (low indices) vs English (high indices)
        if text.startswith("Name: ") or text.startswith("Description:"):
            en_parts.append(text)
        elif idx >= 16:
            en_parts.append(text)
        elif text.startswith("Navn: ") or text.startswith("Beskrivelse:"):
            no_parts.append(text)
        else:
            no_parts.append(text)

    route.description_no = " ".join(no_parts).strip()
    route.description_en = " ".join(en_parts).strip()


def route_to_geojson_feature(route: OlexRoute) -> dict:
    """Convert an OlexRoute to a GeoJSON Feature dict."""
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", route.area_name)
    feature_id = f"ANDOYA_{safe_name}"

    # Build polygon geometry (lon, lat order for GeoJSON)
    coords = route.coordinates
    if len(coords) >= 3:
        ring = [(lon, lat) for lat, lon in coords]
        # Close ring if not already closed
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        geom = mapping(Polygon(ring))
    else:
        geom = None

    now = datetime.datetime.now(datetime.timezone.utc)
    body_text = route.description_en or route.description_no

    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": geom,
        "properties": {
            "dtg": now.isoformat(),
            "raw_dtg": now.strftime("%d%H%MZ %b %y").upper(),
            "msg_id": feature_id,
            "year": now.year,
            "cancellations": [],
            "hazard_type": "firing_exercises",
            "geometry_kind": "polygon",
            "radius_nm": None,
            "body": body_text,
            "cancel_date": None,
            "valid_from": now.isoformat(),
            "valid_until": None,
            "summary": None,
            "source": "BarentsWatch/Andoya",
            "area_name": route.area_name,
            "name": route.name,
        },
    }


def feature_filename(feature_id: str) -> str:
    """Derive a safe filename from a feature id."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", feature_id)
    return f"{safe}.json"


# ---- Download ----


def fetch_olx_gz(url: str = API_URL) -> bytes:
    """Download the gzipped OLX file with retries."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            logging.warning(
                "Request error (attempt %d/%d): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF**attempt)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts")


def main() -> None:
    """Download Andøya OLX, parse, and write GeoJSON features."""
    logging.info("Fetching Andøya danger zones from %s", API_URL)

    raw_gz = fetch_olx_gz()
    logging.info("Downloaded %d bytes (gzipped)", len(raw_gz))

    try:
        olx_text = gzip.decompress(raw_gz).decode("latin-1")
    except gzip.BadGzipFile:
        logging.error("Downloaded data is not valid gzip")
        sys.exit(1)

    # Save raw OLX text to history for archival
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    raw_filename = datetime.datetime.now().strftime("ANDOYA_%Y-%m-%d.olx")
    raw_path = HISTORY_DIR / raw_filename
    raw_path.write_text(olx_text, encoding="latin-1")
    logging.info("Saved raw OLX to %s", raw_path)

    routes = parse_olx(olx_text)
    logging.info("Parsed %d unique danger area(s)", len(routes))

    if not routes:
        logging.warning("No danger areas found in OLX data")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    active_filenames: set[str] = set()

    for route in routes:
        feat = route_to_geojson_feature(route)
        fname = feature_filename(feat["id"])
        active_filenames.add(fname)
        filepath = OUTPUT_DIR / fname

        # Always overwrite — the file content changes
        # when the danger area schedule is updated
        with filepath.open("w", encoding="utf-8") as f:
            json.dump(feat, f, ensure_ascii=False, indent=2)
        logging.info("Wrote %s", filepath)

    # Move features no longer active to history
    cleanup.cleanup(
        active_filenames,
        OUTPUT_DIR,
        "ANDOYA_*.json",
    )

    # Write scrape timestamp
    ts_file = CURRENT_DIR / ".scrape_timestamp_ANDOYA"
    with ts_file.open("w", encoding="utf-8") as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n")

    logging.info("Done: %d danger area(s) written", len(active_filenames))


if __name__ == "__main__":
    main()
