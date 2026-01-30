from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
}

# Common Russian month names/abbr (nominative and genitive), map to month number.
RU_MONTHS = {
    "ЯНВ": 1, "ЯНВАРЯ": 1,
    "ФЕВ": 2, "ФЕВРАЛЯ": 2,
    "МАР": 3, "МАРТА": 3,
    "АПР": 4, "АПРЕЛЯ": 4,
    "МАЙ": 5, "МАЯ": 5,
    "ИЮН": 6, "ИЮНЯ": 6,
    "ИЮЛ": 7, "ИЮЛЯ": 7,
    "АВГ": 8, "АВГУСТА": 8,
    "СЕН": 9, "СЕНТЯБРЯ": 9,
    "ОКТ": 10, "ОКТЯБРЯ": 10,
    "НОЯ": 11, "НОЯБРЯ": 11,
    "ДЕК": 12, "ДЕКАБРЯ": 12,
}

# Timezone offsets commonly seen in these notices.
# Update as necessary for your corpus.
TZ_OFFSETS = {
    "UTC": timezone.utc,
    "Z": timezone.utc,
    "MSK": timezone(timedelta(hours=3)),   # Moscow Standard Time
    "МСК": timezone(timedelta(hours=3)),   # Cyrillic MSK
}

def parse_navwarn_dtg(dtg_str: str) -> Optional[datetime]:
    """
    Parse NATO-style DTG strings:
      - 310601Z AUG 25
      - 030001Z SEP 25
      - 030001Z SEP 2025
    Returns aware UTC datetime or None.
    """
    if not dtg_str:
        return None

    s = dtg_str.strip().upper()

    m = re.match(r"^(\d{2})(\d{2})(\d{2})Z\s+([A-Z]{3})\s+(\d{2}|\d{4})$", s)
    if not m:
        return None

    day = int(m.group(1))
    hour = int(m.group(2))
    minute = int(m.group(3))
    mon_abbr = m.group(4)
    year_str = m.group(5)

    month = MONTHS.get(mon_abbr)
    if not month:
        return None

    if len(year_str) == 2:
        yy = int(year_str)
        year = 2000 + yy if yy <= 79 else 1900 + yy
    else:
        year = int(year_str)

    try:
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        return None

def parse_russian_time_token(token: str, context_year: Optional[int] = None) -> Optional[datetime]:
    """
    Parse Russian/local time expressions commonly found in PRIP-style messages.
    Examples:
      - "181000 МСК" -> day=18, time=10:00 MSK, month/year inferred from context? No day-of-month ambiguity.
      - "181000 МСК  ГС-" (trailing chars ignored)
    We assume the token is DDHHMM <TZ>, with optional spaces.
    If year is unknown, returns a naive 'today's month/year' guess is risky; instead return None unless a year is provided elsewhere.
    We therefore only parse time-of-day with day-of-month if we can also infer month/year from nearby context (not implemented),
    or we let the caller provide context_year (and possibly month via another function). In practice, these often indicate
    a cancellation issuance time rather than a future cancel time. We'll still parse to a datetime if we can at least get year and month.
    """
    if not token:
        return None
    s = token.strip().upper()
    # Pattern: DDHHMM <TZ>
    m = re.match(r"^(\d{2})(\d{2})(\d{2})\s*([A-ZА-Я]{2,4})$", s)
    if not m:
        return None
    day, hh, mm, tz_abbr = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    tz = TZ_OFFSETS.get(tz_abbr)
    if tz is None:
        return None
    # Without month/year we cannot construct a reliable absolute datetime; return None.
    # You can extend by passing context month/year or extracting from surrounding text.
    return None

def extract_dtgs_from_text(text: str) -> List[datetime]:
    """
    Extract absolute DTG-like tokens from arbitrary text:
      - NATO DTGs: ddHHMMZ MON YY(YY)
    Returns UTC datetimes.
    """
    results: List[datetime] = []
    if not text:
        return results
    pattern = re.compile(r"(\d{2}\d{2}\d{2}Z\s+[A-Z]{3}\s+\d{2,4})", re.IGNORECASE)
    for match in pattern.finditer(text):
        dt = parse_navwarn_dtg(match.group(1))
        if dt:
            results.append(dt)
    return results

def extract_cancellation_refs(text: str) -> List[str]:
    """
    Extract cancellation references like '113/21', '113/2021', possibly with Cyrillic 'ОТМ', 'ОТМЕНЯЕТ', etc.
    Returns normalized refs as 'NNN/YY' or 'NNN/YYYY' exactly as found (uppercased).
    """
    refs: List[str] = []
    if not text:
        return refs
    s = text.upper()
    # Typical forms: 123/21, 123/2021 (standalone or after 'ОТМ', 'ОТМЕНА', 'CANCEL', etc.)
    for m in re.finditer(r"\b(\d{1,4}/\d{2,4})\b", s):
        refs.append(m.group(1))
    # Deduplicate preserving order
    seen = set()
    out = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out

def normalize_ref_year(ref: str, default_century: int = 2000) -> Tuple[str, Optional[int]]:
    """
    Normalize ref year to 4 digits if 2-digit provided.
    Returns (normalized_ref, year_int or None).
    """
    m = re.match(r"^(\d{1,4})/(\d{2}|\d{4})$", ref)
    if not m:
        return ref, None
    num, y = m.group(1), m.group(2)
    if len(y) == 2:
        yy = int(y)
        year = default_century + yy if yy <= 79 else 1900 + yy
        return f"{num}/{year}", year
    else:
        return ref, int(y)

def extract_cancellation_dtgs(props: Dict[str, Any]) -> List[datetime]:
    """
    Extract absolute cancellation datetimes:
      - From properties['cancellations'] and body text containing NATO DTGs.
    """
    results: List[datetime] = []
    cancels = props.get("cancellations")
    if isinstance(cancels, list):
        for item in cancels:
            if isinstance(item, str):
                results.extend(extract_dtgs_from_text(item))
    body = props.get("body")
    if isinstance(body, str):
        # If desired, restrict to lines that contain 'CANCEL' / 'ОТМ' tokens.
        results.extend(extract_dtgs_from_text(body))
    # Deduplicate
    deduped: List[datetime] = []
    seen = set()
    for dt in sorted(results):
        key = dt.isoformat()
        if key not in seen:
            seen.add(key)
            deduped.append(dt)
    return deduped

def extract_cancellation_references(props: Dict[str, Any]) -> List[str]:
    """
    Extract cancellation cross-references like '113/21' from:
      - properties['cancellations']
      - properties['body']
    """
    refs: List[str] = []
    cancels = props.get("cancellations")
    if isinstance(cancels, list):
        for item in cancels:
            if isinstance(item, str):
                refs.extend(extract_cancellation_refs(item))
    body = props.get("body")
    if isinstance(body, str):
        refs.extend(extract_cancellation_refs(body))
    # Deduplicate while preserving order
    seen = set()
    out = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out

def parse_issue_dtg(props: Dict[str, Any]) -> Optional[datetime]:
    """
    Parse issuance DTG from either 'raw_dtg' NATO format or ISO 'dtg'.
    For PRIP-style raw_dtg with no time, returns None.
    """
    # ISO dtg
    dtg_iso = props.get("dtg")
    if isinstance(dtg_iso, str) and dtg_iso:
        try:
            return datetime.fromisoformat(dtg_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass
    # NATO raw_dtg
    raw_dtg = props.get("raw_dtg")
    if isinstance(raw_dtg, str):
        dt = parse_navwarn_dtg(raw_dtg)
        if dt:
            return dt
    return None

def evaluate_navwarn_cancellation(feature: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Summarize cancellation status.

    Returns:
      {
        'id': <feature id>,
        'issued_dtg': <UTC datetime or None>,
        'cancellation_times': [<UTC datetimes>],  # absolute times found in text
        'cancellation_refs': [<strings like '113/2021'>],  # cross-references
        'is_cancelled': bool,  # True if any cancellation datetime <= now
        'next_cancellation': <soonest future cancellation datetime or None>
      }

    Note:
      - 'is_cancelled' only reflects absolute times. If only references are present,
        you may need to resolve them against your dataset to determine cancel state.
    """
    now = now or datetime.now(timezone.utc)
    props = feature.get("properties", {}) if isinstance(feature, dict) else {}

    issued_dtg = parse_issue_dtg(props)
    cancellation_times = extract_cancellation_dtgs(props)
    cancellation_refs_raw = extract_cancellation_references(props)
    # Normalize 2-digit years in refs
    cancellation_refs_norm: List[str] = []
    for r in cancellation_refs_raw:
        norm, _ = normalize_ref_year(r)
        cancellation_refs_norm.append(norm)

    cancellation_times_sorted = sorted(cancellation_times)
    is_cancelled = any(dt <= now for dt in cancellation_times_sorted)
    future = [dt for dt in cancellation_times_sorted if dt > now]
    next_cancel = future[0] if future else None

    return {
        "id": feature.get("id"),
        "issued_dtg": issued_dtg,
        "cancellation_times": cancellation_times_sorted,
        "cancellation_refs": cancellation_refs_norm,
        "is_cancelled": is_cancelled,
        "next_cancellation": next_cancel,
    }

def evaluate_many(features: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    return [evaluate_navwarn_cancellation(f, now=now) for f in features]

# --- Example usage with your PRIP sample ---
if __name__ == "__main__":
    prip_sample = {
        "type": "Feature",
        "id": "PRIP MURMANSK 16/22",
        "geometry": {"type": "Point","coordinates": [39.195, 68.18666666666667]},
        "properties": {
            "dtg": None,
            "raw_dtg": "ПРИП МУРМАНСК 16/22 КАРТА 15064\r\nБАРЕНЦЕВО МОРЕ",
            "msg_id": "PRIP MURMANSK 16/22",
            "year": "2022",
            "cancellations": ["113/21"],
            "hazard_type": "general",
            "geometry_kind": "point",
            "radius_nm": None,
            "body": "1. ЗАТОНУВШЕЕ СУДНО С ЧАСТЯМИ НАД ВОДОЙ\nВ 68-11.2С 039-11.7В\n2. ОТМ 113/21 И ЭТОТ ПУНКТ=\n181000 МСК  ГС-",
            "summary": None
        }
    }

    result = evaluate_navwarn_cancellation(prip_sample)
    # Print in a JSON-friendly way
    def dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None
    print({
        "id": result["id"],
        "issued_dtg": dt_to_iso(result["issued_dtg"]),
        "cancellation_times": [dt_to_iso(d) for d in result["cancellation_times"]],
        "cancellation_refs": result["cancellation_refs"],
        "is_cancelled": result["is_cancelled"],
        "next_cancellation": dt_to_iso(result["next_cancellation"]),
    })
