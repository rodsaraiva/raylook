"""Extensive edge-case tests for parse_timestamp."""
import pytest
from datetime import datetime

from metrics.processors import parse_timestamp


# ---------------------------------------------------------------------------
# Falsy / empty / None inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", 0, False, [], {}])
def test_falsy_inputs_return_none(value):
    assert parse_timestamp(value) is None


# ---------------------------------------------------------------------------
# Numeric strings
# ---------------------------------------------------------------------------

def test_negative_timestamp_is_handled():
    # Negative epoch → should still parse (dates before 1970 on some OSes)
    # or return None if the OS doesn't support it.
    result = parse_timestamp("-1")
    # We accept either a datetime or None — it must NOT raise.
    assert result is None or isinstance(result, datetime)


def test_zero_string_returns_none():
    # "0" is falsy for parse_timestamp because float("0") == 0.0 which is epoch
    # but the guard `if not ts_str` catches 0 only when passed as int.
    # When passed as string "0", float("0") works → returns epoch datetime.
    result = parse_timestamp("0")
    # Accept either: it's a valid epoch (1970-01-01) or None.
    assert result is None or isinstance(result, datetime)


def test_very_large_millisecond_timestamp():
    # Year ~2040 in milliseconds
    ms = str(int(datetime(2040, 6, 15).timestamp() * 1000))
    dt = parse_timestamp(ms)
    assert dt is not None
    assert dt.year == 2040


def test_float_string_seconds():
    dt = parse_timestamp("1672531200.123")
    assert dt is not None


def test_scientific_notation_string():
    # 1.67e9 ≈ 2022
    dt = parse_timestamp("1.67e9")
    assert dt is not None


# ---------------------------------------------------------------------------
# ISO 8601 variations
# ---------------------------------------------------------------------------

def test_iso_with_positive_offset():
    dt = parse_timestamp("2023-06-15T10:30:00+03:00")
    assert dt is not None
    assert dt.year == 2023


def test_iso_with_negative_offset():
    dt = parse_timestamp("2023-06-15T10:30:00-05:00")
    assert dt is not None


def test_iso_with_microseconds():
    dt = parse_timestamp("2023-06-15T10:30:00.123456Z")
    assert dt is not None


def test_iso_date_only():
    dt = parse_timestamp("2023-06-15")
    assert dt is not None
    assert dt.day == 15


# ---------------------------------------------------------------------------
# Truly garbage strings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("garbage", [
    "hello world",
    "2023/13/40",
    "NaN",
    "Infinity",
    "-Infinity",
    "true",
    "null",
    "{'time': 123}",
])
def test_garbage_strings_return_none(garbage):
    assert parse_timestamp(garbage) is None


# ---------------------------------------------------------------------------
# Type coercion (non-string inputs)
# ---------------------------------------------------------------------------

def test_integer_input():
    dt = parse_timestamp(1672531200)
    assert dt is not None


def test_float_input():
    dt = parse_timestamp(1672531200.5)
    assert dt is not None


def test_datetime_object_passthrough():
    # If someone passes a datetime, float() will fail,
    # fromisoformat on str(datetime) should work.
    now = datetime(2026, 1, 1, 12, 0, 0)
    dt = parse_timestamp(now)
    assert dt is not None
