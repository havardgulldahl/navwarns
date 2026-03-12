#!/usr/bin/env python3
"""Re-parse all existing GeoJSON warning files using the current parser.

Reads the ``body`` (and ``raw_dtg``) text stored in previously generated
GeoJSON Feature files under ``current/navwarns/`` and ``current/prips/``,
re-parses them with the latest parser routines, and overwrites the files
with updated geometry and metadata.

This is useful after parser upgrades that improve coordinate extraction,
geometry classification, or hazard typing — the early-converted warnings
can be regenerated without re-downloading the original sources.

Usage:
    python -m scripts.regenerate [--dry-run] [--verbose]
    python scripts/regenerate.py  [--dry-run] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from . import parser as navparser  # type: ignore
except ImportError:  # running as a script
    import importlib.util
    import pathlib

    this_dir = pathlib.Path(__file__).resolve().parent
    parser_path = this_dir / "parser.py"
    spec = importlib.util.spec_from_file_location("navparser", parser_path)
    navparser = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(navparser)  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)

CURRENT_DIR = Path("current")
NAVWARNS_DIR = CURRENT_DIR / "navwarns"
PRIPS_DIR = CURRENT_DIR / "prips"


# ---- helpers --------------------------------------------------------


def _load_feature(path: Path) -> Optional[dict]:
    """Load a GeoJSON Feature from a JSON file."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("Could not load %s: %s", path, exc)
        return None


def _feature_filename(feat_id: str) -> str:
    """Derive a safe filename from a GeoJSON Feature id."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", feat_id)
    return f"{safe}.json"


def _write_feature(feat: dict, path: Path) -> None:
    """Write a GeoJSON Feature to *path*."""
    with path.open("w", encoding="utf-8") as f:
        json.dump(feat, f, ensure_ascii=False, indent=2)


def _parse_iso_dtg(iso: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 datetime string, returning None on failure."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


# ---- regeneration logic ---------------------------------------------


def regenerate_navwarn_file(
    path: Path,
    output_dir: Path,
    *,
    dry_run: bool = False,
) -> List[Path]:
    """Re-parse a single HYDROARC / NAVAREA feature file.

    Returns list of paths written (may be >1 for multi-group messages).
    If the parser now splits a single file into groups, the original
    non-group file is removed to avoid stale duplicates.
    """
    feat = _load_feature(path)
    if feat is None:
        return []

    props = feat.get("properties", {})
    body: str = props.get("body", "")
    raw_dtg: str = props.get("raw_dtg", "")
    original_dtg_iso: Optional[str] = props.get("dtg")
    summary = props.get("summary")

    if not body:
        logging.debug("Skipping %s — no body text", path.name)
        return []

    # Re-parse with the current parser
    msg = navparser.NavwarnMessage.from_text(raw_dtg, body)

    # Preserve the original dtg if the parser could not derive one
    if msg.dtg is None:
        msg.dtg = _parse_iso_dtg(original_dtg_iso)
    if msg.dtg is None and not msg.raw_dtg:
        msg.raw_dtg = raw_dtg

    # Preserve year from original if parser couldn't derive it
    if msg.year is None and props.get("year"):
        msg.year = props["year"]

    # Generate features (may be multi-group)
    new_feats: List[dict]
    if hasattr(msg, "to_geojson_features"):
        new_feats = msg.to_geojson_features()
    else:
        new_feats = [msg.to_geojson_feature()]

    written: List[Path] = []
    new_filenames: Set[str] = set()
    for nf in new_feats:
        nf.setdefault("properties", {})["summary"] = summary
        feat_id = nf.get("id") or msg.msg_id or "NOID"
        fname = _feature_filename(feat_id)
        new_filenames.add(fname)
        out_path = output_dir / fname

        if dry_run:
            logging.info("[dry-run] would write %s", out_path)
        else:
            _write_feature(nf, out_path)
            logging.debug("Wrote %s", out_path)
        written.append(out_path)

    # If re-parsing expanded a single file into groups, remove the
    # original non-group file to avoid stale duplicates.
    if path.name not in new_filenames and not dry_run:
        try:
            path.unlink()
            logging.info(
                "Removed stale %s (replaced by %d group files)",
                path.name,
                len(new_filenames),
            )
        except OSError:
            pass

    return written


def regenerate_prip_file(
    path: Path,
    output_dir: Path,
    *,
    dry_run: bool = False,
) -> List[Path]:
    """Re-parse a single PRIP feature file.

    Returns list of paths written (normally one).
    """
    feat = _load_feature(path)
    if feat is None:
        return []

    props = feat.get("properties", {})
    body: str = props.get("body", "")
    raw_dtg: str = props.get("raw_dtg", "")
    original_dtg_iso: Optional[str] = props.get("dtg")
    summary = props.get("summary")

    if not body or not raw_dtg:
        logging.debug("Skipping %s — missing body or raw_dtg", path.name)
        return []

    # Re-parse with the current parser
    msg = navparser.NavwarnMessage.prip_from_text(raw_dtg, body)

    # Preserve the original dtg if the parser could not derive one
    if msg.dtg is None:
        msg.dtg = _parse_iso_dtg(original_dtg_iso)
    if msg.dtg is None and not msg.raw_dtg:
        msg.raw_dtg = raw_dtg

    # Preserve year from original if parser couldn't derive it
    if msg.year is None and props.get("year"):
        msg.year = props["year"]

    new_feat = msg.to_geojson_feature()
    new_feat["properties"]["summary"] = summary

    # Use original filename to preserve naming consistency
    out_path = output_dir / path.name

    if dry_run:
        logging.info("[dry-run] would write %s", out_path)
    else:
        _write_feature(new_feat, out_path)
        logging.debug("Wrote %s", out_path)

    return [out_path]


def regenerate_all(
    *,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Walk all generated GeoJSON files and regenerate them.

    Returns a dict with counts of processed files per category.
    """
    stats: Dict[str, int] = {
        "navwarns_processed": 0,
        "navwarns_written": 0,
        "navwarns_skipped": 0,
        "prips_processed": 0,
        "prips_written": 0,
        "prips_skipped": 0,
    }

    # --- HYDROARC / NAVAREA navwarns ----------------------------------
    if NAVWARNS_DIR.is_dir():
        # Collect all json files; for grouped features (id contains #grp)
        # we only need to re-parse once per parent_id.
        processed_parents: Set[str] = set()

        for json_path in sorted(NAVWARNS_DIR.glob("*.json")):
            feat = _load_feature(json_path)
            if feat is None:
                stats["navwarns_skipped"] += 1
                continue

            props = feat.get("properties", {})
            parent_id = props.get("parent_id")

            # Skip group members already processed via their parent
            if parent_id and parent_id in processed_parents:
                continue

            if parent_id:
                processed_parents.add(parent_id)

            stats["navwarns_processed"] += 1
            written = regenerate_navwarn_file(json_path, NAVWARNS_DIR, dry_run=dry_run)
            if written:
                stats["navwarns_written"] += len(written)
            else:
                stats["navwarns_skipped"] += 1
    else:
        logging.info("No navwarns directory found at %s", NAVWARNS_DIR)

    # --- PRIPs (coastal warnings) ------------------------------------
    if PRIPS_DIR.is_dir():
        for json_path in sorted(PRIPS_DIR.glob("*.json")):
            stats["prips_processed"] += 1
            written = regenerate_prip_file(json_path, PRIPS_DIR, dry_run=dry_run)
            if written:
                stats["prips_written"] += len(written)
            else:
                stats["prips_skipped"] += 1
    else:
        logging.info("No prips directory found at %s", PRIPS_DIR)

    return stats


# ---- CLI entry point ------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for the regeneration script."""
    ap = argparse.ArgumentParser(
        description=(
            "Re-parse all generated GeoJSON warning files using the "
            "current (upgraded) parser routines."
        )
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be regenerated without writing files",
    )
    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug-level logging",
    )
    args = ap.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    stats = regenerate_all(dry_run=args.dry_run)

    logging.info(
        "Navwarns: %d processed, %d files written, %d skipped",
        stats["navwarns_processed"],
        stats["navwarns_written"],
        stats["navwarns_skipped"],
    )
    logging.info(
        "PRIPs:    %d processed, %d files written, %d skipped",
        stats["prips_processed"],
        stats["prips_written"],
        stats["prips_skipped"],
    )

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
