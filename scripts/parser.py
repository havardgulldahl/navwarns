import re
import sys
import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from dateutil import parser as dtparser  # still used as fallback
from typing import List, Tuple, Optional

# --- Regex patterns ---
DTG_PATTERN = re.compile(r"(\d{6}Z [A-Z]{3} \d{2})")  # generic pattern
DTG_LINE_PATTERN = re.compile(r"^\d{6}Z [A-Z]{3} \d{2}\s*$", re.MULTILINE)
MSG_ID_PATTERN = re.compile(r"(HYDROARC \d+/\d+(?:\([^)]+\))?)")
COORD_PATTERN = re.compile(r"(\d{2,3}-\d{2}\.\d{2}[NS])\s+(\d{3}-\d{2}\.\d{2}[EW])")
CANCEL_PATTERN = re.compile(r"CANCEL (HYDROARC \d+/\d+|THIS MSG \d{6}Z [A-Z]{3} \d{2})")


# --- Data container ---
@dataclass
class NavwarnMessage:
    dtg: Optional[datetime]
    raw_dtg: str
    msg_id: Optional[str]
    coordinates: List[Tuple[float, float]] = field(default_factory=list)
    cancellations: List[str] = field(default_factory=list)
    hazard_type: Optional[str] = None
    body: str = ""

    @classmethod
    def from_text(cls, dtg_str: str, body: str) -> "NavwarnMessage":
        """Factory method: build a NavwarnMessage from raw DTG + message body."""
        dtg = parse_dtg(dtg_str)
        msg_id = parse_msg_id(body)
        coords = parse_coordinates(body)
        cancels = parse_cancellations(body)
        hazard = classify_hazard(body)
        return cls(
            dtg=dtg,
            raw_dtg=dtg_str,
            msg_id=msg_id,
            coordinates=coords,
            cancellations=cancels,
            hazard_type=hazard,
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
    return match.group(1) if match else None


def coord_to_decimal(coord: str) -> Optional[float]:
    match = re.match(r"(\d+)-(\d+\.\d+)([NSEW])", coord)
    if not match:
        return None
    deg, minutes, hemi = match.groups()
    deg = int(deg)
    minutes = float(minutes)
    decimal = deg + (minutes / 60)
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
    return CANCEL_PATTERN.findall(body)


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
