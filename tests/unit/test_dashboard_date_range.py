"""Testes do helper _parse_date_range em app/routers/dashboard.py.

Função pura, BRT→UTC. Sem subir app, sem DB.
"""
import pytest
from fastapi import HTTPException

from app.routers.dashboard import _parse_date_range


def test_returns_none_when_both_missing():
    assert _parse_date_range(None, None) == (None, None)


def test_since_only_returns_brt_midnight_in_utc():
    """2026-05-11 00:00 BRT (UTC-3) == 2026-05-11 03:00 UTC."""
    since_iso, until_iso = _parse_date_range("2026-05-11", None)
    assert until_iso is None
    assert since_iso == "2026-05-11T03:00:00+00:00"


def test_until_only_uses_end_of_day_brt():
    """2026-05-11 23:59:59.999 BRT == 2026-05-12 02:59:59.999 UTC."""
    since_iso, until_iso = _parse_date_range(None, "2026-05-11")
    assert since_iso is None
    assert until_iso.startswith("2026-05-12T02:59:59")
    assert until_iso.endswith("+00:00")


def test_full_range_same_day_covers_24h():
    since_iso, until_iso = _parse_date_range("2026-05-11", "2026-05-11")
    assert since_iso == "2026-05-11T03:00:00+00:00"
    assert until_iso.startswith("2026-05-12T02:59:59")


def test_full_range_multi_day():
    since_iso, until_iso = _parse_date_range("2026-05-01", "2026-05-11")
    assert since_iso < until_iso


def test_invalid_since_returns_400():
    with pytest.raises(HTTPException) as exc:
        _parse_date_range("not-a-date", None)
    assert exc.value.status_code == 400
    assert "since" in exc.value.detail.lower()


def test_invalid_until_returns_400():
    with pytest.raises(HTTPException) as exc:
        _parse_date_range(None, "2026/05/11")
    assert exc.value.status_code == 400
    assert "until" in exc.value.detail.lower()


def test_since_greater_than_until_returns_400():
    with pytest.raises(HTTPException) as exc:
        _parse_date_range("2026-05-11", "2026-05-01")
    assert exc.value.status_code == 400
    assert "since" in exc.value.detail.lower()


def test_empty_string_skipped_like_none():
    """Query string vazia (?since=) chega como '' — não deve quebrar."""
    # Note: FastAPI passa None pra query param ausente; "" só viria se o user
    # mandar explícito. Hoje o parse trata "" como falsy (cai no if since:).
    assert _parse_date_range("", "") == (None, None)


def test_leap_year_date_accepted():
    since_iso, _ = _parse_date_range("2028-02-29", None)
    assert since_iso == "2028-02-29T03:00:00+00:00"


def test_impossible_date_returns_400():
    with pytest.raises(HTTPException) as exc:
        _parse_date_range("2026-02-30", None)
    assert exc.value.status_code == 400
