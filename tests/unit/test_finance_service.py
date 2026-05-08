import pytest

from app.services import finance_service


@pytest.fixture(autouse=True)
def _group_context_defaults(monkeypatch):
    monkeypatch.setattr(finance_service, "monitored_chat_ids", lambda: [])
    monkeypatch.setattr(finance_service, "test_group_chat_id", lambda: "test-chat")


def test_list_charges_batches_large_in_filters(monkeypatch):
    captured_batches = {"vendas": [], "clientes": [], "produtos": []}

    class FakeClient:
        def select_all(self, table, **kwargs):
            filters = kwargs.get("filters") or []
            batch_ids = []
            if filters:
                batch_ids = list(filters[0][2])
            if table == "pagamentos":
                return [
                    {
                        "id": f"pg-{idx}",
                        "venda_id": f"v-{idx}",
                        "provider_payment_id": f"pay-{idx}",
                        "status": "paid" if idx % 2 == 0 else "sent",
                        "created_at": "2026-03-25T00:00:00+00:00",
                        "updated_at": "2026-03-25T00:00:00+00:00",
                        "payload_json": None,
                    }
                    for idx in range(450)
                ]
            if table == "vendas":
                captured_batches["vendas"].append(len(batch_ids))
                return [
                    {
                        "id": venda_id,
                        "pacote_id": f"pkg-{venda_id}",
                        "cliente_id": f"c-{venda_id}",
                        "produto_id": f"p-{venda_id}",
                        "qty": 3,
                        "subtotal": 100,
                        "commission_percent": 13,
                        "commission_amount": 13,
                        "total_amount": 113,
                    }
                    for venda_id in batch_ids
                ]
            if table == "clientes":
                captured_batches["clientes"].append(len(batch_ids))
                return [
                    {
                        "id": client_id,
                        "nome": f"Cliente {client_id}",
                        "celular": client_id.replace("c-v-", "55"),
                    }
                    for client_id in batch_ids
                ]
            if table == "produtos":
                captured_batches["produtos"].append(len(batch_ids))
                return [
                    {
                        "id": product_id,
                        "nome": f"Produto {product_id}",
                    }
                    for product_id in batch_ids
                ]
            raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(finance_service, "is_test_group_monitoring_enabled", lambda: False)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    charges = finance_service.list_charges()

    assert len(charges) == 450
    assert captured_batches["vendas"] == [200, 200, 50]
    assert captured_batches["clientes"] == [200, 200, 50]
    assert captured_batches["produtos"] == [200, 200, 50]


def test_list_charges_prefers_current_supabase_row_and_keeps_legacy_metadata(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pagamentos":
                return [
                    {
                        "id": "pg-1",
                        "venda_id": "v-1",
                        "provider_payment_id": "pay-1",
                        "status": "sent",
                        "created_at": "2026-03-25T10:32:28+00:00",
                        "updated_at": "2026-03-25T10:32:41+00:00",
                        "payload_json": {
                            "source": "official_runtime_api",
                            "legacy_charge_history": [
                                {
                                    "id": "legacy-paid",
                                    "package_id": "legacy-pkg",
                                    "poll_title": "Poll Legacy",
                                    "customer_name": "Cliente Legacy",
                                    "customer_phone": "5511999999999",
                                    "quantity": 9,
                                    "subtotal": 324.0,
                                    "commission_percent": 13.0,
                                    "commission_amount": 42.12,
                                    "total_amount": 366.12,
                                    "status": "paid",
                                    "created_at": "2026-03-21T23:15:04+00:00",
                                    "updated_at": "2026-03-22T11:28:22+00:00",
                                },
                                {
                                    "id": "legacy-pending",
                                    "package_id": "legacy-pkg",
                                    "poll_title": "Poll Legacy",
                                    "customer_name": "Cliente Legacy",
                                    "customer_phone": "5511999999999",
                                    "quantity": 9,
                                    "subtotal": 324.0,
                                    "commission_percent": 13.0,
                                    "commission_amount": 42.12,
                                    "total_amount": 366.12,
                                    "status": "pending",
                                    "created_at": "2026-03-25T10:32:28+00:00",
                                    "updated_at": "2026-03-25T10:32:41+00:00",
                                },
                            ],
                        },
                    }
                ]
            if table == "vendas":
                return [
                    {
                        "id": "v-1",
                        "pacote_id": "pkg-1",
                        "cliente_id": "c-1",
                        "produto_id": "p-1",
                        "qty": 9,
                        "subtotal": 324.0,
                        "commission_percent": 13.0,
                        "commission_amount": 42.12,
                        "total_amount": 366.12,
                    }
                ]
            if table == "clientes":
                return [{"id": "c-1", "nome": "Cliente Fallback", "celular": "5500000000000"}]
            if table == "produtos":
                return [{"id": "p-1", "nome": "Produto Fallback"}]
            raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(finance_service, "is_test_group_monitoring_enabled", lambda: False)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    charges = finance_service.list_charges()

    assert len(charges) == 1
    assert charges[0]["id"] == "pg-1"
    assert charges[0]["status"] == "sent"
    assert charges[0]["asaas_id"] == "pay-1"
    assert charges[0]["customer_phone"] == "5500000000000"
    # sent_at agora reflete updated_at quando status é sent/paid (data de envio no dash)
    assert charges[0].get("sent_at") == "2026-03-25T10:32:41+00:00"


def test_list_charges_recovers_group_from_legacy_package_id(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pagamentos":
                return [
                    {
                        "id": "pg-1",
                        "venda_id": "v-1",
                        "provider_payment_id": "pay-1",
                        "status": "sent",
                        "created_at": "2026-03-30T10:32:28+00:00",
                        "updated_at": "2026-03-30T10:32:41+00:00",
                        "payload_json": {
                            "source": "official_runtime_api",
                            "legacy_charge": {
                                "package_id": "poll-test_0",
                            },
                        },
                    }
                ]
            if table == "vendas":
                return [
                    {
                        "id": "v-1",
                        "pacote_id": None,
                        "cliente_id": "c-1",
                        "produto_id": "p-1",
                        "qty": 9,
                        "subtotal": 324.0,
                        "commission_percent": 13.0,
                        "commission_amount": 42.12,
                        "total_amount": 366.12,
                        "pacote": {"enquete": {"chat_id": None, "titulo": "Produto sem vínculo"}},
                    }
                ]
            if table == "clientes":
                return [{"id": "c-1", "nome": "Cliente Fallback", "celular": "5500000000000"}]
            if table == "produtos":
                return [{"id": "p-1", "nome": "Produto Fallback"}]
            raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(finance_service, "is_test_group_monitoring_enabled", lambda: False)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(finance_service, "get_poll_chat_id_by_poll_id", lambda poll_id: "test-chat" if poll_id == "poll-test" else None)
    monkeypatch.setattr(
        finance_service,
        "annotate_group",
        lambda payload, chat_id: payload.update(
            {
                "chat_id": chat_id,
                "group_kind": "test" if chat_id == "test-chat" else "official",
                "group_label": "Grupo de teste" if chat_id == "test-chat" else "Grupo oficial",
                "is_test_group": chat_id == "test-chat",
                "is_official_group": chat_id == "official-chat",
            }
        ),
    )

    charges = finance_service.list_charges()

    assert len(charges) == 1
    assert charges[0]["chat_id"] == "test-chat"
    assert charges[0]["group_label"] == "Grupo de teste"
    assert charges[0]["is_test_group"] is True


def test_get_package_charge_contexts_batches_pagamento_lookup(monkeypatch):
    captured_batches = []

    class FakeClient:
        def select_all(self, table, **kwargs):
            filters = kwargs.get("filters") or []
            batch_ids = []
            if filters:
                batch_ids = list(filters[0][2])
            if table == "vendas":
                return [
                    {
                        "id": f"v-{idx}",
                        "pacote_id": "pkg-1",
                        "qty": 3,
                        "subtotal": 100,
                        "commission_percent": 13,
                        "commission_amount": 13,
                        "total_amount": 113,
                        "cliente": {"nome": f"Cliente {idx}", "celular": f"55{idx:04d}"},
                        "produto": {"nome": f"Produto {idx}", "drive_file_id": None},
                        "pacote": {"enquete": {"chat_id": None, "titulo": f"Poll {idx}"}},
                    }
                    for idx in range(430)
                ]
            if table == "pagamentos":
                captured_batches.append(len(batch_ids))
                return [
                    {
                        "id": f"pg-{venda_id}",
                        "venda_id": venda_id,
                        "status": "sent",
                    }
                    for venda_id in batch_ids
                ]
            raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(finance_service, "is_test_group_monitoring_enabled", lambda: False)
    monkeypatch.setattr(finance_service, "build_drive_image_url", lambda file_id: None)

    rows = finance_service.get_package_charge_contexts("pkg-1")

    assert len(rows) == 430
    assert captured_batches == [200, 200, 30]


def test_list_charges_keeps_historical_rows_without_chat_id(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pagamentos":
                return [
                    {
                        "id": "pg-1",
                        "venda_id": "v-1",
                        "provider_payment_id": "pay-1",
                        "status": "paid",
                        "created_at": "2026-03-25T10:32:28+00:00",
                        "updated_at": "2026-03-25T10:32:41+00:00",
                        "payload_json": {},
                    }
                ]
            if table == "vendas":
                return [
                    {
                        "id": "v-1",
                        "pacote_id": "pkg-1",
                        "cliente_id": "c-1",
                        "produto_id": "p-1",
                        "qty": 3,
                        "subtotal": 90.0,
                        "commission_percent": 13.0,
                        "commission_amount": 11.7,
                        "total_amount": 101.7,
                        "pacote": {"enquete": {"chat_id": None, "titulo": "Produto sem chat"}},
                    }
                ]
            if table == "clientes":
                return [{"id": "c-1", "nome": "Cliente Histórico", "celular": "5511999999999"}]
            if table == "produtos":
                return [{"id": "p-1", "nome": "Produto Histórico"}]
            raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(finance_service, "monitored_chat_ids", lambda: ["official-chat", "test-chat"])
    monkeypatch.setattr(finance_service, "is_test_group_monitoring_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "test_group_chat_id", lambda: "test-chat")
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    charges = finance_service.list_charges()

    assert len(charges) == 1
    assert charges[0]["id"] == "pg-1"
    assert charges[0]["status"] == "paid"


def test_list_charges_page_supabase_fetches_only_requested_page(monkeypatch):
    captured = {}

    class FakeClient:
        def select(self, table, **kwargs):
            captured["table"] = table
            captured["limit"] = kwargs.get("limit")
            captured["offset"] = kwargs.get("offset")
            return [
                {
                    "id": "pg-3",
                    "venda_id": "v-3",
                    "provider_payment_id": "pay-3",
                    "status": "pending",
                    "created_at": "2026-03-30T10:00:00+00:00",
                    "updated_at": "2026-03-30T10:00:00+00:00",
                    "payload_json": None,
                },
                {
                    "id": "pg-4",
                    "venda_id": "v-4",
                    "provider_payment_id": "pay-4",
                    "status": "paid",
                    "created_at": "2026-03-30T09:00:00+00:00",
                    "updated_at": "2026-03-30T09:00:00+00:00",
                    "payload_json": None,
                },
                {
                    "id": "pg-5",
                    "venda_id": "v-5",
                    "provider_payment_id": "pay-5",
                    "status": "paid",
                    "created_at": "2026-03-30T08:00:00+00:00",
                    "updated_at": "2026-03-30T08:00:00+00:00",
                    "payload_json": None,
                },
            ]

        def select_all(self, table, **kwargs):
            filters = kwargs.get("filters") or []
            batch_ids = list(filters[0][2]) if filters else []
            if table == "vendas":
                return [
                    {
                        "id": venda_id,
                        "pacote_id": f"pkg-{venda_id}",
                        "cliente_id": f"c-{venda_id}",
                        "produto_id": f"p-{venda_id}",
                        "qty": 2,
                        "subtotal": 100,
                        "commission_percent": 13,
                        "commission_amount": 13,
                        "total_amount": 113,
                    }
                    for venda_id in batch_ids
                ]
            if table == "clientes":
                return [{"id": client_id, "nome": f"Cliente {client_id}", "celular": f"55{idx}"} for idx, client_id in enumerate(batch_ids)]
            if table == "produtos":
                return [{"id": product_id, "nome": f"Produto {product_id}"} for product_id in batch_ids]
            raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(finance_service, "is_test_group_monitoring_enabled", lambda: False)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: FakeClient())

    page = finance_service.list_charges_page(page=2, page_size=2)

    assert captured["table"] == "pagamentos"
    assert captured["limit"] == 3
    assert captured["offset"] == 2
    assert [row["id"] for row in page["items"]] == ["pg-3", "pg-4"]
    assert page["total"] == 5
    assert page["has_prev"] is True
    assert page["has_next"] is True


def test_list_charges_page_search_falls_back_to_filtered_in_memory_rows(monkeypatch):
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(
        finance_service,
        "list_charges",
        lambda: [
            {"id": "c1", "customer_name": "Ana", "customer_phone": "5511", "poll_title": "Vestido", "status": "paid", "created_at": "2026-03-30T10:00:00+00:00"},
            {"id": "c2", "customer_name": "Bia", "customer_phone": "5522", "poll_title": "Blusa", "status": "pending", "created_at": "2026-03-30T09:00:00+00:00"},
            {"id": "c3", "customer_name": "Ana Paula", "customer_phone": "5533", "poll_title": "Saia", "status": "paid", "created_at": "2026-03-30T08:00:00+00:00"},
        ],
    )

    page = finance_service.list_charges_page(page=1, page_size=1, status="paid", search="ana")

    assert [row["id"] for row in page["items"]] == ["c1"]
    assert page["total"] == 2
    assert page["has_prev"] is False
    assert page["has_next"] is True


def test_build_dashboard_stats_uses_updated_at_for_paid_today(monkeypatch):
    """F-065: paid_today filtra por updated_at (hora real em que o sistema
    marcou paid), não paid_at (que do Asaas vem com DATE → sempre 00:00 BRT
    e tornava a contagem inconsistente com a coluna 'Data de Pagamento'
    exibida no dash)."""
    class FrozenDateTime(finance_service.datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(2026, 3, 31, 14, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    monkeypatch.setattr(finance_service, "datetime", FrozenDateTime)
    now = finance_service.datetime.now()
    stats = finance_service.build_dashboard_stats(
        [
            {
                "status": "paid",
                "created_at": (now - finance_service.timedelta(days=1)).isoformat(),
                "updated_at": now.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(),
                # paid_at "de ontem" (como Asaas manda date-only BRT midnight)
                "paid_at": (now - finance_service.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "total_amount": 113.0,
            }
        ]
    )

    assert stats["paid_today_total"] == 113.0
    assert stats["paid_today_count"] == 1


def test_build_dashboard_stats_converts_utc_timestamps_to_brasilia_day(monkeypatch):
    class FrozenDateTime(finance_service.datetime):
        @classmethod
        def now(cls, tz=None):
            base = cls(2026, 3, 31, 12, 0, 0)
            return base.replace(tzinfo=tz) if tz else base

    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    monkeypatch.setattr(finance_service, "datetime", FrozenDateTime)

    stats = finance_service.build_dashboard_stats(
        [
            {
                "status": "pending",
                "created_at": "2026-03-31T01:30:00+00:00",
                "total_amount": 113.0,
            }
        ]
    )

    assert stats["timeline"]["30/03"]["created"] == 113.0
    assert stats["timeline"]["31/03"]["created"] == 0.0


def test_get_dashboard_stats_uses_runtime_snapshot_when_available(monkeypatch):
    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: True)
    monkeypatch.setattr(
        finance_service,
        "load_runtime_state",
        lambda key: {
            "timeline": {"30/03": {"created": 100.0, "paid": 50.0}},
            "total_pending": 25.0,
            "total_paid": 50.0,
            "total_charges": 3,
            "pending_count": 1,
            "paid_count": 2,
            "paid_today_total": 10.0,
            "paid_today_count": 1,
        },
    )

    stats = finance_service.get_dashboard_stats()

    assert stats["total_pending"] == 25.0
    assert stats["paid_today_total"] == 10.0


def test_list_charges_filters_and_marks_test_group_rows(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            filters = kwargs.get("filters") or []
            batch_ids = list(filters[0][2]) if filters else []
            if table == "pagamentos":
                return [
                    {
                        "id": "pg-test",
                        "venda_id": "v-test",
                        "provider_payment_id": "pay-test",
                        "status": "pending",
                        "created_at": "2026-03-30T10:30:00+00:00",
                        "updated_at": "2026-03-30T10:35:00+00:00",
                        "payload_json": None,
                    },
                    {
                        "id": "pg-old",
                        "venda_id": "v-old",
                        "provider_payment_id": "pay-old",
                        "status": "pending",
                        "created_at": "2026-03-30T09:40:00+00:00",
                        "updated_at": "2026-03-30T09:45:00+00:00",
                        "payload_json": None,
                    },
                    {
                        "id": "pg-official",
                        "venda_id": "v-official",
                        "provider_payment_id": "pay-official",
                        "status": "pending",
                        "created_at": "2026-03-30T10:40:00+00:00",
                        "updated_at": "2026-03-30T10:45:00+00:00",
                        "payload_json": None,
                    },
                ]
            if table == "vendas":
                rows = []
                for venda_id in batch_ids:
                    if venda_id == "v-test":
                        rows.append(
                            {
                                "id": "v-test",
                                "pacote_id": "pkg-test",
                                "cliente_id": "c-test",
                                "produto_id": "p-test",
                                "qty": 3,
                                "subtotal": 90,
                                "commission_percent": 13,
                                "commission_amount": 11.7,
                                "total_amount": 101.7,
                                "pacote": {
                                    "enquete": {
                                        "chat_id": "test-chat",
                                        "titulo": "Teste novo",
                                        "created_at_provider": "2026-03-30T10:05:00+00:00",
                                    }
                                },
                            }
                        )
                    if venda_id == "v-old":
                        rows.append(
                            {
                                "id": "v-old",
                                "pacote_id": "pkg-old",
                                "cliente_id": "c-old",
                                "produto_id": "p-old",
                                "qty": 3,
                                "subtotal": 90,
                                "commission_percent": 13,
                                "commission_amount": 11.7,
                                "total_amount": 101.7,
                                "pacote": {
                                    "enquete": {
                                        "chat_id": "test-chat",
                                        "titulo": "Teste antigo",
                                        "created_at_provider": "2026-03-30T09:40:00+00:00",
                                    }
                                },
                            }
                        )
                    if venda_id == "v-official":
                        rows.append(
                            {
                                "id": "v-official",
                                "pacote_id": "pkg-official",
                                "cliente_id": "c-official",
                                "produto_id": "p-official",
                                "qty": 3,
                                "subtotal": 90,
                                "commission_percent": 13,
                                "commission_amount": 11.7,
                                "total_amount": 101.7,
                                "pacote": {
                                    "enquete": {
                                        "chat_id": "official-chat",
                                        "titulo": "Oficial",
                                        "created_at_provider": "2026-03-30T10:10:00+00:00",
                                    }
                                },
                            }
                        )
                return rows
            if table == "clientes":
                return [{"id": client_id, "nome": f"Cliente {client_id}", "celular": f"55{idx}"} for idx, client_id in enumerate(batch_ids)]
            if table == "produtos":
                return [{"id": product_id, "nome": f"Produto {product_id}"} for product_id in batch_ids]
            raise AssertionError(f"Unexpected table {table}")

    monkeypatch.setattr(finance_service, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "runtime_state_enabled", lambda: False)
    monkeypatch.setattr(finance_service.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(finance_service, "is_test_group_monitoring_enabled", lambda: True)
    monkeypatch.setattr(finance_service, "monitored_chat_ids", lambda: ["official-chat", "test-chat"])
    monkeypatch.setattr(
        finance_service,
        "annotate_group",
        lambda payload, chat_id: payload.update(
            {
                "chat_id": chat_id,
                "group_kind": "test" if chat_id == "test-chat" else "official",
                "group_label": "Grupo de teste" if chat_id == "test-chat" else "Grupo oficial",
                "is_test_group": chat_id == "test-chat",
                "is_official_group": chat_id == "official-chat",
            }
        ),
    )
    monkeypatch.setattr(
        finance_service,
        "get_test_group_monitor_started_at",
        lambda: "2026-03-30T10:00:00+00:00",
    )

    charges = finance_service.list_charges()

    assert [row["id"] for row in charges] == ["pg-official", "pg-test"]
    assert charges[0]["chat_id"] == "official-chat"
    assert charges[0]["group_label"] == "Grupo oficial"
    assert charges[0]["is_official_group"] is True
    assert charges[1]["chat_id"] == "test-chat"
    assert charges[1]["group_label"] == "Grupo de teste"
    assert charges[1]["is_test_group"] is True
