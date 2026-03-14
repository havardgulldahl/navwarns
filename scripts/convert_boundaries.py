#!/usr/bin/env python3
"""Download Norwegian maritime boundaries from BarentsWatch and
convert them to GeoJSON for the Leaflet map.

Source: https://www.barentswatch.no/api/v1/geodata/download/
       maritimeboundary/?format=OLEX

The OLEX file contains line segments for various maritime
boundaries (baselines, territorial waters, economic zones,
agreed delimitation lines).  Multiple route blocks may share
the same name — these represent separate segments of one
boundary and are grouped into MultiLineString features.

Output: docs/maritime_boundaries.geojson
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# Shared OLEX parsing helpers (inlined to avoid fragile cross-script
# imports when running directly vs. as a module).

# Regex for coordinate lines: LAT_MIN LON_MIN TIMESTAMP SYMBOL
COORD_LINE_RE = re.compile(r"^(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+\d+\s+\S+$")
# Regex for MTekst lines
MTEKST_RE = re.compile(r"^MTekst \d+:\s*(.*)$")


def olex_to_decimal_degrees(
    lat_minutes: float, lon_minutes: float
) -> Tuple[float, float]:
    """Convert OLEX decimal-minutes to decimal degrees."""
    return lat_minutes / 60.0, lon_minutes / 60.0


# ---------------- Configuration ----------------
API_URL = (
    "https://www.barentswatch.no/api/v1/geodata/download/"
    "maritimeboundary/?format=OLEX"
)
OUTPUT_PATH = Path("docs/maritime_boundaries.geojson")
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


@dataclass
class BoundarySegment:
    """A single parsed OLEX route block (line segment)."""

    name: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)
    coordinates: List[Tuple[float, float]] = field(default_factory=list)


def parse_boundary_olx(text: str) -> List[BoundarySegment]:
    """Parse maritime boundary OLEX text into segments.

    Each 'Rute ukjent' block becomes a BoundarySegment.
    Duplicate segments (same name + coordinates) are removed.
    """
    segments: List[BoundarySegment] = []
    current: Optional[BoundarySegment] = None
    mtekst_lines: List[Tuple[int, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line == "Rute ukjent":
            if current and current.coordinates:
                _apply_boundary_mtekst(current, mtekst_lines)
                segments.append(current)
            current = BoundarySegment()
            mtekst_lines = []
            continue

        if current is None:
            continue

        if (
            line in ("Ferdig forenklet", "Fikspos", "")
            or line.startswith("Rutetype")
            or line.startswith("Linjefarge")
        ):
            continue

        m = COORD_LINE_RE.match(line)
        if m:
            lat_min = float(m.group(1))
            lon_min = float(m.group(2))
            lat, lon = olex_to_decimal_degrees(lat_min, lon_min)
            current.coordinates.append((lat, lon))
            continue

        if line.startswith("Navn "):
            current.name = line[5:].strip()
            continue

        mt = MTEKST_RE.match(line)
        if mt:
            idx_match = re.match(r"^MTekst (\d+):", raw_line.strip())
            idx = int(idx_match.group(1)) if idx_match else 0
            mtekst_lines.append((idx, mt.group(1)))
            continue

    # Flush last segment
    if current and current.coordinates:
        _apply_boundary_mtekst(current, mtekst_lines)
        segments.append(current)

    # Deduplicate by name + coordinates
    seen: set[str] = set()
    unique: List[BoundarySegment] = []
    for s in segments:
        key = f"{s.name}|{s.coordinates}"
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _apply_boundary_mtekst(
    seg: BoundarySegment,
    lines: List[Tuple[int, str]],
) -> None:
    """Extract structured metadata from MTekst lines."""
    seen: set[Tuple[int, str]] = set()
    for idx, text in lines:
        if (idx, text) in seen:
            continue
        seen.add((idx, text))

        if ": " in text:
            key, _, value = text.partition(": ")
            key = key.strip()
            value = value.strip()
            if key == "Navn":
                seg.name = seg.name or value
            elif key not in seg.metadata:
                seg.metadata[key] = value
            else:
                seg.metadata[key] += " " + value


# Boundary type → line style hints for the map
BOUNDARY_STYLES: Dict[str, Dict] = {
    "Grunnlinje": {
        "color": "#2c7bb6",
        "weight": 1.5,
        "dashArray": "4,4",
    },
    "Territorialgrense": {
        "color": "#d7191c",
        "weight": 2,
        "dashArray": "",
    },
    "Fiskerigrense": {
        "color": "#fdae61",
        "weight": 2,
        "dashArray": "8,4",
    },
    "GrenseSjø": {
        "color": "#e542f5",
        "weight": 2,
        "dashArray": "6,3",
    },
}
DEFAULT_STYLE = {
    "color": "#756bb1",
    "weight": 2,
    "dashArray": "",
}


def _boundary_type(seg: BoundarySegment) -> str:
    """Infer boundary type from metadata or name."""
    bt = seg.metadata.get("Grensetype", "")
    if bt:
        return bt
    name_lower = seg.name.lower()
    if "grunnlinje" in name_lower:
        return "Grunnlinje"
    if "territorialgrense" in name_lower:
        return "Territorialgrense"
    if "nautiske mil" in name_lower:
        return "GrenseSjø"
    return ""


def segments_to_geojson(
    segments: List[BoundarySegment],
) -> dict:
    """Group segments by name and produce a FeatureCollection.

    Segments sharing the same name become a single
    MultiLineString feature.
    """
    grouped: Dict[str, List[BoundarySegment]] = defaultdict(list)
    for seg in segments:
        grouped[seg.name].append(seg)

    features: List[dict] = []
    for name, segs in grouped.items():
        lines = []
        for s in segs:
            line = [[lon, lat] for lat, lon in s.coordinates]
            if len(line) >= 2:
                lines.append(line)

        if not lines:
            continue

        if len(lines) == 1:
            geometry = {
                "type": "LineString",
                "coordinates": lines[0],
            }
        else:
            geometry = {
                "type": "MultiLineString",
                "coordinates": lines,
            }

        btype = _boundary_type(segs[0])
        style = BOUNDARY_STYLES.get(btype, DEFAULT_STYLE)

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": name,
                    "boundary_type": btype,
                    "status": segs[0].metadata.get("Status", ""),
                    "country": segs[0].metadata.get("Landkode", ""),
                    "style": style,
                },
                "geometry": geometry,
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


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
    logging.info("Fetching maritime boundaries from %s", API_URL)

    raw_gz = fetch_olx_gz()
    logging.info("Downloaded %d bytes (gzipped)", len(raw_gz))

    try:
        olx_text = gzip.decompress(raw_gz).decode("latin-1")
    except gzip.BadGzipFile:
        logging.error("Downloaded data is not valid gzip")
        sys.exit(1)

    segments = parse_boundary_olx(olx_text)
    logging.info("Parsed %d unique boundary segment(s)", len(segments))

    geojson = segments_to_geojson(segments)
    logging.info(
        "Grouped into %d boundary feature(s)",
        len(geojson["features"]),
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    logging.info("Wrote %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
