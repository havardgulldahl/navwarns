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


def is_navwarn_valid_at(cancellations, check_date):
    """
    Check if a navwarn is valid at a given date.
    
    Args:
        cancellations: List of cancellation strings
        check_date: datetime to check validity at
    
    Returns:
        True if navwarn is valid (not cancelled) at the given date
    """
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
    assert is_navwarn_valid_at(cancellations, check_date) is True


def test_navwarn_invalid_after_cancellation():
    """Test that navwarn is invalid after its cancellation date."""
    cancellations = ["THIS MSG 171600Z SEP 25"]
    check_date = datetime(2025, 9, 17, 20, 0)  # After cancellation
    assert is_navwarn_valid_at(cancellations, check_date) is False


def test_navwarn_valid_at_exact_cancellation_time():
    """Test that navwarn is still valid at exact cancellation time."""
    cancellations = ["THIS MSG 171600Z SEP 25"]
    check_date = datetime(2025, 9, 17, 16, 0)  # Exact cancellation time
    assert is_navwarn_valid_at(cancellations, check_date) is True


def test_navwarn_with_no_cancellation():
    """Test that navwarn with no self-cancellation is always valid."""
    cancellations = ["101/24", "HYDROARC 119/25"]  # Other types of cancellations
    check_date = datetime(2025, 12, 1, 0, 0)
    assert is_navwarn_valid_at(cancellations, check_date) is True


def test_navwarn_with_empty_cancellations():
    """Test that navwarn with empty cancellations is always valid."""
    cancellations = []
    check_date = datetime(2025, 12, 1, 0, 0)
    assert is_navwarn_valid_at(cancellations, check_date) is True


def test_multiple_cancellations_with_one_expired():
    """Test navwarn with multiple cancellations where one has expired."""
    cancellations = ["101/24", "THIS MSG 141500 UTC SEP 25"]
    check_date = datetime(2025, 9, 20, 0, 0)  # After the dated cancellation
    assert is_navwarn_valid_at(cancellations, check_date) is False
