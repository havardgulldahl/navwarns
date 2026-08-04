"""Audit cancellation-line parser coverage in history XML archives.

This utility scans NGA history XML files and reports cancellation lines that
look like self-cancel directives but are not captured as self-cancel tokens by
``parse_cancellations``.

It is designed for two use-cases:
1. Manual diagnostics and trend reporting.
2. A stable baseline gate in tests/CI to detect regressions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "history"
sys.path.insert(0, str(ROOT / "scripts"))
from parser import parse_cancellations  # noqa: E402

RE_FILE_YEAR = re.compile(r"msgYear=(\d{4})")
RE_CANCEL_WORD = re.compile(r"\bCANCEL\b", re.IGNORECASE)
RE_THIS_SELF = re.compile(r"\bTHIS\s+(?:MSG|MESSAGE)\b", re.IGNORECASE)
RE_BARE_DTG = re.compile(
    r"\b\d{6}(?:Z| ?UTC)\s+[A-Z]{3}(?:\s+\d{2})?\b",
    re.IGNORECASE,
)
RE_HIGH_CONF_THIS_DTG = re.compile(
    r"^\s*(?:\d+\.)?\s*CANCEL\s+"
    r"THIS(?:\s+(?:MSG|MESSAGE|WARNING))?\s+"
    r"\d{6}(?:Z| ?UTC)?\s+[A-Z]{3}(?:\s+\d{2,4})?\b",
    re.IGNORECASE,
)
RE_HIGH_CONF_THE_MSG_DTG = re.compile(
    r"^\s*(?:\d+\.)?\s*CANCEL\s+THE\s+MSG\s+"
    r"\d{6}(?:Z| ?UTC)?\s+[A-Z]{3}(?:\s+\d{2,4})?\b",
    re.IGNORECASE,
)
RE_HIGH_CONF_THIS_DATE = re.compile(
    r"^\s*(?:\d+\.)?\s*CANCEL\s+"
    r"THIS(?:\s+(?:MSG|MESSAGE|WARNING))?\s+"
    r"\d{2}\s+[A-Z]{3}(?:\s+\d{2,4})?\b",
    re.IGNORECASE,
)
RE_HIGH_CONF_THE_MSG_DATE = re.compile(
    r"^\s*(?:\d+\.)?\s*CANCEL\s+THE\s+MSG\s+" r"\d{2}\s+[A-Z]{3}(?:\s+\d{2,4})?\b",
    re.IGNORECASE,
)
RE_HIGH_CONF_BARE_DTG = re.compile(
    r"^\s*(?:\d+\.)?\s*CANCEL\s+" r"\d{6}(?:Z| ?UTC)\s+[A-Z]{3}(?:\s+\d{2,4})?\b",
    re.IGNORECASE,
)
RE_SELF_TOKEN = re.compile(
    r"(?:\bTHIS\b|^\d{6}(?:Z| ?UTC)\s+[A-Z]{3}(?:\s+\d{2})?$)",
    re.IGNORECASE,
)


@dataclass
class CoverageMiss:
    """A cancellation line that was not recognized as a self-cancel token."""

    xml_file: str
    year: int
    msg_id: str
    issue_date: str
    line: str
    family: str


def _year_from_path(path: Path) -> Optional[int]:
    match = RE_FILE_YEAR.search(path.name)
    if match:
        return int(match.group(1))
    return None


def _iter_history_xml_files(
    history_dir: Path,
    years: Optional[Sequence[int]] = None,
) -> Iterable[Path]:
    allowed = set(years) if years else None
    for xml_path in sorted(history_dir.glob("broadcast-warn*.xml")):
        year = _year_from_path(xml_path)
        if year is None:
            continue
        if allowed is not None and year not in allowed:
            continue
        yield xml_path


def _family_for_line(line: str) -> str:
    text = line.upper()
    if RE_HIGH_CONF_BARE_DTG.search(text):
        if re.search(r"\b\d{6}(?:Z| ?UTC)\s+[A-Z]{3}\s+\d{2,4}\b", text):
            return "bare_dtg_with_year"
        return "bare_dtg_without_year"
    if RE_HIGH_CONF_THE_MSG_DTG.search(text):
        if re.search(r"\b\d{6}(?:Z| ?UTC)\s+[A-Z]{3}\s+\d{2,4}\b", text):
            return "the_msg_dtg_with_year"
        return "the_msg_dtg_without_year"
    if RE_HIGH_CONF_THIS_DTG.search(text):
        if "THIS WARNING" in text:
            if re.search(r"\b\d{6}(?:Z| ?UTC)\s+[A-Z]{3}\s+\d{2,4}\b", text):
                return "this_warning_dtg_with_year"
            return "this_warning_dtg_without_year"
        if re.search(r"\b\d{6}(?:Z| ?UTC)\s+[A-Z]{3}\s+\d{2,4}\b", text):
            return "this_msg_dtg_with_year"
        return "this_msg_dtg_without_year"
    if RE_HIGH_CONF_THE_MSG_DATE.search(text):
        if re.search(r"\b\d{2}\s+[A-Z]{3}\s+\d{2,4}\b", text):
            return "the_msg_date_with_year"
        return "the_msg_date_without_year"
    if RE_HIGH_CONF_THIS_DATE.search(text):
        if "THIS WARNING" in text:
            if re.search(r"\b\d{2}\s+[A-Z]{3}\s+\d{2,4}\b", text):
                return "this_warning_date_with_year"
            return "this_warning_date_without_year"
        if re.search(r"\b\d{2}\s+[A-Z]{3}\s+\d{2,4}\b", text):
            return "this_msg_date_with_year"
        return "this_msg_date_without_year"
    return "other_high_conf_cancel"


def _is_self_cancel_candidate(line: str) -> bool:
    text = line.upper()
    if RE_CANCEL_WORD.search(text) is None:
        return False
    return any(
        matcher.search(text)
        for matcher in (
            RE_HIGH_CONF_THIS_DTG,
            RE_HIGH_CONF_THE_MSG_DTG,
            RE_HIGH_CONF_THIS_DATE,
            RE_HIGH_CONF_THE_MSG_DATE,
            RE_HIGH_CONF_BARE_DTG,
        )
    )


def _line_has_self_token(parsed_tokens: List[str]) -> bool:
    for token in parsed_tokens:
        if RE_SELF_TOKEN.search(str(token).strip()):
            return True
    return False


def find_self_cancel_regex_misses(
    history_dir: Path,
    years: Optional[Sequence[int]] = None,
    max_examples_per_family: int = 0,
) -> Dict[str, object]:
    """Return parser coverage misses for self-cancel candidate lines.

    Args:
        history_dir: Base directory containing broadcast-warn XML files.
        years: Optional explicit year filter.
        max_examples_per_family: Optional cap for examples in output.

    Returns:
        A dictionary with totals, family counts and miss examples.
    """
    misses: List[CoverageMiss] = []
    candidate_count = 0

    for xml_path in _iter_history_xml_files(history_dir, years=years):
        year = _year_from_path(xml_path)
        if year is None:
            continue
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue

        for entity in root:
            body = (entity.findtext("text") or "").replace("\r", "")
            if not body:
                continue
            msg_id = (
                (entity.findtext("authority") or "").strip()
                or (entity.findtext("msgSeries") or "").strip()
                or "UNKNOWN"
            )
            issue_date = (entity.findtext("issueDate") or "").strip()

            for raw_line in body.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if not _is_self_cancel_candidate(line):
                    continue

                candidate_count += 1
                parsed = parse_cancellations(line)
                if _line_has_self_token(parsed):
                    continue

                misses.append(
                    CoverageMiss(
                        xml_file=xml_path.name,
                        year=year,
                        msg_id=msg_id,
                        issue_date=issue_date,
                        line=line,
                        family=_family_for_line(line),
                    )
                )

    by_family: Dict[str, int] = {}
    for miss in misses:
        by_family[miss.family] = by_family.get(miss.family, 0) + 1

    miss_examples: List[Dict[str, str]] = []
    if max_examples_per_family > 0:
        seen_per_family: Dict[str, int] = {}
        for miss in misses:
            used = seen_per_family.get(miss.family, 0)
            if used >= max_examples_per_family:
                continue
            miss_examples.append(
                {
                    "family": miss.family,
                    "xml_file": miss.xml_file,
                    "year": str(miss.year),
                    "msg_id": miss.msg_id,
                    "issue_date": miss.issue_date,
                    "line": miss.line,
                }
            )
            seen_per_family[miss.family] = used + 1

    return {
        "candidate_lines": candidate_count,
        "miss_count": len(misses),
        "misses_by_family": dict(sorted(by_family.items())),
        "examples": miss_examples,
        "years": sorted(set(m.year for m in misses)),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit self-cancel line coverage in history XML.",
    )
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=HISTORY_DIR,
        help="Path to history directory (default: ./history).",
    )
    parser.add_argument(
        "--years",
        nargs="*",
        type=int,
        default=None,
        help="Optional list of years to scan.",
    )
    parser.add_argument(
        "--max-examples-per-family",
        type=int,
        default=3,
        help="Maximum examples to emit per family.",
    )
    parser.add_argument(
        "--as-json",
        action="store_true",
        help="Emit JSON output only.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entry point."""
    args = _parse_args()
    report = find_self_cancel_regex_misses(
        history_dir=args.history_dir,
        years=args.years,
        max_examples_per_family=args.max_examples_per_family,
    )

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("Self-cancel coverage audit")
    print(f"  Candidate lines: {report['candidate_lines']}")
    print(f"  Misses: {report['miss_count']}")
    families = report["misses_by_family"]
    if families:
        print("  Misses by family:")
        for family, count in families.items():
            print(f"    - {family}: {count}")
    else:
        print("  Misses by family: none")

    examples = report["examples"]
    if examples:
        print("  Examples:")
        for ex in examples:
            print(
                "    - "
                f"[{ex['year']}] {ex['xml_file']} | {ex['msg_id']} | "
                f"{ex['line']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
