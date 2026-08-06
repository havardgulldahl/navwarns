"""Build archive GeoJSON files from history/ directory.

Walks ``history/<year>/`` (both old layout with A/B/C/D/E sub-dirs
and new layout with navwarns/prips/NAVAREAXX) and merges all
individual Feature JSON files into per-year FeatureCollections
written to ``docs/archive<year>.geojson``.

Also writes ``docs/manifest.json`` listing available years with
feature counts so the frontend can discover them dynamically.

Usage::

    python scripts/build_archives.py          # all years
    python scripts/build_archives.py 2024     # single year
    python scripts/build_archives.py 2024 2025
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from parser import FROM_TO_PERIOD_PATTERN  # noqa: E402
from scraper_andoya import (
    parse_active_period as parse_andoya_active_period,
)  # noqa: E402

HISTORY_DIR = ROOT / "history"
DOCS_DIR = ROOT / "docs"
CURRENT_NAVWARNS_DIR = ROOT / "current" / "navwarns"

MONTH_MAP = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    """Parse ISO datetime text into an aware UTC datetime when possible."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _valid_until_before_valid_from(props: Dict[str, Any]) -> bool:
    """Return True when both endpoints parse and until is before from."""
    valid_from = _parse_iso_datetime(props.get("valid_from"))
    valid_until = _parse_iso_datetime(props.get("valid_until"))
    if not valid_from or not valid_until:
        return False
    return valid_until < valid_from


def _clear_invalid_valid_until(props: Dict[str, Any]) -> bool:
    """Clear impossible validity windows and report whether a change was made."""
    if _valid_until_before_valid_from(props):
        props["valid_until"] = None
        return True
    return False


def _extract_self_cancel_datetime(props: Dict[str, Any]) -> Optional[datetime]:
    """Extract self-cancellation datetime from cancellations/body text."""

    def _infer_year(month: int) -> Optional[int]:
        """Best-effort year for yearless cancel dates."""
        dtg_text = props.get("dtg")
        if isinstance(dtg_text, str):
            try:
                parsed = datetime.fromisoformat(dtg_text.replace("Z", "+00:00"))
                base = parsed.year
                if month < parsed.month:
                    return base + 1
                return base
            except ValueError:
                pass
        msg_year = props.get("year")
        if msg_year:
            try:
                return int(msg_year)
            except (TypeError, ValueError):
                return None
        return None

    sources = list(props.get("cancellations") or [])
    body = props.get("body") or ""
    if body:
        for line in re.split(r"[.\n]", body.upper()):
            if "THIS MSG" in line or "THIS MESSAGE" in line:
                sources.append(line.strip())
            for m_dtg_var in _RE_CANCEL_THIS_VARIANT_DTG.finditer(line):
                normalized = f"THIS MSG {m_dtg_var.group(1).strip().upper()}"
                if normalized not in sources:
                    sources.append(normalized)
            for m_dtg in _RE_CANCEL_BARE_DTG.finditer(line):
                token = m_dtg.group(1).strip().upper()
                if token not in sources:
                    sources.append(token)
        year_hint = str(props["year"])[-2:] if props.get("year") else None
        for m_ru in _RE_RU_SELF_CANCEL.finditer(body):
            digits = m_ru.group(1)
            ru_month_raw = m_ru.group(2)
            yr_raw = m_ru.group(3)
            en_month = _RU_MONTH_MAP.get(ru_month_raw.upper())
            if not en_month:
                continue
            if len(digits) == 6:
                ddhhmm = digits
            elif len(digits) == 2:
                ddhhmm = digits + "0000"
            else:
                continue
            yr_2 = yr_raw[-2:] if yr_raw else year_hint
            if yr_2 is None:
                continue
            normalized = f"THIS MSG {ddhhmm} UTC {en_month} {yr_2}"
            if normalized not in sources:
                sources.append(normalized)

    for cancel in sources:
        if not cancel:
            continue
        upper = cancel.upper()
        m = re.search(
            r"THIS (?:MSG|MESSAGE) (\d{2})(\d{2})(\d{2})"
            r"(?:Z| UTC)? ?([A-Z]{3}) (\d{2})",
            cancel,
        )
        if not m:
            m = re.search(
                r"\b(\d{2})(\d{2})(\d{2})(?:Z| ?UTC)\s+([A-Z]{3})\s+(\d{2})\b",
                cancel,
            )
        if m:
            day, hour, minute = (
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )
            mon = MONTH_MAP.get(m.group(4))
            yr = 2000 + int(m.group(5))
            if mon:
                try:
                    return datetime(
                        yr,
                        mon,
                        day,
                        hour,
                        minute,
                        tzinfo=timezone.utc,
                    )
                except ValueError:
                    pass

        m_noy = re.search(
            r"THIS (?:MSG|MESSAGE) (\d{2})(\d{2})(\d{2})(?:Z| ?UTC)? ([A-Z]{3})$",
            cancel,
        )
        if not m_noy:
            m_noy = re.search(
                r"\b(\d{2})(\d{2})(\d{2})(?:Z| ?UTC)\s+([A-Z]{3})\b$",
                cancel,
            )
        if m_noy:
            day, hour, minute = (
                int(m_noy.group(1)),
                int(m_noy.group(2)),
                int(m_noy.group(3)),
            )
            mon = MONTH_MAP.get(m_noy.group(4))
            yr = _infer_year(mon) if mon else None
            if mon and yr:
                try:
                    return datetime(
                        yr,
                        mon,
                        day,
                        hour,
                        minute,
                        tzinfo=timezone.utc,
                    )
                except ValueError:
                    pass

        m2 = re.search(
            r"THIS (?:MSG|MESSAGE) (\d{2}) ([A-Z]{3})" r" (\d{2})",
            cancel,
        )
        if m2:
            day = int(m2.group(1))
            mon = MONTH_MAP.get(m2.group(2))
            yr = 2000 + int(m2.group(3))
            if mon:
                try:
                    return datetime(
                        yr,
                        mon,
                        day,
                        tzinfo=timezone.utc,
                    )
                except ValueError:
                    pass

        m3 = re.search(
            r"THIS (?:MSG|MESSAGE) (\d{2}) ([A-Z]{3})$",
            upper,
        )
        if m3:
            day = int(m3.group(1))
            mon = MONTH_MAP.get(m3.group(2))
            yr = _infer_year(mon) if mon else None
            if mon and yr:
                try:
                    return datetime(
                        yr,
                        mon,
                        day,
                        tzinfo=timezone.utc,
                    )
                except ValueError:
                    pass

    return None


def _append_cancel_typo_correction(
    props: Dict[str, Any],
    *,
    parsed: datetime,
    corrected: datetime,
) -> bool:
    """Record a deterministic correction for self-cancel typo adjustment."""
    corrections = props.setdefault("corrections", [])
    if not isinstance(corrections, list):
        return False
    entry = {
        "field": "cancel_date",
        "code": "self_cancel_before_valid_from",
        "before": parsed.isoformat(),
        "after": corrected.isoformat(),
        "note": "Adjusted to valid_from + 24h due to likely source typo.",
    }
    if entry in corrections:
        return False
    corrections.append(entry)
    return True


def _normalize_cancel_date(props: Dict[str, Any]) -> bool:
    """Normalize and reconcile cancel_date with parsed self-cancel text."""
    raw = props.get("cancel_date")
    valid_from = _parse_iso_datetime(props.get("valid_from"))
    valid_until = _parse_iso_datetime(props.get("valid_until"))
    cancel_dt = _parse_iso_datetime(raw)
    changed = False

    if cancel_dt is not None:
        if valid_from and cancel_dt < valid_from:
            cancel_dt = None
            changed = True
        if valid_until and cancel_dt and cancel_dt > valid_until:
            cancel_dt = None
            changed = True
    elif raw is not None:
        changed = True

    text_cancel_dt = _extract_self_cancel_datetime(props)
    if text_cancel_dt is not None:
        if valid_from and text_cancel_dt < valid_from:
            corrected = valid_from + timedelta(hours=24)
            if _append_cancel_typo_correction(
                props,
                parsed=text_cancel_dt,
                corrected=corrected,
            ):
                changed = True
            text_cancel_dt = corrected
        if valid_until and text_cancel_dt and text_cancel_dt > valid_until:
            text_cancel_dt = None

    if text_cancel_dt is not None:
        if cancel_dt is None or text_cancel_dt != cancel_dt:
            cancel_dt = text_cancel_dt
            changed = True

    normalized = cancel_dt.isoformat() if cancel_dt is not None else None
    if props.get("cancel_date") != normalized:
        props["cancel_date"] = normalized
        return True
    return changed


def _andoya_year_hint(props: Dict[str, Any]) -> Optional[int]:
    """Best-effort reference year for yearless Andoya active-period text."""
    dtg = _parse_iso_datetime(props.get("dtg"))
    if dtg is not None:
        return dtg.year
    year = props.get("year")
    if year is None:
        return None
    try:
        return int(year)
    except (TypeError, ValueError):
        return None


def _apply_andoya_active_period(props: Dict[str, Any]) -> bool:
    """Set valid_from/valid_until from Andoya active-period text when available."""
    msg_id = props.get("msg_id") or ""
    if not isinstance(msg_id, str) or not msg_id.startswith("ANDOYA_"):
        return False
    body = props.get("body") or ""
    if not isinstance(body, str) or not body.strip():
        return False

    start_dt, end_dt = parse_andoya_active_period(
        body,
        ref_year=_andoya_year_hint(props),
    )
    if start_dt is None:
        return False

    changed = False
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat() if end_dt is not None else None
    if props.get("valid_from") != start_iso:
        props["valid_from"] = start_iso
        changed = True
    if props.get("valid_until") != end_iso:
        props["valid_until"] = end_iso
        changed = True
    return changed


# Russian month abbreviations → English 3-letter abbreviations (for Russian NAVAREA XX)
_RU_MONTH_MAP: Dict[str, str] = {
    "ЯНВ": "JAN",
    "ЯНВА": "JAN",
    "ФЕВ": "FEB",
    "ФЕВР": "FEB",
    "МАР": "MAR",
    "МАРТ": "MAR",
    "АПР": "APR",
    "АПРЕ": "APR",
    "МАЙ": "MAY",
    "МАЯ": "MAY",
    "ИЮН": "JUN",
    "ИЮНЬ": "JUN",
    "ИЮЛ": "JUL",
    "ИЮЛЬ": "JUL",
    "АВГ": "AUG",
    "АВГА": "AUG",
    "АВГУСТА": "AUG",
    "СЕН": "SEP",
    "СЕНТ": "SEP",
    "ОКТ": "OCT",
    "ОКТЯ": "OCT",
    "НОЯ": "NOV",
    "НОЯБ": "NOV",
    "ДЕК": "DEC",
    "ДЕКА": "DEC",
}

_RE_RU_SELF_CANCEL = re.compile(
    r"ОТМ\s+ЭТОТ\s+(?:НР|ПУНКТ)\s+(\d{2,6})\s+([\u0400-\u04FF]{2,7})(?:\s+(\d{2,4}))?",
    re.IGNORECASE,
)

_RE_CANCEL_BARE_DTG = re.compile(
    r"\bCANCEL\s+" r"((\d{2})(\d{2})(\d{2})(?:Z| ?UTC)\s+([A-Z]{3})(?:\s+(\d{2}))?)\b",
    re.IGNORECASE,
)

_RE_CANCEL_THIS_VARIANT_DTG = re.compile(
    r"\bCANCEL\s+(?:THIS(?:\s+(?:MSG|MESSAGE|WARNING))?|THE\s+MSG)\s+"
    r"((\d{2})(\d{2})(\d{2})(?:Z| ?UTC)\s+([A-Z]{3})(?:\s+(\d{2}))?)\b",
    re.IGNORECASE,
)

_FROM_TO_DD_DD_MON_YY = re.compile(
    r"\bFROM\s+(\d{1,2})\s+TO\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{2})\b"
)

_FROM_TO_DD_HHMM_DD_HHMM_UTC_MON_YY = re.compile(
    r"\bFROM\s+(\d{1,2})\s+(\d{4})\s+TO\s+(\d{1,2})\s+(\d{4})\s+UTC\s+([A-Z]{3})\s+(\d{2})\b"
)


def _parse_nga_date(text: str) -> Optional[datetime]:
    """Parse NGA DTG format DDHHMM[Z] MON YYYY into a UTC datetime."""
    m = re.match(
        r"(\d{2})(\d{2})(\d{2})Z?\s+([A-Z]{3})\s+(\d{4})",
        text.strip().upper(),
    )
    if m:
        day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3))
        mon = MONTH_MAP.get(m.group(4))
        yr = int(m.group(5))
        if mon:
            try:
                return datetime(yr, mon, day, hour, minute, tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _load_xml_cancel_dates(
    history_dir: Path,
    year: int,
) -> Dict[int, str]:
    """Extract cancel dates from NGA broadcast-warn XML files for *year*.

    Parses all available navArea XMLs (A-E) and returns a mapping of
    msgNumber -> cancelDate ISO string for messages with status='C'.
    """
    cancel_dates: Dict[int, str] = {}
    for nav_area in ("A", "B", "C", "D", "E"):
        xml_path = (
            history_dir
            / f"broadcast-warn?navArea={nav_area}&status=all&msgYear={year}&output=xml.xml"
        )
        if not xml_path.exists():
            continue
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError:
            continue
        for entity in tree.getroot():
            cancel_text = entity.findtext("cancelDate")
            num_text = entity.findtext("msgNumber")
            if not cancel_text or not num_text:
                continue
            try:
                msg_num = int(num_text)
            except ValueError:
                continue
            dt = _parse_nga_date(cancel_text)
            if dt and msg_num not in cancel_dates:
                cancel_dates[msg_num] = dt.isoformat()
    return cancel_dates


def _apply_xml_cancel_dates(
    features: List[Dict[str, Any]],
    cancel_dates: Dict[int, str],
) -> int:
    """Set valid_until from NGA XML cancel dates for features that lack one.

    Only updates features whose msg_id carries a numeric message number
    matching an entry in *cancel_dates*.  Returns count updated.
    """
    updated = 0
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("valid_until"):
            continue
        mid = props.get("msg_id") or feat.get("id") or ""
        # Extract message number: e.g. "HYDROARC 1293/22(25)" -> 1293
        m = re.search(r"(\d+)/\d{2}", mid)
        if not m:
            continue
        msg_num = int(m.group(1))
        cancel_iso = cancel_dates.get(msg_num)
        if cancel_iso:
            props["valid_until"] = cancel_iso
            updated += 1
    return updated


def _parse_from_to_period(
    props: Dict[str, Any],
) -> "Tuple[Optional[str], Optional[str]]":
    """Parse 'FROM DDMON[YY] TO DDMON[YY]' from props body."""
    body = props.get("body") or ""
    if not body:
        return None, None
    m = FROM_TO_PERIOD_PATTERN.search(body.upper())
    base_year = props.get("year")
    if base_year:
        try:
            base_year = int(base_year)
        except (ValueError, TypeError):
            base_year = None

    if m:
        from_day = int(m.group(1))
        from_mon = MONTH_MAP.get(m.group(2))
        from_yr2 = m.group(3)
        to_day = int(m.group(4))
        to_mon = MONTH_MAP.get(m.group(5))
        to_yr2 = m.group(6)
        if not from_mon or not to_mon:
            return None, None
        if to_yr2 is not None:
            to_year = 2000 + int(to_yr2)
        elif base_year:
            to_year = base_year
        else:
            return None, None
        if from_yr2 is not None:
            from_year = 2000 + int(from_yr2)
        elif from_mon > to_mon:
            from_year = to_year - 1
        else:
            from_year = to_year
        try:
            from_dt = datetime(from_year, from_mon, from_day, tzinfo=timezone.utc)
            to_dt = datetime(to_year, to_mon, to_day, tzinfo=timezone.utc)
        except ValueError:
            return None, None
        return from_dt.isoformat(), to_dt.isoformat()

    m2 = _FROM_TO_DD_DD_MON_YY.search(body.upper())
    if m2:
        from_day = int(m2.group(1))
        to_day = int(m2.group(2))
        mon = MONTH_MAP.get(m2.group(3))
        year = 2000 + int(m2.group(4))
        if not mon:
            return None, None
        try:
            from_dt = datetime(year, mon, from_day, tzinfo=timezone.utc)
            to_dt = datetime(year, mon, to_day, tzinfo=timezone.utc)
        except ValueError:
            return None, None
        return from_dt.isoformat(), to_dt.isoformat()

    m3 = _FROM_TO_DD_HHMM_DD_HHMM_UTC_MON_YY.search(body.upper())
    if m3:
        from_day = int(m3.group(1))
        from_hhmm = m3.group(2)
        to_day = int(m3.group(3))
        to_hhmm = m3.group(4)
        mon = MONTH_MAP.get(m3.group(5))
        year = 2000 + int(m3.group(6))
        if not mon:
            return None, None
        try:
            from_hour, from_min = int(from_hhmm[:2]), int(from_hhmm[2:])
            to_hour, to_min = int(to_hhmm[:2]), int(to_hhmm[2:])
            from_dt = datetime(
                year,
                mon,
                from_day,
                from_hour,
                from_min,
                tzinfo=timezone.utc,
            )
            to_dt = datetime(
                year,
                mon,
                to_day,
                to_hour,
                to_min,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None, None
        return from_dt.isoformat(), to_dt.isoformat()

    return None, None


def _compute_valid_from(props: Dict[str, Any]) -> Optional[str]:
    """Derive valid_from from dtg or year."""
    dtg = props.get("dtg")
    if dtg:
        if isinstance(dtg, str):
            try:
                dt = datetime.fromisoformat(dtg.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                pass
        return dtg if isinstance(dtg, str) else None
    # FROM date is a better fallback than year-Jan-1 when present.
    from_iso, _ = _parse_from_to_period(props)
    if from_iso:
        return from_iso
    year = props.get("year")
    if year:
        try:
            return datetime(int(year), 1, 1, tzinfo=timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass
    return None


def _compute_valid_until(
    props: Dict[str, Any],
) -> Optional[str]:
    """Parse self-cancellation dates from cancellations list and body text."""
    cancel_date = _parse_iso_datetime(props.get("cancel_date"))
    valid_from = _parse_iso_datetime(props.get("valid_from"))
    current_until = _parse_iso_datetime(props.get("valid_until"))
    if (
        cancel_date is not None
        and (valid_from is None or cancel_date >= valid_from)
        and (current_until is None or cancel_date <= current_until)
    ):
        return cancel_date.isoformat()

    parsed_self_cancel = _extract_self_cancel_datetime(props)
    if parsed_self_cancel is not None:
        if valid_from is not None and parsed_self_cancel < valid_from:
            corrected = valid_from + timedelta(hours=24)
            _append_cancel_typo_correction(
                props,
                parsed=parsed_self_cancel,
                corrected=corrected,
            )
            return corrected.isoformat()
        return parsed_self_cancel.isoformat()

    # Fall back to active-period TO date
    _, until_iso = _parse_from_to_period(props)
    return until_iso


def _enrich_properties(
    props: Dict[str, Any],
) -> Dict[str, Any]:
    """Add valid_from / valid_until if missing."""
    _apply_andoya_active_period(props)
    if "valid_from" not in props or props["valid_from"] is None:
        props["valid_from"] = _compute_valid_from(props)
    if "valid_until" not in props or props["valid_until"] is None:
        props["valid_until"] = _compute_valid_until(props)
    _normalize_cancel_date(props)
    return props


def _infer_year_from_dir(
    year_dir: Path,
) -> Optional[int]:
    """Parse year integer from directory name."""
    try:
        return int(year_dir.name)
    except ValueError:
        return None


def _dedup_key(feat: Dict[str, Any]) -> str:
    """Return a deduplication key for a feature.

    Uses feature id (includes group suffix like #grp1),
    then msg_id, then body+geometry hash as fallback.
    """
    fid = feat.get("id")
    if fid:
        return f"fid:{fid}"
    props = feat.get("properties") or {}
    mid = props.get("msg_id")
    if mid:
        return f"id:{mid}"
    # Fallback: body text + geometry type to distinguish
    # features at different locations with different geometry
    body = (props.get("body") or "").strip()[:150]
    geom = feat.get("geometry") or {}
    geom_key = str(geom.get("coordinates", ""))[:60]
    return f"body:{body}|geo:{geom_key}"


def _deduplicate_features(
    features: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Remove duplicate features, keeping the best date coverage.

    Daily scrapes of the same warning produce near-identical
    features.  Keep the copy with the earliest valid_from and
    latest valid_until so the timeline filter is most accurate.
    """
    best: Dict[str, Dict[str, Any]] = {}
    for feat in features:
        key = _dedup_key(feat)
        if key not in best:
            best[key] = feat
            continue
        # Merge: keep earliest valid_from, latest valid_until
        old_p = best[key].get("properties") or {}
        new_p = feat.get("properties") or {}
        old_from = old_p.get("valid_from")
        new_from = new_p.get("valid_from")
        if new_from and (not old_from or new_from < old_from):
            old_p["valid_from"] = new_from
        old_until = old_p.get("valid_until")
        new_until = new_p.get("valid_until")
        if new_until and (not old_until or new_until > old_until):
            old_p["valid_until"] = new_until
    return list(best.values())


def _scan_daily_presence(
    year_dir: Path,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Scan daily scrape snapshots to find first-seen and last-seen dates.

    Returns (first_seen, last_seen_cancelled) where:
    - first_seen: msg_id -> earliest date the message appeared in any snapshot
    - last_seen_cancelled: msg_id -> last date seen, only for messages that
      disappeared before the final scrape date (i.e. confirmed cancelled)
        Works for NAVAREAXX (navwarns_raw.txt + ROSATOM HTML), PRIP (HTML),
        NAVTEX_SE (HTML), and ANDOYA (OLX).
    """
    RU_MAP = {
        "АРХАНГЕЛЬСК": "ARKHANGELSK",
        "МУРМАНСК": "MURMANSK",
        "ЗАПАД": "WEST",
    }
    first_seen: Dict[str, str] = {}
    last_seen: Dict[str, str] = {}
    all_dates: List[str] = []

    def mark_seen(msg_id: str, date_str: str) -> None:
        """Track earliest and latest daily appearances for a message."""
        if msg_id not in first_seen or date_str < first_seen[msg_id]:
            first_seen[msg_id] = date_str
        if msg_id not in last_seen or date_str > last_seen[msg_id]:
            last_seen[msg_id] = date_str

    # NAVAREAXX: dated subdirectories with navwarns_raw.txt
    nxx_dir = year_dir / "NAVAREAXX"
    if nxx_dir.is_dir():
        for d in nxx_dir.iterdir():
            if not d.is_dir():
                continue
            date_str = d.name  # e.g. 2025-09-23
            raw = d / "navwarns_raw.txt"
            if not raw.exists():
                continue
            all_dates.append(date_str)
            text = raw.read_text(errors="replace")
            for m in re.finditer(r"NAVAREA XX (\d+/\d+)", text):
                mid = f"NAVAREA XX {m.group(1)}"
                mark_seen(mid, date_str)

        # Also scan ROSATOM HTML files stored directly in the NAVAREAXX dir
        for html_file in nxx_dir.glob("ROSATOM_*.html"):
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", html_file.name)
            if not dm:
                continue
            date_str = dm.group(1)
            all_dates.append(date_str)
            text = html_file.read_text(errors="replace")
            for m in re.finditer(r"NAVAREA XX (\d+/\d+)", text):
                mid = f"NAVAREA XX {m.group(1)}"
                mark_seen(mid, date_str)

    # PRIP: HTML files with date in filename
    prip_dir = year_dir / "PRIP"
    if prip_dir.is_dir():
        for html_file in prip_dir.glob("*.html"):
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", html_file.name)
            if not dm:
                continue
            date_str = dm.group(1)
            all_dates.append(date_str)
            text = html_file.read_text(errors="replace")
            for m in re.finditer(
                r"ПРИП\s+(АРХАНГЕЛЬСК|МУРМАНСК|ЗАПАД)" r"\s+(\d+)/(\d+)",
                text,
            ):
                reg = RU_MAP.get(m.group(1), m.group(1))
                ref = f"PRIP {reg} {m.group(2)}/{m.group(3)}"
                mark_seen(ref, date_str)

    # NAVTEX_SE: HTML files with date in filename
    navtex_dir = year_dir / "NAVTEX_SE"
    if navtex_dir.is_dir():
        for html_file in navtex_dir.glob("*.html"):
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", html_file.name)
            if not dm:
                continue
            date_str = dm.group(1)
            all_dates.append(date_str)
            text = html_file.read_text(errors="replace")
            for m in re.finditer(
                r"([A-Z][A-Z ]+? NAV WARN)\s+(\d+/\d+)",
                text.upper(),
            ):
                ref = " ".join(f"{m.group(1)} {m.group(2)}".split())
                mark_seen(ref, date_str)

    # ANDOYA: OLX files with date in filename
    andoya_dir = year_dir / "ANDOYA"
    if andoya_dir.is_dir():
        for olx_file in andoya_dir.glob("ANDOYA_*.olx"):
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", olx_file.name)
            if not dm:
                continue
            date_str = dm.group(1)
            all_dates.append(date_str)
            text = olx_file.read_text(errors="replace", encoding="latin-1")
            for m in re.finditer(r"(?:Navn|Name):\s*([^\n\r]+)", text):
                safe_name = re.sub(r"[^A-Za-z0-9]+", "_", m.group(1).strip())
                if safe_name:
                    mark_seen(f"ANDOYA_{safe_name}", date_str)

    if not all_dates:
        return {}, {}

    # Only use last_seen as valid_until when the message disappeared
    # *before* the final scrape date (otherwise it may still be active)
    final_date = max(all_dates)
    last_seen_cancelled = {
        mid: date for mid, date in last_seen.items() if date < final_date
    }
    return first_seen, last_seen_cancelled


def _apply_first_seen(
    features: List[Dict[str, Any]],
    first_seen: Dict[str, str],
) -> int:
    """Set valid_from from first-seen snapshot date for features lacking a dtg.

    Only overrides the year-Jan-1 fallback, not actual recorded timestamps.
    Returns count of features updated.
    """
    updated = 0
    for feat in features:
        props = feat.get("properties") or {}
        # Skip if an actual issue timestamp was recorded
        if props.get("dtg"):
            continue
        mid = props.get("msg_id") or ""
        fid = feat.get("id") or ""
        base_id = re.sub(r"#grp\d+$", "", fid)
        date = first_seen.get(mid) or first_seen.get(base_id)
        if date:
            props["valid_from"] = f"{date}T00:00:00+00:00"
            updated += 1
    return updated


def _apply_last_seen(
    features: List[Dict[str, Any]],
    last_seen: Dict[str, str],
) -> int:
    """Set valid_until from last-seen dates for features that lack one.

    Returns count of features updated.
    """
    updated = 0
    for feat in features:
        props = feat.get("properties") or {}
        _apply_andoya_active_period(props)
        _clear_invalid_valid_until(props)
        if props.get("valid_until"):
            continue
        # Match by msg_id or feature id (without group suffix)
        mid = props.get("msg_id") or ""
        fid = feat.get("id") or ""
        # For grouped features like "PRIP WEST 87/25#grp3",
        # strip the group suffix to match the parent msg_id
        base_id = re.sub(r"#grp\d+$", "", fid)
        date = last_seen.get(mid) or last_seen.get(base_id)
        if date:
            candidate = f"{date}T23:59:59+00:00"
            props["valid_until"] = candidate
            if _valid_until_before_valid_from(props):
                props["valid_until"] = None
                continue
            _normalize_cancel_date(props)
            updated += 1
    return updated


def _backfill_last_seen_in_history_files(
    year_dir: Path,
    last_seen: Dict[str, str],
) -> int:
    """Persist inferred valid_until into history JSON files.

    This keeps the source history records consistent with archive inference,
    so downstream tools that read `history/<year>/.../*.json` get the same
    validity windows as `docs/archive<year>.geojson`.
    """

    def _resolve_date_from_feature(feature: Dict[str, Any]) -> Optional[str]:
        props = feature.get("properties") or {}
        mid = props.get("msg_id") or ""
        fid = feature.get("id") or ""
        base_id = re.sub(r"#grp\d+$", "", fid)
        return last_seen.get(mid) or last_seen.get(base_id)

    updated = 0
    for path in sorted(year_dir.rglob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        changed = False
        if data.get("type") == "Feature":
            props = data.setdefault("properties", {})
            before_from = props.get("valid_from")
            before_until = props.get("valid_until")
            before_cancel = props.get("cancel_date")
            data["properties"] = _enrich_properties(props)
            props = data["properties"]
            if (
                props.get("valid_from") != before_from
                or props.get("valid_until") != before_until
                or props.get("cancel_date") != before_cancel
            ):
                changed = True
            if _apply_andoya_active_period(props):
                changed = True
            if _clear_invalid_valid_until(props):
                changed = True
            if not props.get("valid_until"):
                date = _resolve_date_from_feature(data)
                if date:
                    candidate = f"{date}T23:59:59+00:00"
                    props["valid_until"] = candidate
                    if _valid_until_before_valid_from(props):
                        props["valid_until"] = None
                    else:
                        changed = True
            if _normalize_cancel_date(props):
                changed = True
        elif data.get("type") == "FeatureCollection":
            for feat in data.get("features") or []:
                if not isinstance(feat, dict):
                    continue
                props = feat.setdefault("properties", {})
                before_from = props.get("valid_from")
                before_until = props.get("valid_until")
                before_cancel = props.get("cancel_date")
                feat["properties"] = _enrich_properties(props)
                props = feat["properties"]
                if (
                    props.get("valid_from") != before_from
                    or props.get("valid_until") != before_until
                    or props.get("cancel_date") != before_cancel
                ):
                    changed = True
                if _apply_andoya_active_period(props):
                    changed = True
                if _clear_invalid_valid_until(props):
                    changed = True
                if props.get("valid_until"):
                    continue
                date = _resolve_date_from_feature(feat)
                if date:
                    candidate = f"{date}T23:59:59+00:00"
                    props["valid_until"] = candidate
                    if _valid_until_before_valid_from(props):
                        props["valid_until"] = None
                    else:
                        changed = True
                if _normalize_cancel_date(props):
                    changed = True

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            updated += 1

    return updated


def _resolve_cross_cancellations(features: List[Dict[str, Any]]) -> int:
    """Propagate cross-cancel refs to set valid_until on the cancelled feature.

    When message B lists 'CANCEL HYDROARC NNN/YY' in its cancellations, the
    valid_until of HYDROARC NNN/YY must not exceed B's valid_from.
    Only tightens (advances) valid_until — never extends it.
    """
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    by_num_year: Dict[str, List[Dict[str, Any]]] = {}
    for feat in features:
        props = feat.get("properties") or {}
        mid = props.get("msg_id") or feat.get("id") or ""
        base_id = re.sub(r"#grp\d+$", "", mid).strip()
        if not base_id:
            continue
        by_id.setdefault(base_id, []).append(feat)
        m = re.search(r"(\d+/\d+)$", base_id)
        if m:
            by_num_year.setdefault(m.group(1), []).append(feat)

    updated = 0
    for feat in features:
        props = feat.get("properties") or {}
        cancels = props.get("cancellations") or []
        canceller_from = props.get("valid_from")
        if not cancels or not canceller_from:
            continue
        for ref in cancels:
            if not ref or "THIS" in str(ref).upper():
                continue  # skip self-cancel entries
            cancelled = by_id.get(ref) or by_num_year.get(ref)
            if not cancelled:
                continue
            for cfeat in cancelled:
                cp = cfeat.get("properties") or {}
                existing = cp.get("valid_until")
                if existing is None or canceller_from < existing:
                    cp["valid_until"] = canceller_from
                    updated += 1
    return updated


def collect_features(year_dir: Path) -> List[Dict[str, Any]]:
    """Collect all GeoJSON Features from a year directory."""
    features: List[Dict[str, Any]] = []
    json_files = sorted(year_dir.rglob("*.json"))
    for path in json_files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            print(f"  SKIP (bad JSON): {path}", file=sys.stderr)
            continue

        if not isinstance(data, dict):
            continue

        feat_type = data.get("type")
        if feat_type == "Feature":
            props = data.get("properties") or {}
            _clear_invalid_valid_until(props)
            data["properties"] = _enrich_properties(props)
            features.append(data)
        elif feat_type == "FeatureCollection":
            for feat in data.get("features") or []:
                if isinstance(feat, dict):
                    props = feat.get("properties") or {}
                    _clear_invalid_valid_until(props)
                    feat["properties"] = _enrich_properties(props)
                    features.append(feat)
    return features


def build_archive(
    year: int,
    year_dir: Path,
    output_dir: Path,
    *,
    extra_cancel_dirs: Optional[List[Path]] = None,
) -> int:
    """Build a single archive file. Returns feature count.

    ``extra_cancel_dirs`` lists directories whose features are included when
    resolving cross-cancellations but excluded from the archive output.  When
    *None* (the default) only ``current/navwarns/`` is used so that cancellers
    not yet moved to history are still applied.
    """
    features = collect_features(year_dir)
    if not features:
        print(f"  {year}: no features found, skipping")
        return 0

    before = len(features)
    features = _deduplicate_features(features)
    if before != len(features):
        print(f"  {year}: deduplicated {before} -> {len(features)}")

    # Infer valid_from/valid_until from daily scrape presence data
    first_seen, last_seen = _scan_daily_presence(year_dir)
    if not first_seen and not last_seen:
        print(f"  {year}: no daily snapshots found for inference")
    if last_seen:
        n = _apply_last_seen(features, last_seen)
        if n:
            print(f"  {year}: inferred valid_until for {n} features from daily scrapes")
    n_files = _backfill_last_seen_in_history_files(year_dir, last_seen)
    if n_files:
        print(f"  {year}: backfilled valid_until in {n_files} history JSON files")
    if first_seen:
        n = _apply_first_seen(features, first_seen)
        if n:
            print(f"  {year}: inferred valid_from for {n} features from daily scrapes")

    # Resolve cancel dates from NGA broadcast-warn XML for features still missing valid_until
    xml_cancel = _load_xml_cancel_dates(HISTORY_DIR, year)
    if xml_cancel:
        n = _apply_xml_cancel_dates(features, xml_cancel)
        if n:
            print(f"  {year}: resolved valid_until for {n} features from NGA XML")

    cancel_dirs = (
        extra_cancel_dirs if extra_cancel_dirs is not None else [CURRENT_NAVWARNS_DIR]
    )
    extra_cancellers: List[Dict[str, Any]] = []
    for d in cancel_dirs:
        if d.is_dir():
            extra_cancellers.extend(collect_features(d))
    n = _resolve_cross_cancellations(features + extra_cancellers)
    if n:
        print(f"  {year}: cross-cancel resolved valid_until for {n} features")

    collection = {
        "type": "FeatureCollection",
        "features": features,
    }
    out_path = output_dir / f"archive{year}.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False)
    print(f"  {year}: {len(features)} features -> {out_path}")
    return len(features)


def build_manifest(
    year_counts: Dict[int, int],
    output_dir: Path,
) -> None:
    """Write manifest.json with available years and counts.

    Merges *year_counts* with any existing archive files on disk so
    that rebuilding a single year does not erase other years from the
    manifest.
    """
    # Discover all archiveYYYY.geojson files already on disk
    existing: Dict[int, int] = {}
    for archive_path in output_dir.glob("archive*.geojson"):
        m = re.search(r"archive(\d{4})\.geojson$", archive_path.name)
        if not m:
            continue
        yr = int(m.group(1))
        try:
            with open(archive_path, encoding="utf-8") as f:
                data = json.load(f)
            cnt = len(data.get("features") or [])
        except (json.JSONDecodeError, OSError):
            cnt = 0
        if cnt > 0:
            existing[yr] = cnt

    # Merge: rebuilt years override, existing years are preserved
    merged = {**existing, **year_counts}

    manifest = {
        "years": [
            {"year": yr, "count": cnt} for yr, cnt in sorted(merged.items()) if cnt > 0
        ],
    }
    out_path = output_dir / "manifest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  manifest -> {out_path}")


def main(years: Optional[List[int]] = None) -> None:
    """Build archives for specified years (or all)."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    if years:
        year_dirs = [(yr, HISTORY_DIR / str(yr)) for yr in years]
    else:
        year_dirs = [
            (int(d.name), d)
            for d in sorted(HISTORY_DIR.iterdir())
            if d.is_dir() and d.name.isdigit()
        ]

    year_counts: Dict[int, int] = {}
    for yr, yr_dir in year_dirs:
        if not yr_dir.is_dir():
            print(f"  {yr}: directory not found, skip")
            continue
        count = build_archive(yr, yr_dir, DOCS_DIR)
        year_counts[yr] = count

    build_manifest(year_counts, DOCS_DIR)
    total = sum(year_counts.values())
    print(f"Done: {len(year_counts)} years," f" {total} total features")


if __name__ == "__main__":
    requested = [int(a) for a in sys.argv[1:] if a.isdigit()]
    main(requested or None)
