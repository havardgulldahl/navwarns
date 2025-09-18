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
# Message identifier pattern. Supports:
#  - HYDROARC 123/24 (optionally with parentheses suffix)
#  - NAVAREA A 123/24 (existing)
#  - NAVAREA XX 112/25 (two-letter designators like 'XX' newly added)
#    (The previous pattern already allowed multiple alphanumerics, but we keep this
#     comment to document the explicit requirement.)
MSG_ID_PATTERN = re.compile(
    r"(HYDROARC \d+/\d+(?:\([^)]+\))?|NAVAREA [A-Z0-9]{1,3} \d+/\d+)"
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
    geometry: Optional[str] = (
        None  # one of: point, linestring, polygon, circle, multipoint
    )
    radius: Optional[float] = None  # miles / NM (unit ambiguous in source)
    groups: List[List[Tuple[float, float]]] = field(default_factory=list)
    body: str = ""
    year: Optional[int] = None  # four-digit year inferred from msg_id or dtg

    # --- GeoJSON helpers ---
    def geojson_geometry(self, circle_segments: int = 72) -> Optional[dict]:
        """Return a GeoJSON geometry object derived from parsed coordinates & geometry metadata.

        circle: approximated as a Polygon with given number of segments (>=8) around center point (first coord).
        linestring: LineString of provided coordinate sequence.
        polygon: Polygon (single outer ring) closing the ring if necessary.
        point (or fallback): Point (first coordinate) or empty Point when none.
        """
        coords = self.coordinates or []
        if not coords:
            return None  # null geometry for info-only
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
        if geom_type == "multipoint" and len(coords) >= 2:
            return {
                "type": "MultiPoint",
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
        geom = self.geojson_geometry()
        return {
            "type": "Feature",
            "id": self.msg_id or None,
            "geometry": geom,
            "properties": {
                "dtg": self.dtg.isoformat() if self.dtg else None,
                "raw_dtg": self.raw_dtg,
                "msg_id": self.msg_id,
                "year": self.year,
                "cancellations": self.cancellations,
                "hazard_type": self.hazard_type,
                "geometry_kind": self.geometry,
                "radius_nm": self.radius,
                "body": self.body,
            },
        }

    def to_geojson_features(self) -> List[dict]:
        if not self.groups or len(self.groups) <= 1 or self.geometry == "circle":
            return [self.to_geojson_feature()]
        # Multiple groups -> split into separate features
        kw = self.body.upper()
        area_hint = any(k in kw for k in ["AREA", "BOUNDED", "WITHIN", "RADIUS"])
        feats: List[dict] = []
        for idx, grp in enumerate(self.groups):
            if not grp:
                continue
            if len(grp) == 1:
                lat, lon = grp[0]
                geom = {"type": "Point", "coordinates": [lon, lat]}
            elif len(grp) >= 3 and area_hint:
                ring = [[lon, lat] for (lat, lon) in grp]
                if ring[0] != ring[-1]:
                    ring.append(ring[0])
                geom = {"type": "Polygon", "coordinates": [ring]}
            elif len(grp) >= 2 and self.geometry == "linestring":
                geom = {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for (lat, lon) in grp],
                }
            else:
                if len(grp) > 1:
                    geom = {
                        "type": "MultiPoint",
                        "coordinates": [[lon, lat] for (lat, lon) in grp],
                    }
                else:
                    lat, lon = grp[0]
                    geom = {"type": "Point", "coordinates": [lon, lat]}
            feats.append(
                {
                    "type": "Feature",
                    "id": f"{self.msg_id or 'MSG'}#grp{idx+1}",
                    "geometry": geom,
                    "properties": {
                        "parent_id": self.msg_id,
                        "group_index": idx + 1,
                        "dtg": self.dtg.isoformat() if self.dtg else None,
                        "raw_dtg": self.raw_dtg,
                        "year": self.year,
                        "hazard_type": self.hazard_type,
                        "body": self.body,
                    },
                }
            )
        return feats or [self.to_geojson_feature()]

    @classmethod
    def from_text(cls, dtg_str: str, body: str) -> "NavwarnMessage":
        """Factory method: build a NavwarnMessage from raw DTG + message body."""
        dtg = parse_dtg(dtg_str)
        msg_id = parse_msg_id(body)
        coords = parse_coordinates(body)
        cancels = parse_cancellations(body)
        hazard = classify_hazard(body)
        geometry, radius = analyze_geometry(body, coords)
        groups = parse_coordinate_groups(body)
        year = extract_year(msg_id, dtg)
        return cls(
            dtg=dtg,
            raw_dtg=dtg_str,
            msg_id=msg_id,
            coordinates=coords,
            cancellations=cancels,
            hazard_type=hazard,
            geometry=geometry,
            radius=radius,
            groups=groups,
            body=body,
            year=year,
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


def parse_coordinate_groups(body: str) -> List[List[Tuple[float, float]]]:
    """Split coordinates into enumerated groups (A., B., 1., 2., etc.)."""
    lines = body.splitlines()
    groups: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    enum_pattern = re.compile(r"^\s*(?:[A-Z]|\d{1,2})\.")
    for raw in lines:
        line = raw.strip()
        if enum_pattern.match(line):
            if current:
                groups.append(current)
                current = []
        for lat, lon in COORD_PATTERN.findall(line):
            lat_dec = coord_to_decimal(lat)
            lon_dec = coord_to_decimal(lon)
            if lat_dec is not None and lon_dec is not None:
                current.append((lat_dec, lon_dec))
    if current:
        groups.append(current)
    return groups


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
    """Infer geometry kind and possible radius.

    Added heuristics:
      - multipoint: list-style enumerations (1. 2. 3.) with multiple coordinates and feature keywords.
      - polygon: explicit 'AREA BOUNDED' or first ~= last closure or 4 points with boundary terms.
      - linestring: 'ALONG LINE' or many points not otherwise polygon/multipoint.
      - circle: radius phrase.
      - empty: no coords -> still return 'point' but renderer will create null geometry.
    """
    text = body.upper()
    radius: Optional[float] = None
    circle_pattern = re.search(
        r"(WITHIN\s+)?(\d+(?:\.\d+)?)\s*(NM|NAUTICAL MILES?|MILES?|MILE)\s+RADIUS", text
    )
    geometry: Optional[str] = None
    if circle_pattern and coords:
        try:
            radius = float(circle_pattern.group(2))
            geometry = "circle"
        except ValueError:
            pass
    if geometry != "circle":
        # Multipoint: enumerated list and feature nouns
        if len(coords) >= 2 and re.search(r"\b1\.\s", text):
            feature_terms = [
                "WELL",
                "BUOY",
                "HEAD",
                "PLATFORM",
                "STATION",
                "LIGHT",
                "BEACON",
            ]
            if any(term in text for term in feature_terms):
                geometry = "multipoint"
        # Polygon by keywords
        if (
            not geometry
            and ("AREA BOUNDED BY" in text or "AREA BOUNDED" in text)
            and len(coords) >= 3
        ):
            geometry = "polygon"
        # Closed ring
        if not geometry and len(coords) >= 4:
            f_lat, f_lon = coords[0]
            l_lat, l_lon = coords[-1]
            if abs(f_lat - l_lat) < 1e-4 and abs(f_lon - l_lon) < 1e-4:
                geometry = "polygon"
        # Linestring for many points (cable, track)
        if not geometry and (
            "ALONG LINE" in text or (len(coords) >= 5 and len(coords) != 4)
        ):
            geometry = "linestring"
        # Fallback resolution
        if not geometry and len(coords) > 1:
            if len(coords) == 4 and ("AREA" in text and "BOUND" in text):
                geometry = "polygon"
            else:
                geometry = "linestring" if len(coords) > 2 else "point"
    if not geometry:
        geometry = "point" if coords else "point"
    return geometry, radius


def extract_year(msg_id: Optional[str], dtg: Optional[datetime]) -> Optional[int]:
    """Infer four-digit year from msg_id suffix (e.g., HYDROARC 136/25 -> 2025) or dtg.

    Rules:
      - If msg_id ends with /YY where YY are digits, map 00-79 -> 2000-2079, 80-99 -> 1980-1999 (assumption).
      - Else fall back to dtg.year if available.
    """
    if msg_id:
        m = re.search(r"/(\d{2})(?:\b|\D*$)", msg_id)
        if m:
            yy = int(m.group(1))
            if 0 <= yy <= 79:
                return 2000 + yy
            else:
                return 1900 + yy
    if dtg:
        return dtg.year
    return None


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
                "year": m.year,
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
