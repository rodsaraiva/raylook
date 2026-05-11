"""Service virou no-op desde F-051 — rejected_today é populado pelo
fetch_package_lists_for_metrics direto do Postgres. Restam só smoke tests
da interface pra não esquecermos se algum chamador ainda existe."""

from app.services import rejected_packages_service as svc


def test_load_rejected_packages_returns_empty_list():
    assert svc.load_rejected_packages() == []


def test_save_rejected_packages_is_noop():
    svc.save_rejected_packages([{"id": "pkg-1"}])
    # No side effect: load continua vazio.
    assert svc.load_rejected_packages() == []


def test_add_rejected_package_is_noop():
    svc.add_rejected_package({"id": "pkg-1", "status": "rejected"})
    assert svc.load_rejected_packages() == []


def test_get_rejected_package_returns_none():
    assert svc.get_rejected_package("pkg-1") is None


def test_merge_rejected_into_metrics_returns_metrics_unchanged():
    metrics = {
        "votos": {
            "packages": {
                "open": [],
                "closed_today": [{"id": "pkg-1"}],
                "confirmed_today": [{"id": "pkg-2"}],
                "rejected_today": [{"id": "pkg-3"}],
            }
        }
    }
    merged = svc.merge_rejected_into_metrics(metrics)
    assert merged is metrics
    assert merged["votos"]["packages"]["rejected_today"] == [{"id": "pkg-3"}]
