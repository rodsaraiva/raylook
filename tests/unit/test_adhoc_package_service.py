from unittest.mock import MagicMock


def test_create_phantom_poll_and_product_inserts_both(monkeypatch):
    from app.services import adhoc_package_service

    fake_client = MagicMock()
    fake_client.insert.side_effect = [
        [{"id": "PROD-1", "nome": "Vestido Floral"}],
        [{"id": "POLL-1", "titulo": "Pacote manual — Vestido Floral — 2026-04-17"}],
    ]

    monkeypatch.setattr(
        adhoc_package_service,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake_client)),
    )

    produto_id, enquete_id = adhoc_package_service.create_phantom_poll_and_product(
        product_name="Vestido Floral",
        unit_price=45.00,
        drive_file_id="DRIVE-1",
    )

    assert produto_id == "PROD-1"
    assert enquete_id == "POLL-1"
    # Primeira chamada: produtos
    args_prod = fake_client.insert.call_args_list[0]
    assert args_prod.args[0] == "produtos"
    assert args_prod.args[1]["nome"] == "Vestido Floral"
    assert args_prod.args[1]["valor_unitario"] == 45.00
    assert args_prod.args[1]["drive_file_id"] == "DRIVE-1"
    assert args_prod.args[1]["source"] == "manual"
    # Segunda: enquetes
    args_poll = fake_client.insert.call_args_list[1]
    assert args_poll.args[0] == "enquetes"
    assert args_poll.args[1]["source"] == "manual"
    assert args_poll.args[1]["produto_id"] == "PROD-1"
    assert args_poll.args[1]["drive_file_id"] == "DRIVE-1"
    assert args_poll.args[1]["external_poll_id"].startswith("manual_")


def test_create_adhoc_package_persists_pacote_votos_pacote_clientes(monkeypatch):
    from app.services import adhoc_package_service

    fake_client = MagicMock()
    inserted = []

    def fake_insert(table, payload, **kwargs):
        inserted.append((table, dict(payload)))
        if table == "produtos":
            return [{"id": "PROD-1"}]
        if table == "enquetes":
            return [{"id": "POLL-1"}]
        if table == "pacotes":
            return [{"id": "PKG-1"}]
        if table == "votos":
            n = len([t for t, _ in inserted if t == "votos"])
            return [{"id": f"VOTO-{n}"}]
        if table == "pacote_clientes":
            return [{"id": "PC-1"}]
        return [{}]

    fake_client.insert.side_effect = fake_insert

    def fake_upsert_one(table, payload, on_conflict=None):
        if table == "clientes":
            return {"id": f"CLI-{payload['celular']}", **payload}
        return payload

    fake_client.upsert_one.side_effect = fake_upsert_one

    monkeypatch.setattr(
        adhoc_package_service,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake_client)),
    )
    monkeypatch.setattr(
        adhoc_package_service, "run_post_confirmation_effects", MagicMock()
    )

    result = adhoc_package_service.create_adhoc_package(
        product_name="Vestido Floral",
        unit_price=45.00,
        drive_file_id="DRIVE-1",
        votes=[
            {"phone": "5511999999999", "qty": 10, "customer_id": None, "name": "Maria"},
            {"phone": "5511988887777", "qty": 14, "customer_id": None, "name": "João"},
        ],
    )

    assert result["package_id"] == "PKG-1"
    tables = [t for t, _ in inserted]
    assert tables.count("produtos") == 1
    assert tables.count("enquetes") == 1
    assert tables.count("pacotes") == 1
    assert tables.count("votos") == 2
    assert tables.count("pacote_clientes") == 2

    pacote_payload = next(p for t, p in inserted if t == "pacotes")
    assert pacote_payload["enquete_id"] == "POLL-1"
    assert pacote_payload["created_via"] == "adhoc"
    assert pacote_payload["total_qty"] == 24

    voto_payloads = [p for t, p in inserted if t == "votos"]
    assert all(v["synthetic"] is True for v in voto_payloads)

    pc_payloads = [p for t, p in inserted if t == "pacote_clientes"]
    pc_by_qty = {p["qty"]: p for p in pc_payloads}
    # 10 × 45 = 450; assessoria R$5 × 10 = 50; total = 500
    assert pc_by_qty[10]["subtotal"] == 450.0
    assert pc_by_qty[10]["commission_amount"] == 50.0
    assert pc_by_qty[10]["total_amount"] == 500.0


def test_create_adhoc_package_rejects_sum_not_24(monkeypatch):
    from app.services import adhoc_package_service

    monkeypatch.setattr(
        adhoc_package_service,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=MagicMock())),
    )

    import pytest
    with pytest.raises(ValueError, match="24"):
        adhoc_package_service.create_adhoc_package(
            product_name="X",
            unit_price=10.0,
            drive_file_id="D",
            votes=[{"phone": "5511999999999", "qty": 5}],
        )
