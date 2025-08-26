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
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Any
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
OUTPUT_DIR = Path("current")


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def fetch_xml(url: str = API_URL) -> str:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.text


def extract_msg_text_blocks(xml_text: str) -> Iterable[str]:
    root = ET.fromstring(xml_text)
    # Each smapsActiveEntity/msgText contains one NAVWARN style block
    for ent in root.findall("smapsActiveEntity"):
        msg_el = ent.find("msgText")
        if msg_el is not None and (text := msg_el.text):
            yield text.strip()


def message_filename(msg: Any) -> str:
    base_id = msg.msg_id or "NOID"
    # Simplify: filename is just the (sanitized) NAVWARN id.
    import re as _re

    safe = _re.sub(r"[^A-Za-z0-9_.-]+", "_", base_id)
    return f"{safe}.json"


def serialize_message(msg: Any) -> dict:
    # Build GeoJSON Feature. Use Point if single coordinate, MultiPoint if multiple.
    coords = msg.coordinates or []
    if not coords:
        geometry = {"type": "Point", "coordinates": []}  # empty placeholder
    elif len(coords) == 1:
        lat, lon = coords[0]
        geometry = {"type": "Point", "coordinates": [lon, lat]}
    else:
        # Multiple coordinates -> MultiPoint
        geometry = {
            "type": "MultiPoint",
            "coordinates": [[lon, lat] for (lat, lon) in coords],
        }
    return {
        "type": "Feature",
        "id": msg.msg_id or None,
        "geometry": geometry,
        "properties": {
            "dtg": msg.dtg.isoformat() if msg.dtg else None,
            "raw_dtg": msg.raw_dtg,
            "msg_id": msg.msg_id,
            "cancellations": msg.cancellations,
            "hazard_type": msg.hazard_type,
            "summary": None,
            "body": msg.body,
        },
    }


def store_messages(messages: Iterable[Any], force: bool = False) -> int:
    ensure_output_dir()
    written = 0
    for m in messages:
        fname = message_filename(m)
        path = OUTPUT_DIR / fname
        if path.exists() and not force:
            continue
        # Clean up older hashed variants for same id
        base_glob = fname.split(".json")[0] + "_*.json"
        for old in OUTPUT_DIR.glob(base_glob):
            try:
                old.unlink()
            except OSError:
                pass
        with path.open("w", encoding="utf-8") as f:
            json.dump(serialize_message(m), f, ensure_ascii=False, indent=2)
        written += 1
    return written


def run_scrape(force: bool = False, dry_run: bool = False) -> int:
    xml_text = fetch_xml()
    total_written = 0
    for block in extract_msg_text_blocks(xml_text):
        navmsgs = navparser.parse_navwarns(block)
        if dry_run:
            for m in navmsgs:
                print(json.dumps(serialize_message(m), ensure_ascii=False))
        else:
            total_written += store_messages(navmsgs, force=force)
    return total_written


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
            for block in extract_msg_text_blocks(xml_text):
                for m in navparser.parse_navwarns(block):
                    print(json.dumps(serialize_message(m), ensure_ascii=False))
            return 0
        written = 0
        for block in extract_msg_text_blocks(xml_text):
            written += store_messages(navparser.parse_navwarns(block), force=args.force)
        print(f"Wrote {written} new/updated message files to {OUTPUT_DIR}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
