import re
import sys
import argparse
import json
from dataclasses import dataclass, field
import math
from datetime import datetime
from dateutil import parser as dtparser  # still used as fallback
from typing import List, Tuple, Optional

# --- Regex patterns ---
DTG_PATTERN = re.compile(r"(\d{6}Z [A-Z]{3} \d{2})")  # generic pattern
DTG_LINE_PATTERN = re.compile(r"^\d{6}Z [A-Z]{3} \d{2}\s*$", re.MULTILINE)
MSG_ID_PATTERN = re.compile(
    r"(HYDROARC \d+/\d+(?:\([^)]+\))?|NAVAREA [A-Z0-9]+ \d+/\d+)"
)
# Coordinate pair pattern supporting both DM (DD-MM.mm) and DMS (DD-MM-SS.ss) forms.
_LAT_PART = r"\d{2,3}-(?:\d{2}\.\d+|\d{2}-\d{2}(?:\.\d+)?)"
_LON_PART = r"\d{3}-(?:\d{2}\.\d+|\d{2}-\d{2}(?:\.\d+)?)"
COORD_PATTERN = re.compile(rf"({_LAT_PART}[NS])\s+({_LON_PART}[EW])")
# Expanded cancellation recognition:
#  - HYDROARC X/Y
#  - plain X/Y (e.g. 47/18)
#  - THIS MSG <DTG>
#  - THIS MESSAGE <DD MON YY>
#  - THIS MSG <DD MON YY>
CANCEL_PATTERN = re.compile(
    r"CANCEL ("  # capture the target only
    r"HYDROARC \d+/\d+"  # structured HYDROARC
    r"|\d+/\d+"  # plain number/year
    r"|THIS (?:MSG|MESSAGE) \d{6}Z [A-Z]{3} \d{2}"  # DTG form with Z
    r"|THIS (?:MSG|MESSAGE) \d{6} UTC [A-Z]{3} \d{2}"  # DTG without Z + UTC
    r"|THIS (?:MSG|MESSAGE) \d{2} [A-Z]{3} (?:\d{2}|\d{4})"  # date only 2 or 4-digit year
    r")"
)


# --- Data container ---
@dataclass
class NavwarnMessage:
    dtg: Optional[datetime]
    raw_dtg: str
    msg_id: Optional[str]
    coordinates: List[Tuple[float, float]] = field(default_factory=list)
    cancellations: List[str] = field(default_factory=list)
    hazard_type: Optional[str] = None
    geometry: Optional[str] = None  # one of: point, linestring, polygon, circle
    radius: Optional[float] = None  # miles / NM (unit ambiguous in source)
    body: str = ""

    # --- GeoJSON helpers ---
    def geojson_geometry(self, circle_segments: int = 72) -> dict:
        """Return a GeoJSON geometry object derived from parsed coordinates & geometry metadata.

        circle: approximated as a Polygon with given number of segments (>=8) around center point (first coord).
        linestring: LineString of provided coordinate sequence.
        polygon: Polygon (single outer ring) closing the ring if necessary.
        point (or fallback): Point (first coordinate) or empty Point when none.
        """
        coords = self.coordinates or []
        if not coords:
            return {"type": "Point", "coordinates": []}
        geom_type = (self.geometry or "").lower()
        if geom_type == "circle" and self.radius and len(coords) >= 1:
            # Approximate circle; assume radius in nautical miles -> degrees of latitude = r/60.
            lat_c, lon_c = coords[0]
            segments = max(8, circle_segments)
            ring: List[List[float]] = []
            # Convert NM to degrees lat; lon degrees scaled by cos(lat)
            deg_lat = self.radius / 60.0
            cos_lat = math.cos(math.radians(lat_c)) or 1e-9
            deg_lon = deg_lat / cos_lat
            for i in range(segments):
                theta = 2 * math.pi * (i / segments)
                dy = math.sin(theta) * deg_lat
                dx = math.cos(theta) * deg_lon
                ring.append([lon_c + dx, lat_c + dy])
            # Close ring
            ring.append(ring[0])
            return {"type": "Polygon", "coordinates": [ring]}
        if geom_type == "linestring" and len(coords) >= 2:
            return {
                "type": "LineString",
                "coordinates": [[lon, lat] for (lat, lon) in coords],
            }
        if geom_type == "polygon" and len(coords) >= 3:
            ring = [[lon, lat] for (lat, lon) in coords]
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            return {"type": "Polygon", "coordinates": [ring]}
        # Default / point
        lat, lon = coords[0]
        return {"type": "Point", "coordinates": [lon, lat]}

    def to_geojson_feature(self) -> dict:
        return {
            "type": "Feature",
            "id": self.msg_id or None,
            "geometry": self.geojson_geometry(),
            "properties": {
                "dtg": self.dtg.isoformat() if self.dtg else None,
                "raw_dtg": self.raw_dtg,
                "msg_id": self.msg_id,
                "cancellations": self.cancellations,
                "hazard_type": self.hazard_type,
                "geometry_kind": self.geometry,
                "radius_nm": self.radius,
                "body": self.body,
            },
        }

    @classmethod
    def from_text(cls, dtg_str: str, body: str) -> "NavwarnMessage":
        """Factory method: build a NavwarnMessage from raw DTG + message body."""
        dtg = parse_dtg(dtg_str)
        msg_id = parse_msg_id(body)
        coords = parse_coordinates(body)
        cancels = parse_cancellations(body)
        hazard = classify_hazard(body)
        geometry, radius = analyze_geometry(body, coords)
        return cls(
            dtg=dtg,
            raw_dtg=dtg_str,
            msg_id=msg_id,
            coordinates=coords,
            cancellations=cancels,
            hazard_type=hazard,
            geometry=geometry,
            radius=radius,
            body=body,
        )


# --- Helper functions ---
def parse_dtg(dtg_str: str) -> Optional[datetime]:
    """Parse DTG of form DDHHMMZ MON YY into a datetime (UTC naive).

    Falls back to dateutil if manual parse fails.
    """
    m = re.match(r"^(\d{2})(\d{2})(\d{2})Z ([A-Z]{3}) (\d{2})$", dtg_str.strip())
    if m:
        day, hour, minute, mon_str, year2 = m.groups()
        day = int(day)
        hour = int(hour)
        minute = int(minute)
        year = 2000 + int(year2)
        try:
            month = datetime.strptime(mon_str, "%b").month
            return datetime(year, month, day, hour, minute)
        except ValueError:
            pass
    # Fallback
    try:
        return dtparser.parse(dtg_str, dayfirst=True, yearfirst=False)
    except Exception:
        return None


def parse_msg_id(body: str) -> Optional[str]:
    match = MSG_ID_PATTERN.search(body)
    if match:
        return match.group(1)
    # Sometimes the identifier is the very first line with trailing spaces not included in body (fallback path)
    first_line = body.splitlines()[0].strip() if body.strip() else ""
    m2 = MSG_ID_PATTERN.match(first_line)
    if m2:
        return m2.group(1)
    return None


def coord_to_decimal(coord: str) -> Optional[float]:
    """Convert coordinate token to signed decimal degrees.

    Supports:
      - DM:  DD-MM.mmH (minutes decimal)
      - DMS: DD-MM-SS.ssH (seconds decimal)
    where H is hemisphere N/S/E/W.
    """
    # DMS first
    m_dms = re.match(r"^(\d+)-(\d+)-(\d+(?:\.\d+)?)([NSEW])$", coord)
    if m_dms:
        deg_s, min_s, sec_s, hemi = m_dms.groups()
        deg_i = int(deg_s)
        min_i = int(min_s)
        sec_f = float(sec_s)
        decimal = deg_i + (min_i / 60.0) + (sec_f / 3600.0)
    else:
        m_dm = re.match(r"^(\d+)-(\d+\.\d+)([NSEW])$", coord)
        if not m_dm:
            return None
        deg_s, min_s, hemi = m_dm.groups()
        deg_i = int(deg_s)
        min_f = float(min_s)
        decimal = deg_i + (min_f / 60.0)
    if hemi in ["S", "W"]:
        decimal = -decimal
    return decimal


def parse_coordinates(body: str) -> List[Tuple[float, float]]:
    coords = []
    for lat, lon in COORD_PATTERN.findall(body):
        lat_dec = coord_to_decimal(lat)
        lon_dec = coord_to_decimal(lon)
        if lat_dec is not None and lon_dec is not None:
            coords.append((lat_dec, lon_dec))
    return coords


def parse_cancellations(body: str) -> List[str]:
    """Extract cancellation references.

    We purposely broaden parsing to capture simple NAVAREA style references like
    'CANCEL 47/18', multi-word forms like 'CANCEL THIS MESSAGE 01 JAN 19' and the
    existing HYDROARC / DTG formats. Returned values are the captured target
    strings without the leading 'CANCEL '.
    """
    cancels: List[str] = []
    # Primary regex (already excludes the leading 'CANCEL ' via group)
    cancels.extend(CANCEL_PATTERN.findall(body))
    # Additional heuristic: for any line containing CANCEL, pull all token forms NNN/YY
    for line in body.splitlines():
        if "CANCEL" in line.upper():
            for m in re.findall(r"\b(\d+/\d+)\b", line):
                # Skip if a longer token already captured (e.g., HYDROARC 134/25)
                if any(c.endswith(m) for c in cancels):
                    continue
                if m not in cancels:
                    cancels.append(m)
    # Heuristic: drop any plain number/year token that corresponds to the message id in body
    msg_id_match = re.search(r"HYDROARC (\d+/\d+)", body)
    if msg_id_match:
        own_suffix = msg_id_match.group(1)
        cancels = [c for c in cancels if c != own_suffix]
    return cancels


def classify_hazard(body: str) -> Optional[str]:
    """Very simple keyword-based hazard classifier."""
    text = body.upper()
    if "DERELICT" in text and ("ADRIFT" in text or "M/V" in text or "VESSEL" in text):
        return "derelict vessel"
    if "SHOAL" in text:
        return "shoals"
    if "RACON" in text and (
        "INOPERATIVE" in text or "UNLIT" in text or "DAMAGED" in text
    ):
        return "aid to navigation outage"
    if "ROCKET" in text or "HAZARDOUS OPERATIONS" in text:
        return "hazardous operations"
    if "MOORING" in text:
        return "scientific mooring"
    if "ENC" in text and "CANCELLED" in text:
        return "chart advisory"
    return "general"


def analyze_geometry(
    body: str, coords: List[Tuple[float, float]]
) -> Tuple[str, Optional[float]]:
    """Infer geometry type (point/linestring/polygon/circle) and radius if applicable.

    Heuristics:
      - Circle: text contains '<number> (MILE|NM) RADIUS' or 'WITHIN <number> ... RADIUS' and at least one coordinate.
      - Linestring: phrase 'ALONG LINE' OR many (>=5) coordinates and not circle/polygon.
      - Polygon: phrase 'AREA BOUNDED BY' (>=3 coords) OR exactly 4 distinct coords in area context.
      - Point: fallback when one coordinate only.
    Radius unit not normalized; numeric value returned.
    """
    text = body.upper()
    radius: Optional[float] = None
    circle_pattern = re.search(
        r"(WITHIN\s+)?(\d+(?:\.\d+)?)\s*(NM|NAUTICAL MILES?|MILES?|MILE)\s+RADIUS", text
    )
    geometry = None
    if circle_pattern and coords:
        try:
            radius = float(circle_pattern.group(2))
            geometry = "circle"
        except ValueError:
            radius = None
    if geometry != "circle":
        if ("AREA BOUNDED BY" in text or "AREA BOUNDED" in text) and len(coords) >= 3:
            geometry = "polygon"
        elif "ALONG LINE" in text or (len(coords) >= 5 and len(coords) != 4):
            geometry = "linestring"
        elif len(coords) > 1:
            # If exactly 4 coordinates and no keywords, assume polygon only if repeating first not required
            if len(coords) == 4 and ("AREA" in text and "BOUND" in text):
                geometry = "polygon"
            else:
                geometry = "linestring" if len(coords) > 2 else "point"
    if not geometry:
        geometry = "point" if coords else "point"
    return geometry, radius


# --- Top-level parser ---
def parse_navwarns(text: str) -> List[NavwarnMessage]:
    """Parse full NAVWARN bulletin text into messages.

    Only DTG tokens that appear on their own line start a new message; this
    prevents splitting on embedded cancellation references like 'CANCEL THIS MSG 222359Z AUG 25.'
    """
    lines = text.strip().splitlines()
    current_dtg: Optional[str] = None
    current_body_lines: List[str] = []
    messages: List[NavwarnMessage] = []

    def flush():
        if current_dtg is not None:
            body = "\n".join(current_body_lines).strip()
            msg = NavwarnMessage.from_text(current_dtg, body)
            messages.append(msg)

    for raw_line in lines:
        line = raw_line.rstrip()
        if DTG_LINE_PATTERN.match(line):
            # Start of a new message
            if current_dtg is not None:
                flush()
                current_body_lines = []  # type: ignore
            current_dtg = line.strip()
        else:
            current_body_lines.append(line)
    # Flush last
    flush()

    # Fallback: If no DTG-triggered messages were found but text is non-empty,
    # treat the entire blob as a single message (NAVAREA style without explicit DTG line)
    if not messages and text.strip():
        first_line = lines[0].strip() if lines else ""
        body = "\n".join(
            lines
        ).strip()  # include full text so msg id regex can hit first line
        msg = NavwarnMessage.from_text(first_line, body)
        messages.append(msg)

    # If multiple messages, normalize msg_id by stripping trailing parentheses group
    if len(messages) > 1:
        for m in messages:
            if m.msg_id:
                m.msg_id = re.sub(r"\([^)]*\)$", "", m.msg_id)
    return messages


# --- Example usage ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse NAVWARN messages from a file or stdin and output structured data."
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="Input file path (if omitted, read from stdin)",
    )
    parser.add_argument(
        "-j", "--json", action="store_true", help="Output messages as JSON list"
    )
    args = parser.parse_args()

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            print(f"Error reading {args.file}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            # No file and stdin is a TTY: prompt user
            print("Reading NAVWARN text from stdin (Ctrl-D to end)...", file=sys.stderr)
        text = sys.stdin.read()

    messages = parse_navwarns(text)

    if args.json:
        serializable = [
            {
                "dtg": m.dtg.isoformat() if m.dtg else None,
                "raw_dtg": m.raw_dtg,
                "msg_id": m.msg_id,
                "coordinates": m.coordinates,
                "cancellations": m.cancellations,
                "hazard_type": m.hazard_type,
                "body": m.body,
            }
            for m in messages
        ]
        json.dump(serializable, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for m in messages:
            dtg_disp = m.dtg.isoformat() if m.dtg else m.raw_dtg
            print(
                f"{dtg_disp} | {m.msg_id or 'NO-ID'} | {m.hazard_type} | {len(m.coordinates)} coords | {len(m.cancellations)} cancellations"
            )
