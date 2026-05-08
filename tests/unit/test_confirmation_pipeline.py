import asyncio

from app.services import confirmation_pipeline as svc


def test_run_post_confirmation_effects_resolves_postgres_charges(monkeypatch):
    """Pós-confirmação resolve as charges no Postgres (sem enfileirar WhatsApp,
    que foi removido). Garante que FinanceManager legada não é usada quando
    a fonte do Postgres retorna charges."""
    moved = {
        "id": "legacy-pkg-1",
        "source_package_id": "source-pkg-1",
        "poll_title": "[teste] Roupa Vermelha - 10 reais PMG",
        "votes": [
            {"phone": "5511999990001", "name": "Ana", "qty": 12},
            {"phone": "5511999990002", "name": "Bia", "qty": 12},
        ],
        "pdf_status": "sent",
        "image": "https://example.com/image.png",
    }
    list_calls = []

    async def _run():
        await svc.run_post_confirmation_effects(
            moved,
            "source-pkg-1",
            metrics_data_to_save=None,
            persist_confirmed_package=False,
        )

    def _list_charges(package_id):
        list_calls.append(package_id)
        return [
            {
                "id": "db-charge-1",
                "package_id": package_id,
                "customer_name": "Ana",
                "customer_phone": "5511999990001",
                "quantity": 12,
                "subtotal": 120.0,
                "commission_percent": 13.0,
                "poll_title": moved["poll_title"],
                "image": moved["image"],
            }
        ]

    monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.finance_service.list_package_charges", _list_charges)
    monkeypatch.setattr("app.services.confirmed_packages_service.add_confirmed_package", lambda *_args, **_kwargs: None)

    class _UnexpectedFinanceManager:
        def __init__(self, *args, **kwargs):
            raise AssertionError("FinanceManager legada nao deveria ser usada quando ha charge no Postgres")

    monkeypatch.setattr(svc, "FinanceManager", _UnexpectedFinanceManager)

    asyncio.run(_run())

    assert list_calls == ["source-pkg-1"]
