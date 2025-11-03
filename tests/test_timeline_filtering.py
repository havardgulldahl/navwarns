"""
Test timeline filtering logic for NavWarns display.

This tests the JavaScript date parsing logic that filters navwarns based on cancellation dates.
"""
import re
from datetime import datetime


def parse_cancellation_date(cancel_str):
    """
    Parse cancellation date from "THIS MSG DDHHMMZ MON YY" or "THIS MSG DDHHMM UTC MON YY" format.
    
    This is a Python implementation of the JavaScript function for testing purposes.
    """
    if not cancel_str:
        return None
    
    # Match patterns like "THIS MSG 171600 UTC SEP 25" or "THIS MSG 171600Z SEP 25"
    match = re.match(r'THIS (?:MSG|MESSAGE) (\d{2})(\d{2})(\d{2})(?:Z| UTC) ([A-Z]{3}) (\d{2})', cancel_str)
    if match:
        day, hour, minute, month_str, year = match.groups()
        month_map = {
            'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
            'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
        }
        month = month_map.get(month_str)
        if month is None:
            return None
        
        full_year = 2000 + int(year)
        return datetime(full_year, month, int(day), int(hour), int(minute))
    return None


def is_navwarn_valid_at(cancellations, check_date, dtg=None, year=None):
    """
    Check if a navwarn is valid at a given date.
    
    Args:
        cancellations: List of cancellation strings
        check_date: datetime to check validity at
        dtg: ISO format start date string (optional)
        year: Year of the navwarn (used as fallback when dtg is None)
    
    Returns:
        True if navwarn is valid (not cancelled) at the given date
    """
    # Check if navwarn has started (using DTG as start date)
    if dtg:
        try:
            start_date = datetime.fromisoformat(dtg.replace('Z', '+00:00'))
            if check_date < start_date:
                return False  # This navwarn hasn't started yet
        except (ValueError, AttributeError):
            pass  # Invalid DTG, skip start date check
    elif year:
        # For navwarns without DTG, use year as heuristic
        year_start = datetime(year, 1, 1)
        if check_date < year_start:
            return False  # Before this navwarn's year
    
    # Check for self-cancellation with date (end date)
    for cancel in cancellations:
        if cancel and ('THIS MSG' in cancel or 'THIS MESSAGE' in cancel):
            cancel_date = parse_cancellation_date(cancel)
            if cancel_date and check_date > cancel_date:
                return False  # This navwarn is cancelled
    return True  # Valid at this date


def test_parse_cancellation_date_with_z():
    """Test parsing date with Z suffix."""
    cancel_str = "THIS MSG 171600Z SEP 25"
    result = parse_cancellation_date(cancel_str)
    assert result is not None
    assert result.year == 2025
    assert result.month == 9
    assert result.day == 17
    assert result.hour == 16
    assert result.minute == 0


def test_parse_cancellation_date_with_utc():
    """Test parsing date with UTC keyword."""
    cancel_str = "THIS MSG 141500 UTC SEP 25"
    result = parse_cancellation_date(cancel_str)
    assert result is not None
    assert result.year == 2025
    assert result.month == 9
    assert result.day == 14
    assert result.hour == 15
    assert result.minute == 0


def test_parse_cancellation_date_with_message():
    """Test parsing with MESSAGE instead of MSG."""
    cancel_str = "THIS MESSAGE 010900 UTC MAR 19"
    result = parse_cancellation_date(cancel_str)
    assert result is not None
    assert result.year == 2019
    assert result.month == 3
    assert result.day == 1
    assert result.hour == 9
    assert result.minute == 0


def test_parse_invalid_cancellation():
    """Test that invalid strings return None."""
    assert parse_cancellation_date("101/24") is None
    assert parse_cancellation_date("HYDROARC 119/25") is None
    assert parse_cancellation_date(None) is None
    assert parse_cancellation_date("") is None


def test_navwarn_valid_before_cancellation():
    """Test that navwarn is valid before its cancellation date."""
    cancellations = ["THIS MSG 171600Z SEP 25"]
    check_date = datetime(2025, 9, 17, 10, 0)  # Before cancellation
    assert is_navwarn_valid_at(cancellations, check_date, year=2025) is True


def test_navwarn_invalid_after_cancellation():
    """Test that navwarn is invalid after its cancellation date."""
    cancellations = ["THIS MSG 171600Z SEP 25"]
    check_date = datetime(2025, 9, 17, 20, 0)  # After cancellation
    assert is_navwarn_valid_at(cancellations, check_date, year=2025) is False


def test_navwarn_valid_at_exact_cancellation_time():
    """Test that navwarn is still valid at exact cancellation time."""
    cancellations = ["THIS MSG 171600Z SEP 25"]
    check_date = datetime(2025, 9, 17, 16, 0)  # Exact cancellation time
    assert is_navwarn_valid_at(cancellations, check_date, year=2025) is True


def test_navwarn_with_no_cancellation():
    """Test that navwarn with no self-cancellation is always valid."""
    cancellations = ["101/24", "HYDROARC 119/25"]  # Other types of cancellations
    check_date = datetime(2025, 12, 1, 0, 0)
    assert is_navwarn_valid_at(cancellations, check_date, year=2025) is True


def test_navwarn_with_empty_cancellations():
    """Test that navwarn with empty cancellations is always valid."""
    cancellations = []
    check_date = datetime(2025, 12, 1, 0, 0)
    assert is_navwarn_valid_at(cancellations, check_date, year=2025) is True


def test_multiple_cancellations_with_one_expired():
    """Test navwarn with multiple cancellations where one has expired."""
    cancellations = ["101/24", "THIS MSG 141500 UTC SEP 25"]
    check_date = datetime(2025, 9, 20, 0, 0)  # After the dated cancellation
    assert is_navwarn_valid_at(cancellations, check_date, year=2025) is False


def test_navwarn_not_started_yet():
    """Test that navwarn is not valid before its DTG start date."""
    cancellations = []
    dtg = "2025-09-23T18:42:00"  # Start date
    check_date = datetime(2025, 9, 20, 0, 0)  # Before start date
    assert is_navwarn_valid_at(cancellations, check_date, dtg=dtg, year=2025) is False


def test_navwarn_after_start_date():
    """Test that navwarn is valid after its DTG start date."""
    cancellations = []
    dtg = "2025-09-23T18:42:00"  # Start date
    check_date = datetime(2025, 9, 25, 0, 0)  # After start date
    assert is_navwarn_valid_at(cancellations, check_date, dtg=dtg, year=2025) is True


def test_navwarn_active_period():
    """Test that navwarn is valid only during its active period (start to end)."""
    cancellations = ["THIS MSG 261700Z SEP 25"]  # Cancellation on Sep 26
    dtg = "2025-09-23T18:42:00"  # Start date on Sep 23
    
    # Before start
    check_date = datetime(2025, 9, 22, 0, 0)
    assert is_navwarn_valid_at(cancellations, check_date, dtg=dtg, year=2025) is False
    
    # During active period
    check_date = datetime(2025, 9, 25, 0, 0)
    assert is_navwarn_valid_at(cancellations, check_date, dtg=dtg, year=2025) is True
    
    # After cancellation
    check_date = datetime(2025, 9, 27, 0, 0)
    assert is_navwarn_valid_at(cancellations, check_date, dtg=dtg, year=2025) is False


def test_navwarn_without_dtg_uses_year_heuristic():
    """Test that navwarn without DTG uses year as start heuristic."""
    cancellations = []
    check_date = datetime(2024, 12, 31, 23, 59)  # Before 2025
    assert is_navwarn_valid_at(cancellations, check_date, dtg=None, year=2025) is False
    
    check_date = datetime(2025, 1, 1, 0, 1)  # After year start
    assert is_navwarn_valid_at(cancellations, check_date, dtg=None, year=2025) is True


def test_navwarn_without_dtg_or_year():
    """Test that navwarn without DTG or year is always valid (no start date check)."""
    cancellations = []
    check_date = datetime(2020, 1, 1, 0, 0)  # Any date
    assert is_navwarn_valid_at(cancellations, check_date, dtg=None, year=None) is True
