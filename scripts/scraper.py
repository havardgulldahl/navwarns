"""Download, scrape and parse NavWarn (SMAPS category 14) messages from NGA MSI.

Process:
1. Hit API endpoint that yields XML listing active SMAPS records for NAVAREA C (Arctic) category 14.
2. Iterate each <smapsActiveEntity><msgText> block.
3. Parse its free-text portion (which itself can contain multiple NAVWARN messages) using parser.parse_navwarns.
4. Write each parsed message to current/ directory as JSON (one file per NAVWARN msg_id+dtg hash).

This keeps idempotency: existing files are skipped unless --force is used.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Any, List
import requests
from xml.etree import ElementTree as ET

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

API_URL = "https://msi.nga.mil/api/publications/smaps?navArea=C&status=active&category=14&output=xml"
CURRENT_DIR = Path("current")
OUTPUT_DIR = CURRENT_DIR / "navwarns"


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_xml(url: str = API_URL) -> str:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text


def extract_msg_text_blocks(xml_text: str) -> Iterable[str]:
    """Yield free-text NAVWARN bulletin blocks from the original SMAPS XML format."""
    root = ET.fromstring(xml_text)
    for ent in root.findall("smapsActiveEntity"):
        msg_el = ent.find("msgText")
        if msg_el is not None and (text := msg_el.text):
            yield text.strip()


def message_filename(msg: Any) -> str:
    base_id = msg.msg_id or "NOID"
    import re as _re

    safe = _re.sub(r"[^A-Za-z0-9_.-]+", "_", base_id)
    return f"{safe}.json"


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


def parse_broadcast_warn_xml(
    xml_text: str,
) -> List[Any]:  # returns list[NavwarnMessage]
    """Parse historical broadcast-warn XML into NavwarnMessage objects.

    Each child element (e.g. <broadcastWarnCancelledEntity>) is a single
    message with structured metadata fields and a <text> body.
    """
    root = ET.fromstring(xml_text)
    if root.tag != "broadcast-warn":
        return []
    messages: List[Any] = []
    for ent in list(root):
        if not ent.tag.endswith("Entity"):
            continue
        msg_year = (ent.findtext("msgYear") or "").strip()
        msg_number = (ent.findtext("msgNumber") or "").strip()
        subregion = (ent.findtext("subregion") or "").strip()
        text_body = (ent.findtext("text") or "").strip()
        issue_date = (ent.findtext("issueDate") or "").strip()
        cancel_msg_year = (ent.findtext("cancelMsgYear") or "").strip()
        cancel_msg_number = (ent.findtext("cancelMsgNumber") or "").strip()

        raw_dtg = ""
        if issue_date:
            parts = issue_date.split()
            if len(parts) >= 3 and parts[0].endswith("Z"):
                raw_dtg = f"{parts[0]} {parts[1]} {parts[2][-2:]}"
            else:
                raw_dtg = issue_date
        year2 = msg_year[-2:] if len(msg_year) >= 2 else msg_year
        if msg_number and year2:
            if subregion and subregion.upper() != "GEN":
                msg_id = f"HYDROARC {msg_number}/{year2}({subregion})"
            else:
                msg_id = f"HYDROARC {msg_number}/{year2}"
        else:
            msg_id = None

        cancellations = navparser.parse_cancellations(text_body)
        if cancel_msg_number and cancel_msg_year:
            c2 = cancel_msg_year[-2:]
            structured_cancel = f"HYDROARC {cancel_msg_number}/{c2}"
            if structured_cancel not in cancellations:
                cancellations.append(structured_cancel)
        coords = navparser.parse_coordinates(text_body)
        hazard = navparser.classify_hazard(text_body)
        messages.append(
            navparser.NavwarnMessage(
                dtg=navparser.parse_dtg(raw_dtg) if raw_dtg else None,
                raw_dtg=raw_dtg,
                msg_id=msg_id,
                coordinates=coords,
                cancellations=cancellations,
                hazard_type=hazard,
                body=text_body,
            )
        )
    return messages


def store_messages(
    messages: Iterable[Any], force: bool = False, output_dir: Path = OUTPUT_DIR
) -> int:
    ensure_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for m in messages:
        fname = message_filename(m)
        path = output_dir / fname
        
        # If file exists and not force, preserve existing data (including DTG)
        if path.exists() and not force:
            continue
        
        # If message doesn't have DTG, assign current timestamp as first-seen date
        if m.dtg is None:
            m.dtg = datetime.datetime.utcnow()
            # Also update raw_dtg if it's empty or just contains the message ID
            if not m.raw_dtg or m.raw_dtg.startswith(m.msg_id or ''):
                m.raw_dtg = m.dtg.strftime('%d%H%MZ %b %y').upper()
        
        base_glob = fname.split(".json")[0] + "_*.json"
        for old in output_dir.glob(base_glob):
            try:
                old.unlink()
            except OSError:
                pass
        with path.open("w", encoding="utf-8") as f:
            json.dump(serialize_message(m), f, ensure_ascii=False, indent=2)
        written += 1
    return written


def run_scrape(
    url: str = API_URL,
    force: bool = False,
    dry_run: bool = False,
    store_xml: bool = False,
    output_dir: Path = OUTPUT_DIR,
) -> int:
    xml_text = fetch_xml(url)
    if store_xml:
        (OUTPUT_DIR / f"{url.split('/')[-1]}.xml").write_text(
            xml_text, encoding="utf-8"
        )
    total_written = 0
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"Failed to parse XML: {e}")
    if root.tag == "broadcast-warn":
        navmsgs = parse_broadcast_warn_xml(xml_text)
        if dry_run:
            for m in navmsgs:
                print(json.dumps(serialize_message(m), ensure_ascii=False))
        else:
            total_written += store_messages(navmsgs, force=force, output_dir=output_dir)
        return total_written
    # SMAPS active format
    for block in extract_msg_text_blocks(xml_text):
        navmsgs = navparser.parse_navwarns(block)
        if dry_run:
            for m in navmsgs:
                print(json.dumps(serialize_message(m), ensure_ascii=False))
        else:
            total_written += store_messages(navmsgs, force=force, output_dir=output_dir)
    return total_written


# --- Generic parse function for tests (accepts path or XML string) ---
def parse(arg: str | os.PathLike[str]) -> list[Any]:  # pragma: no cover - thin wrapper
    """Parse a broadcast-warn or SMAPS XML document and return list of NavwarnMessage.

    Accepts either a filesystem path or an XML string. This provides a simple
    stable parsing entrypoint for tests expecting a generic 'parse' callable.
    """
    if isinstance(arg, (str, Path)) and Path(arg).exists():
        xml_text = Path(arg).read_text(encoding="utf-8")
    else:
        xml_text = str(arg)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    if root.tag == "broadcast-warn":
        return parse_broadcast_warn_xml(xml_text)
    # SMAPS style: flatten all blocks' messages
    messages: list[Any] = []
    for block in extract_msg_text_blocks(xml_text):
        messages.extend(navparser.parse_navwarns(block))
    return messages


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Scrape active NAVWARN SMAPS (category 14) messages"
    )
    ap.add_argument(
        "--force", action="store_true", help="Overwrite existing message JSON files"
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print parsed messages to stdout instead of writing files",
    )
    ap.add_argument("--xml-out", help="Write retrieved raw XML to this file path")
    args = ap.parse_args(argv)

    try:
        xml_text = fetch_xml()
        if args.xml_out:
            Path(args.xml_out).write_text(xml_text, encoding="utf-8")
        if args.dry_run:
            root = ET.fromstring(xml_text)
            if root.tag == "broadcast-warn":
                for m in parse_broadcast_warn_xml(xml_text):
                    print(json.dumps(serialize_message(m), ensure_ascii=False))
            else:
                for block in extract_msg_text_blocks(xml_text):
                    for m in navparser.parse_navwarns(block):
                        print(json.dumps(serialize_message(m), ensure_ascii=False))
            return 0
        written = 0
        root = ET.fromstring(xml_text)
        if root.tag == "broadcast-warn":
            written += store_messages(
                parse_broadcast_warn_xml(xml_text), force=args.force
            )
        else:
            for block in extract_msg_text_blocks(xml_text):
                written += store_messages(
                    navparser.parse_navwarns(block), force=args.force
                )
        print(f"Wrote {written} new/updated message files")
        if written > 0:
            with open(
                CURRENT_DIR / ".scrape_timestamp_HYDROARC", "w", encoding="utf-8"
            ) as f:
                f.write(f"{datetime.datetime.utcnow().isoformat()}Z\n")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
