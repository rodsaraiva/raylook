import json
from datetime import datetime, timedelta
from metrics.processors import get_date_range, analyze_votos
import os

def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def test_customer_name_not_overwritten_if_useful(monkeypatch):
    """When customer store already has a useful name, it should NOT be overwritten."""
    now = datetime(2026, 3, 16, 12, 0, 0)
    dates = get_date_range(now=now)
    today = dates["today_start"]

    # Mock customer store in memory
    store = {"5511999990001": "João Silva"}
    monkeypatch.setattr("app.services.customer_service.load_customers", lambda: dict(store))
    monkeypatch.setattr("app.services.customer_service.save_customers", lambda data: store.update(data))

    votos = [
        {
            "id": 1,
            "pollId": "poll-1",
            "voterPhone": "5511999990001",
            "voterName": "João Silva Novo",
            "qty": "1",
            "timestamp": _ts(today + timedelta(hours=1)),
        },
    ]

    enquetes_map = {}
    analyze_votos(votos, dates, enquetes_map)

    # The original useful name should be preserved
    assert store["5511999990001"] == "João Silva"


def test_customer_name_overwrites_generic(monkeypatch):
    now = datetime(2026, 3, 16, 12, 0, 0)
    dates = get_date_range(now=now)
    today = dates["today_start"]

    store = {"5511999990002": "Desconhecido"}
    monkeypatch.setattr("app.services.customer_service.load_customers", lambda: dict(store))
    monkeypatch.setattr("app.services.customer_service.save_customers", lambda data: store.update(data))

    votos = [
        {
            "id": 2,
            "pollId": "poll-1",
            "voterPhone": "5511999990002",
            "voterName": "João Silva",
            "qty": "1",
            "timestamp": _ts(today + timedelta(hours=1)),
        },
    ]

    enquetes_map = {}
    analyze_votos(votos, dates, enquetes_map)

    assert store["5511999990002"] == "João Silva"


def test_customer_name_last_one_wins_in_batch_if_new(monkeypatch):
    now = datetime(2026, 3, 16, 12, 0, 0)
    dates = get_date_range(now=now)
    today = dates["today_start"]

    store = {}
    monkeypatch.setattr("app.services.customer_service.load_customers", lambda: dict(store))
    monkeypatch.setattr("app.services.customer_service.save_customers", lambda data: store.update(data))

    votos = [
        {
            "id": 3,
            "pollId": "poll-1",
            "voterPhone": "5511999990003",
            "voterName": "João Primeiro",
            "qty": "1",
            "timestamp": _ts(today + timedelta(hours=1)),
        },
        {
            "id": 4,
            "pollId": "poll-2",
            "voterPhone": "5511999990003",
            "voterName": "João Segundo",
            "qty": "1",
            "timestamp": _ts(today + timedelta(hours=2)),
        },
    ]

    enquetes_map = {}
    analyze_votos(votos, dates, enquetes_map)

    assert store["5511999990003"] == "João Segundo"
