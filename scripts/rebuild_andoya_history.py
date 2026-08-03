#!/usr/bin/env python3
"""Reparse all cached Andøya OLX files and write one GeoJSON Feature per
unique (area_name, valid_from date) into history/<year>/navwarns/.

Run after upgrading parse_active_period to backfill correct valid_from /
valid_until into the archive GeoJSON files.

Usage:
    python scripts/rebuild_andoya_history.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

try:
    from . import scraper_andoya  # type: ignore
except ImportError:
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "scraper_andoya", Path(__file__).resolve().parent / "scraper_andoya.py"
    )
    scraper_andoya = _ilu.module_from_spec(_spec)  # type: ignore
    assert _spec and _spec.loader
    _spec.loader.exec_module(scraper_andoya)  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

HISTORY_DIR = Path("history")


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print actions without writing")
    args = ap.parse_args()

    olx_files = sorted(HISTORY_DIR.rglob("ANDOYA_*.olx"))
    logging.info("Found %d OLX files", len(olx_files))

    # Collect unique features: (area_name, valid_from_date) → feature dict
    seen: dict[tuple[str, str], dict] = {}

    for olx_path in olx_files:
        text = olx_path.read_text(encoding="latin-1")
        routes = scraper_andoya.parse_olx(text)
        for route in routes:
            feat = scraper_andoya.route_to_geojson_feature(route)
            vf_date = feat["properties"]["valid_from"][:10]
            key = (route.area_name, vf_date)
            if key not in seen:
                seen[key] = feat

    logging.info("Found %d unique ANDOYA warnings", len(seen))

    written = 0
    for (area_name, vf_date), feat in sorted(seen.items(), key=lambda x: x[0]):
        year = vf_date[:4]
        out_dir = HISTORY_DIR / year / "navwarns"
        fname = f"ANDOYA_{_safe(area_name)}_{vf_date}.json"
        out_path = out_dir / fname

        if out_path.exists():
            logging.debug("Skipping existing %s", out_path)
            continue

        if args.dry_run:
            logging.info("[dry-run] would write %s  valid_from=%s  valid_until=%s",
                         out_path, feat["properties"]["valid_from"],
                         feat["properties"]["valid_until"])
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(feat, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logging.info("Wrote %s", out_path)
        written += 1

    logging.info(
        "%s %d file(s)",
        "Would write" if args.dry_run else "Wrote",
        written,
    )


if __name__ == "__main__":
    main()
