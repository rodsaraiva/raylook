import pytest

from metrics import supabase_clients


@pytest.fixture(autouse=True)
def _reset_group_context(monkeypatch):
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: [])
    monkeypatch.setattr(supabase_clients, "is_test_group_monitoring_enabled", lambda: False)
    monkeypatch.setattr(supabase_clients, "get_test_group_monitor_started_at", lambda: None)
    monkeypatch.setattr(supabase_clients, "test_group_chat_id", lambda: "test-chat")
    monkeypatch.setattr(
        supabase_clients,
        "annotate_group",
        lambda payload, chat_id: payload.update(
            {
                "chat_id": chat_id,
                "group_kind": "test" if chat_id == "test-chat" else ("official" if chat_id == "official-chat" else "authorized"),
                "group_label": "Grupo de teste" if chat_id == "test-chat" else ("Grupo oficial" if chat_id == "official-chat" else "Grupo autorizado"),
                "is_test_group": chat_id == "test-chat",
                "is_official_group": chat_id == "official-chat",
            }
        ),
    )


def test_headers_falls_back_to_service_role_key(monkeypatch):
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_ANON_KEY", None)
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_SERVICE_ROLE_KEY", "service-role-key")

    headers = supabase_clients._headers()

    assert headers["apikey"] == "service-role-key"
    assert headers["Authorization"] == "Bearer service-role-key"


def test_fetch_package_lists_for_metrics_uses_live_supabase_packages(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-open",
                        "status": "open",
                        "sequence_no": 0,
                        "total_qty": 9,
                        "participants_count": 0,
                        "opened_at": "2026-03-23T10:00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "enquete": {
                            "id": "enquete-open",
                            "external_poll_id": "poll-open",
                            "titulo": "Produto Aberto",
                            "chat_id": "chat-open",
                            "created_at_provider": "2026-03-23T10:00:00",
                            "produto": {"drive_file_id": "drive-open"},
                        },
                    },
                    {
                        "id": "pkg-closed",
                        "status": "closed",
                        "sequence_no": 2,
                        "total_qty": 24,
                        "participants_count": 2,
                        "opened_at": "2026-03-23T08:00:00",
                        "closed_at": "2026-03-23T11:00:00",
                        "approved_at": None,
                        "enquete": {
                            "id": "enquete-closed",
                            "external_poll_id": "poll-closed",
                            "titulo": "Produto Fechado",
                            "chat_id": "chat-closed",
                            "created_at_provider": "2026-03-23T08:00:00",
                            "produto": {"drive_file_id": "drive-closed"},
                        },
                    },
                    {
                        "id": "pkg-approved",
                        "status": "approved",
                        "sequence_no": 1,
                        "total_qty": 24,
                        "participants_count": 2,
                        "opened_at": "2026-03-23T07:00:00",
                        "closed_at": "2026-03-23T09:30:00",
                        "approved_at": "2026-03-22T12:00:00",
                        "enquete": {
                            "id": "enquete-approved",
                            "external_poll_id": "poll-approved",
                            "titulo": "Produto Aprovado",
                            "chat_id": "chat-approved",
                            "created_at_provider": "2026-03-23T07:00:00",
                            "produto": {"drive_file_id": "drive-approved"},
                        },
                    },
                    {
                        "id": "pkg-cancelled",
                        "status": "cancelled",
                        "sequence_no": 1,
                        "total_qty": 24,
                        "participants_count": 2,
                        "opened_at": "2026-03-23T07:30:00",
                        "closed_at": "2026-03-23T09:00:00",
                        "approved_at": None,
                        "cancelled_at": "2026-03-23T12:10:00",
                        "updated_at": "2026-03-23T12:10:00",
                        "tag": "cancelado",
                        "pdf_status": "failed",
                        "pdf_file_name": "cancelled.pdf",
                        "pdf_sent_at": None,
                        "pdf_attempts": 2,
                        "confirmed_by": None,
                        "cancelled_by": "tester",
                        "enquete": {
                            "id": "enquete-cancelled",
                            "external_poll_id": "poll-cancelled",
                            "titulo": "Produto Cancelado",
                            "chat_id": "chat-cancelled",
                            "created_at_provider": "2026-03-23T07:30:00",
                            "produto": {"drive_file_id": "drive-cancelled"},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return [
                    {"pacote_id": "pkg-closed", "cliente_id": "cli-1", "voto_id": "vote-closed-1", "qty": 12, "cliente": {"celular": "5511", "nome": "Ana"}},
                    {"pacote_id": "pkg-closed", "cliente_id": "cli-2", "voto_id": "vote-closed-2", "qty": 12, "cliente": {"celular": "5522", "nome": "Bia"}},
                    {"pacote_id": "pkg-approved", "cliente_id": "cli-3", "voto_id": "vote-approved-1", "qty": 12, "cliente": {"celular": "5533", "nome": "Cris"}},
                ]
            if table == "votos":
                return [
                    {"id": "vote-open-1", "enquete_id": "enquete-open", "cliente_id": "cli-open-1", "qty": 3, "status": "in", "voted_at": "2026-03-23T10:05:00", "updated_at": "2026-03-23T10:05:00", "cliente": {"celular": "5591", "nome": "Dora"}},
                    {"id": "vote-open-2", "enquete_id": "enquete-open", "cliente_id": "cli-open-2", "qty": 6, "status": "in", "voted_at": "2026-03-23T10:06:00", "updated_at": "2026-03-23T10:06:00", "cliente": {"celular": "5592", "nome": "Eva"}},
                    {"id": "vote-closed-1", "enquete_id": "enquete-closed", "cliente_id": "cli-1", "qty": 12, "status": "in", "voted_at": "2026-03-23T08:10:00", "updated_at": "2026-03-23T08:10:00", "cliente": {"celular": "5511", "nome": "Ana"}},
                ]
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-03-23T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-03-23T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-03-16T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert data["packages"]["open"][0]["id"] == "poll-open_0"
    assert data["packages"]["open"][0]["source_package_id"] == "pkg-open"
    assert data["packages"]["open"][0]["image"] == "/files/drive-open"
    assert len(data["packages"]["open"][0]["votes"]) == 2
    assert data["packages"]["open"][0]["votes"][0]["name"] == "Eva"
    assert data["packages"]["closed_today"][0]["id"] == "poll-closed_1"
    assert len(data["packages"]["closed_today"][0]["votes"]) == 2
    assert data["packages"]["confirmed_today"][0]["id"] == "poll-approved_0"
    # F-051: cancelled packages without votes (pacote_clientes) are now filtered as orphans
    # The mock data has no pacote_clientes for pkg-cancelled, so rejected_today is empty
    assert len(data["packages"]["rejected_today"]) == 0
    assert data["packages_summary_confirmed"]["today"] == 1


def test_fetch_package_lists_for_metrics_uses_72h_window_for_confirmed(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-approved-recent",
                        "status": "approved",
                        "sequence_no": 1,
                        "total_qty": 24,
                        "participants_count": 1,
                        "opened_at": "2026-03-22T07:00:00",
                        "closed_at": "2026-03-22T09:30:00",
                        "approved_at": "2026-03-22T14:00:00",
                        "enquete": {
                            "id": "enquete-approved-recent",
                            "external_poll_id": "poll-approved-recent",
                            "titulo": "Produto Aprovado Recente",
                            "chat_id": "chat-approved-recent",
                            "created_at_provider": "2026-03-22T07:00:00",
                            "produto": {"drive_file_id": "drive-approved-recent"},
                        },
                    },
                    {
                        "id": "pkg-approved-old",
                        "status": "approved",
                        "sequence_no": 2,
                        "total_qty": 24,
                        "participants_count": 1,
                        "opened_at": "2026-03-20T07:00:00",
                        "closed_at": "2026-03-20T09:30:00",
                        "approved_at": "2026-03-20T08:00:00",
                        "enquete": {
                            "id": "enquete-approved-old",
                            "external_poll_id": "poll-approved-old",
                            "titulo": "Produto Aprovado Antigo",
                            "chat_id": "chat-approved-old",
                            "created_at_provider": "2026-03-20T07:00:00",
                            "produto": {"drive_file_id": "drive-approved-old"},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return []
            if table == "votos":
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-03-25T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-03-25T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-03-18T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert [pkg["id"] for pkg in data["packages"]["confirmed_today"]] == ["poll-approved-recent_0"]
    assert data["packages_summary_confirmed"]["today"] == 1


def test_fetch_enquetes_for_metrics_filters_to_official_group(monkeypatch):
    captured = {}

    def fake_get(path, params, max_retries=3):
        captured["path"] = path
        captured["params"] = params
        return [
            {
                "external_poll_id": "poll-1",
                "titulo": "Produto Oficial",
                "status": "open",
                "chat_id": "official-chat",
                "created_at_provider": "2026-03-30T10:00:00+00:00",
                "created_at": "2026-03-30T10:00:00+00:00",
                "produto": {"drive_file_id": "drive-1", "drive_folder_id": "folder-1", "nome": "Produto Oficial"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_enquetes_for_metrics()

    assert captured["path"] == "/rest/v1/enquetes"
    assert captured["params"]["chat_id"] == "eq.official-chat"
    assert rows[0]["pollId"] == "poll-1"


def test_fetch_votos_for_metrics_skips_non_official_group(monkeypatch):
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-1",
                "qty": 12,
                "status": "in",
                "voted_at": "2026-03-30T10:00:00+00:00",
                "updated_at": "2026-03-30T10:00:00+00:00",
                "enquete": {"external_poll_id": "poll-official", "titulo": "Oficial", "chat_id": "official-chat"},
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": 12, "label": "12"},
            },
            {
                "id": "vote-2",
                "qty": 24,
                "status": "in",
                "voted_at": "2026-03-30T11:00:00+00:00",
                "updated_at": "2026-03-30T11:00:00+00:00",
                "enquete": {"external_poll_id": "poll-other", "titulo": "Outro", "chat_id": None},
                "cliente": {"celular": "5522", "nome": "Bia"},
                "alternativa": {"qty": 24, "label": "24"},
            },
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_votos_for_metrics()

    assert len(rows) == 1
    assert rows[0]["pollId"] == "poll-official"


def test_fetch_package_lists_for_metrics_skips_non_official_group(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-official",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 12,
                        "participants_count": 1,
                        "opened_at": "2026-03-30T10:00:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "enquete": {
                            "id": "enquete-official",
                            "external_poll_id": "poll-official",
                            "titulo": "Oficial",
                            "chat_id": "official-chat",
                            "created_at_provider": "2026-03-30T10:00:00+00:00",
                            "produto": {"drive_file_id": "drive-official"},
                        },
                    },
                    {
                        "id": "pkg-other",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 12,
                        "participants_count": 1,
                        "opened_at": "2026-03-30T11:00:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "enquete": {
                            "id": "enquete-other",
                            "external_poll_id": "poll-other",
                            "titulo": "Outro",
                            "chat_id": None,
                            "created_at_provider": "2026-03-30T11:00:00+00:00",
                            "produto": {"drive_file_id": "drive-other"},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return []
            if table == "votos":
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-03-30T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-03-30T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-03-23T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert [pkg["id"] for pkg in data["packages"]["open"]] == ["poll-official_0"]


def test_fetch_enquetes_for_metrics_uses_test_group_and_monitor_floor(monkeypatch):
    captured = {}

    def fake_get(path, params, max_retries=3):
        captured["path"] = path
        captured["params"] = params
        return [
            {
                "external_poll_id": "poll-old",
                "titulo": "Teste antigo",
                "status": "open",
                "chat_id": "test-chat",
                "created_at_provider": "2026-03-30T09:59:00+00:00",
                "created_at": "2026-03-30T09:59:00+00:00",
                "produto": {"drive_file_id": "drive-old", "nome": "Teste antigo"},
            },
            {
                "external_poll_id": "poll-new",
                "titulo": "Teste novo",
                "status": "open",
                "chat_id": "test-chat",
                "created_at_provider": "2026-03-30T10:01:00+00:00",
                "created_at": "2026-03-30T10:01:00+00:00",
                "produto": {"drive_file_id": "drive-new", "nome": "Teste novo"},
            },
            {
                "external_poll_id": "poll-official",
                "titulo": "Oficial",
                "status": "open",
                "chat_id": "official-chat",
                "created_at_provider": "2026-03-30T09:30:00+00:00",
                "created_at": "2026-03-30T09:30:00+00:00",
                "produto": {"drive_file_id": "drive-official", "nome": "Oficial"},
            },
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat", "test-chat"])
    monkeypatch.setattr(supabase_clients, "is_test_group_monitoring_enabled", lambda: True)
    monkeypatch.setattr(
        supabase_clients,
        "get_test_group_monitor_started_at",
        lambda: "2026-03-30T10:00:00+00:00",
    )

    rows = supabase_clients.fetch_enquetes_for_metrics()

    assert captured["path"] == "/rest/v1/enquetes"
    assert "chat_id" not in captured["params"]
    assert [row["pollId"] for row in rows] == ["poll-new", "poll-official"]
    assert rows[0]["group_label"] == "Grupo de teste"
    assert rows[0]["is_test_group"] is True
    assert rows[1]["group_label"] == "Grupo oficial"
    assert rows[1]["is_official_group"] is True


def test_fetch_enquetes_for_metrics_uses_postgrest_root_path(monkeypatch):
    """fetch_enquetes_for_metrics always passes /rest/v1/enquetes to _get.
    When SUPABASE_REST_PATH is empty, _normalize_rest_path inside _get strips
    /rest/v1 before building the URL. But since we mock _get directly,
    we see the raw path before normalization."""
    captured = {}

    def fake_get(path, params, max_retries=3):
        captured["path"] = path
        captured["params"] = params
        return [
            {
                "external_poll_id": "poll-1",
                "titulo": "Produto Oficial",
                "status": "open",
                "chat_id": "official-chat",
                "created_at_provider": "2026-03-30T10:00:00+00:00",
                "created_at": "2026-03-30T10:00:00+00:00",
                "produto": {"drive_file_id": "drive-1", "drive_folder_id": "folder-1", "nome": "Produto Oficial"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_REST_PATH", "")

    rows = supabase_clients.fetch_enquetes_for_metrics()

    # _get receives the raw path; normalization happens inside _get
    assert captured["path"] == "/rest/v1/enquetes"
    assert captured["params"]["chat_id"] == "eq.official-chat"
    assert rows[0]["pollId"] == "poll-1"


def test_fetch_votos_for_metrics_uses_test_group_and_monitor_floor(monkeypatch):
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-old",
                "qty": 12,
                "status": "in",
                "voted_at": "2026-03-30T10:05:00+00:00",
                "updated_at": "2026-03-30T10:05:00+00:00",
                "enquete": {
                    "external_poll_id": "poll-old",
                    "titulo": "Teste antigo",
                    "chat_id": "test-chat",
                    "created_at_provider": "2026-03-30T09:59:00+00:00",
                    "created_at": "2026-03-30T09:59:00+00:00",
                },
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": 12, "label": "12"},
            },
            {
                "id": "vote-new",
                "qty": 6,
                "status": "in",
                "voted_at": "2026-03-30T10:06:00+00:00",
                "updated_at": "2026-03-30T10:06:00+00:00",
                "enquete": {
                    "external_poll_id": "poll-new",
                    "titulo": "Teste novo",
                    "chat_id": "test-chat",
                    "created_at_provider": "2026-03-30T10:01:00+00:00",
                    "created_at": "2026-03-30T10:01:00+00:00",
                },
                "cliente": {"celular": "5522", "nome": "Bia"},
                "alternativa": {"qty": 6, "label": "6"},
            },
            {
                "id": "vote-official",
                "qty": 3,
                "status": "in",
                "voted_at": "2026-03-30T10:07:00+00:00",
                "updated_at": "2026-03-30T10:07:00+00:00",
                "enquete": {
                    "external_poll_id": "poll-official",
                    "titulo": "Oficial",
                    "chat_id": "official-chat",
                    "created_at_provider": "2026-03-30T09:30:00+00:00",
                    "created_at": "2026-03-30T09:30:00+00:00",
                },
                "cliente": {"celular": "5533", "nome": "Cris"},
                "alternativa": {"qty": 3, "label": "3"},
            },
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat", "test-chat"])
    monkeypatch.setattr(supabase_clients, "is_test_group_monitoring_enabled", lambda: True)
    monkeypatch.setattr(
        supabase_clients,
        "get_test_group_monitor_started_at",
        lambda: "2026-03-30T10:00:00+00:00",
    )

    rows = supabase_clients.fetch_votos_for_metrics()

    assert [row["id"] for row in rows] == ["vote-new", "vote-official"]
    assert rows[0]["pollId"] == "poll-new"
    assert rows[0]["group_label"] == "Grupo de teste"
    assert rows[0]["is_test_group"] is True
    assert rows[1]["group_label"] == "Grupo oficial"
    assert rows[1]["is_official_group"] is True


def test_fetch_package_lists_for_metrics_uses_test_group_and_monitor_floor(monkeypatch):
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-old",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 12,
                        "participants_count": 1,
                        "opened_at": "2026-03-30T09:58:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-03-30T09:58:00+00:00",
                        "enquete": {
                            "id": "enquete-old",
                            "external_poll_id": "poll-old",
                            "titulo": "Teste antigo",
                            "chat_id": "test-chat",
                            "created_at_provider": "2026-03-30T09:58:00+00:00",
                            "produto": {"drive_file_id": "drive-old"},
                        },
                    },
                    {
                        "id": "pkg-new",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 9,
                        "participants_count": 1,
                        "opened_at": "2026-03-30T10:05:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-03-30T10:05:00+00:00",
                        "enquete": {
                            "id": "enquete-new",
                            "external_poll_id": "poll-new",
                            "titulo": "Teste novo",
                            "chat_id": "test-chat",
                            "created_at_provider": "2026-03-30T10:05:00+00:00",
                            "produto": {"drive_file_id": "drive-new"},
                        },
                    },
                    {
                        "id": "pkg-official",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 9,
                        "participants_count": 1,
                        "opened_at": "2026-03-30T10:10:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-03-30T10:10:00+00:00",
                        "enquete": {
                            "id": "enquete-official",
                            "external_poll_id": "poll-official",
                            "titulo": "Oficial",
                            "chat_id": "official-chat",
                            "created_at_provider": "2026-03-30T10:10:00+00:00",
                            "produto": {"drive_file_id": "drive-official"},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return []
            if table == "votos":
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat", "test-chat"])
    monkeypatch.setattr(supabase_clients, "is_test_group_monitoring_enabled", lambda: True)
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients,
        "get_test_group_monitor_started_at",
        lambda: "2026-03-30T10:00:00+00:00",
    )
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-03-30T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-03-30T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-03-23T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert [pkg["id"] for pkg in data["packages"]["open"]] == ["poll-official_0", "poll-new_0"]
    assert data["packages"]["open"][0]["group_label"] == "Grupo oficial"
    assert data["packages"]["open"][0]["is_official_group"] is True
    assert data["packages"]["open"][1]["group_label"] == "Grupo de teste"
    assert data["packages"]["open"][1]["is_test_group"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Helpers: _headers, _base_url, _normalize_rest_path
# ──────────────────────────────────────────────────────────────────────────────

def test_headers_raises_when_no_key_configured(monkeypatch):
    """_headers() lança RuntimeError quando nem anon nem service-role estão setadas."""
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_ANON_KEY", None)
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_SERVICE_ROLE_KEY", None)

    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="SUPABASE_ANON_KEY"):
        supabase_clients._headers()


def test_base_url_raises_when_not_configured(monkeypatch):
    """_base_url() lança RuntimeError quando SUPABASE_URL está ausente."""
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_URL", None)

    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="SUPABASE_URL"):
        supabase_clients._base_url()


def test_base_url_strips_trailing_slash(monkeypatch):
    """_base_url() remove barra final da URL."""
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_URL", "https://example.supabase.co/")

    result = supabase_clients._base_url()

    assert result == "https://example.supabase.co"


def test_normalize_rest_path_strips_prefix_when_rest_path_empty(monkeypatch):
    """_normalize_rest_path() remove /rest/v1 quando SUPABASE_REST_PATH está vazio."""
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_REST_PATH", "")

    result = supabase_clients._normalize_rest_path("/rest/v1/enquetes")

    assert result == "/enquetes"


def test_normalize_rest_path_returns_root_when_only_prefix(monkeypatch):
    """_normalize_rest_path() retorna '/' quando path é exatamente /rest/v1."""
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_REST_PATH", "")

    result = supabase_clients._normalize_rest_path("/rest/v1")

    assert result == "/"


def test_normalize_rest_path_keeps_path_when_rest_path_set(monkeypatch):
    """_normalize_rest_path() não modifica o path quando SUPABASE_REST_PATH está configurado."""
    monkeypatch.setattr(supabase_clients.settings, "SUPABASE_REST_PATH", "/rest/v1")

    result = supabase_clients._normalize_rest_path("/rest/v1/enquetes")

    assert result == "/rest/v1/enquetes"


# ──────────────────────────────────────────────────────────────────────────────
# Helper: _test_group_monitor_floor, _include_for_test_group_monitoring
# ──────────────────────────────────────────────────────────────────────────────

def test_test_group_monitor_floor_returns_none_when_not_started(monkeypatch):
    """_test_group_monitor_floor() retorna None quando monitoramento não foi iniciado."""
    monkeypatch.setattr(supabase_clients, "get_test_group_monitor_started_at", lambda: None)

    result = supabase_clients._test_group_monitor_floor()

    assert result is None


def test_include_for_test_group_monitoring_true_when_floor_none(monkeypatch):
    """_include_for_test_group_monitoring() retorna True quando não há floor definido."""
    monkeypatch.setattr(supabase_clients, "get_test_group_monitor_started_at", lambda: None)

    result = supabase_clients._include_for_test_group_monitoring("2026-03-30T10:00:00+00:00")

    assert result is True


def test_include_for_test_group_monitoring_false_all_before_floor(monkeypatch):
    """_include_for_test_group_monitoring() retorna False quando todos os timestamps são anteriores ao floor."""
    monkeypatch.setattr(
        supabase_clients,
        "get_test_group_monitor_started_at",
        lambda: "2026-03-30T10:00:00+00:00",
    )

    result = supabase_clients._include_for_test_group_monitoring("2026-03-30T09:59:00+00:00")

    assert result is False


# ──────────────────────────────────────────────────────────────────────────────
# Helper: _metrics_floor, _include_from_floor
# ──────────────────────────────────────────────────────────────────────────────

def test_metrics_floor_returns_none_when_min_date_empty(monkeypatch):
    """_metrics_floor() retorna None quando METRICS_MIN_DATE não está definido."""
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", "")

    result = supabase_clients._metrics_floor()

    assert result is None


def test_metrics_floor_parses_min_date(monkeypatch):
    """_metrics_floor() converte METRICS_MIN_DATE em datetime (não-None)."""
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", "2026-04-01T00:00:00+00:00")

    result = supabase_clients._metrics_floor()

    assert result is not None


def test_include_from_floor_true_when_no_floor(monkeypatch):
    """_include_from_floor() retorna True quando METRICS_MIN_DATE está vazio."""
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", "")

    result = supabase_clients._include_from_floor("2025-01-01T00:00:00+00:00")

    assert result is True


def test_include_from_floor_false_when_all_before_floor(monkeypatch):
    """_include_from_floor() retorna False quando todos os timestamps são anteriores ao floor."""
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", "2026-06-01T00:00:00+00:00")

    result = supabase_clients._include_from_floor("2026-01-01T00:00:00+00:00")

    assert result is False


def test_include_from_floor_true_when_any_after_floor(monkeypatch):
    """_include_from_floor() retorna True quando pelo menos um timestamp é posterior ao floor."""
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", "2026-03-01T00:00:00+00:00")

    result = supabase_clients._include_from_floor(
        "2026-01-01T00:00:00+00:00",  # antes
        "2026-04-01T00:00:00+00:00",  # depois
    )

    assert result is True


# ──────────────────────────────────────────────────────────────────────────────
# Helper: _legacy_package_id
# ──────────────────────────────────────────────────────────────────────────────

def test_legacy_package_id_uses_fallback_when_sequence_no_invalid():
    """_legacy_package_id() usa fallback_id quando sequence_no não é número."""
    result = supabase_clients._legacy_package_id("poll-x", "nao-numero", "fallback-123")

    assert result == "fallback-123"


def test_legacy_package_id_clamps_to_zero_when_sequence_is_zero():
    """_legacy_package_id() gera sufixo 0 quando sequence_no é 0 (max(0-1,0)=0)."""
    result = supabase_clients._legacy_package_id("poll-x", 0, "fallback-123")

    assert result == "poll-x_0"


# ──────────────────────────────────────────────────────────────────────────────
# _get(): paginação múltipla e fallback em exceção
# ──────────────────────────────────────────────────────────────────────────────

def test_get_returns_empty_on_exception(monkeypatch):
    """_get() retorna lista vazia quando o client lança exceção na primeira página."""
    class FailingClient:
        def _request(self, *args, **kwargs):
            raise ConnectionError("supabase indisponível")

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FailingClient())

    result = supabase_clients._get("/rest/v1/enquetes", params={})

    assert result == []


def test_get_paginates_multiple_pages(monkeypatch):
    """_get() encadeia páginas enquanto o batch for igual ao page_size (1000)."""
    page_size = 1000
    page1 = [{"id": f"row-{i}"} for i in range(page_size)]
    page2 = [{"id": f"row-{i}"} for i in range(page_size, page_size + 5)]
    pages = [page1, page2]
    call_count = [0]

    class FakeResp:
        def __init__(self, data):
            self._data = data
            self.text = "data"
        def json(self):
            return self._data

    class PagingClient:
        def _request(self, method, path, params, extra_headers):
            idx = call_count[0]
            call_count[0] += 1
            return FakeResp(pages[idx] if idx < len(pages) else [])

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: PagingClient())

    result = supabase_clients._get("/rest/v1/votos", params={})

    assert len(result) == page_size + 5
    assert call_count[0] == 2


def test_get_handles_non_list_response(monkeypatch):
    """_get() transforma resposta dict em lista de um elemento."""
    class FakeResp:
        text = "x"
        def json(self):
            return {"id": "single"}

    class SingleClient:
        def _request(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: SingleClient())

    result = supabase_clients._get("/rest/v1/qualquer", params={})

    assert result == [{"id": "single"}]


def test_get_handles_empty_text_response(monkeypatch):
    """_get() retorna lista vazia quando resp.text está vazio."""
    class FakeResp:
        text = ""
        def json(self):
            return []

    class EmptyClient:
        def _request(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: EmptyClient())

    result = supabase_clients._get("/rest/v1/qualquer", params={})

    assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# fetch_enquetes_for_metrics: edge cases
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_enquetes_uses_drive_file_id_from_enquete_over_produto(monkeypatch):
    """Enquete com drive_file_id próprio usa o dela, não o do produto (F-061)."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "external_poll_id": "poll-proprio",
                "titulo": "Com imagem própria",
                "status": "open",
                "chat_id": "official-chat",
                "created_at_provider": "2026-04-01T10:00:00+00:00",
                "created_at": "2026-04-01T10:00:00+00:00",
                "drive_file_id": "drive-da-enquete",
                "produto": {"drive_file_id": "drive-do-produto", "nome": "Produto X"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_enquetes_for_metrics()

    assert rows[0]["driveFileId"] == "drive-da-enquete"
    assert rows[0]["field_200"] == "drive-da-enquete"


def test_fetch_enquetes_falls_back_to_produto_drive_when_enquete_has_none(monkeypatch):
    """Enquete sem drive_file_id usa o do produto associado."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "external_poll_id": "poll-sem-drive",
                "titulo": "Sem imagem própria",
                "status": "open",
                "chat_id": "official-chat",
                "created_at_provider": "2026-04-01T10:00:00+00:00",
                "created_at": "2026-04-01T10:00:00+00:00",
                "drive_file_id": None,
                "produto": {"drive_file_id": "drive-do-produto", "nome": "Produto Y"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_enquetes_for_metrics()

    assert rows[0]["driveFileId"] == "drive-do-produto"


def test_fetch_enquetes_handles_invalid_timestamp_fallback(monkeypatch):
    """created_at_provider inválido resulta em fallback para a string original."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "external_poll_id": "poll-ts-ruim",
                "titulo": "Timestamp inválido",
                "status": "open",
                "chat_id": "official-chat",
                "created_at_provider": "nao-eh-data",
                "created_at": None,
                "drive_file_id": None,
                "produto": {},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_enquetes_for_metrics()

    # Não deve lançar exceção; createdAtTs recebe o valor original como fallback
    assert rows[0]["pollId"] == "poll-ts-ruim"
    assert rows[0]["createdAtTs"] == "nao-eh-data"


def test_fetch_enquetes_no_monitored_chat_ids_no_filter(monkeypatch):
    """Quando monitored_chat_ids retorna lista vazia, nenhum filtro chat_id é adicionado."""
    captured = {}

    def fake_get(path, params, max_retries=3):
        captured["params"] = params
        return []

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: [])

    supabase_clients.fetch_enquetes_for_metrics()

    assert "chat_id" not in captured["params"]


def test_fetch_enquetes_multiple_chat_ids_no_filter(monkeypatch):
    """Com >1 monitored_chat_ids, nenhum filtro chat_id é adicionado (feito pelo caller)."""
    captured = {}

    def fake_get(path, params, max_retries=3):
        captured["params"] = params
        return []

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["chat-a", "chat-b"])

    supabase_clients.fetch_enquetes_for_metrics()

    assert "chat_id" not in captured["params"]


# ──────────────────────────────────────────────────────────────────────────────
# fetch_votos_for_metrics: edge cases
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_votos_status_out_results_in_qty_zero(monkeypatch):
    """Voto com status='out' deve ter qty=0 independente do valor de alternativa.qty."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-out",
                "qty": 5,
                "status": "out",
                "voted_at": "2026-04-01T10:00:00+00:00",
                "updated_at": "2026-04-01T10:00:00+00:00",
                "enquete": {
                    "external_poll_id": "poll-1",
                    "chat_id": "official-chat",
                    "created_at_provider": "2026-04-01T09:00:00+00:00",
                    "created_at": "2026-04-01T09:00:00+00:00",
                },
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": 5, "label": "5kg"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_votos_for_metrics()

    assert len(rows) == 1
    assert rows[0]["qty"] == 0
    assert rows[0]["field_164"] == 0


def test_fetch_votos_uses_alternativa_qty_over_votos_qty(monkeypatch):
    """alternativa.qty tem prioridade sobre votos.qty."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-1",
                "qty": 3,        # valor no registro votos
                "status": "in",
                "voted_at": "2026-04-01T10:00:00+00:00",
                "updated_at": None,
                "enquete": {
                    "external_poll_id": "poll-1",
                    "chat_id": "official-chat",
                    "created_at_provider": "2026-04-01T09:00:00+00:00",
                    "created_at": "2026-04-01T09:00:00+00:00",
                },
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": 12, "label": "12kg"},  # valor oficial
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_votos_for_metrics()

    assert rows[0]["qty"] == 12


def test_fetch_votos_falls_back_to_votos_qty_when_alternativa_empty(monkeypatch):
    """Quando alternativa.qty é None, usa votos.qty."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-2",
                "qty": 7,
                "status": "in",
                "voted_at": "2026-04-01T10:00:00+00:00",
                "updated_at": None,
                "enquete": {
                    "external_poll_id": "poll-1",
                    "chat_id": "official-chat",
                    "created_at_provider": "2026-04-01T09:00:00+00:00",
                    "created_at": "2026-04-01T09:00:00+00:00",
                },
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": None, "label": "7kg"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_votos_for_metrics()

    assert rows[0]["qty"] == 7


def test_fetch_votos_handles_invalid_qty_as_zero(monkeypatch):
    """qty não numérico resulta em 0."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-3",
                "qty": "invalido",
                "status": "in",
                "voted_at": "2026-04-01T10:00:00+00:00",
                "updated_at": None,
                "enquete": {
                    "external_poll_id": "poll-1",
                    "chat_id": "official-chat",
                    "created_at_provider": "2026-04-01T09:00:00+00:00",
                    "created_at": "2026-04-01T09:00:00+00:00",
                },
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": "invalido", "label": "X"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_votos_for_metrics()

    assert rows[0]["qty"] == 0


def test_fetch_votos_uses_updated_at_when_voted_at_missing(monkeypatch):
    """voted_at=None → usa updated_at como timestamp."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-fallback",
                "qty": 2,
                "status": "in",
                "voted_at": None,
                "updated_at": "2026-04-01T11:00:00+00:00",
                "enquete": {
                    "external_poll_id": "poll-1",
                    "chat_id": "official-chat",
                    "created_at_provider": "2026-04-01T09:00:00+00:00",
                    "created_at": "2026-04-01T09:00:00+00:00",
                },
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": 2, "label": "2"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_votos_for_metrics()

    assert rows[0]["timestamp"] is not None


def test_fetch_votos_handles_invalid_voted_at_fallback(monkeypatch):
    """voted_at inválido resulta em fallback para a string original."""
    def fake_get(path, params, max_retries=3):
        return [
            {
                "id": "vote-bad-ts",
                "qty": 2,
                "status": "in",
                "voted_at": "nao-eh-data",
                "updated_at": None,
                "enquete": {
                    "external_poll_id": "poll-1",
                    "chat_id": "official-chat",
                    "created_at_provider": "2026-04-01T09:00:00+00:00",
                    "created_at": "2026-04-01T09:00:00+00:00",
                },
                "cliente": {"celular": "5511", "nome": "Ana"},
                "alternativa": {"qty": 2, "label": "2"},
            }
        ]

    monkeypatch.setattr(supabase_clients, "_get", fake_get)
    monkeypatch.setattr(supabase_clients, "monitored_chat_ids", lambda: ["official-chat"])

    rows = supabase_clients.fetch_votos_for_metrics()

    assert rows[0]["timestamp"] == "nao-eh-data"


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: METRICS_MIN_DATE filtra pacotes antigos
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_metrics_min_date_filters_old_packages(monkeypatch):
    """Pacotes aprovados com timestamps anteriores ao METRICS_MIN_DATE são excluídos.

    Usa pacotes 'approved' para não depender da janela 72h de pacotes 'open'.
    """
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-novo",
                        "status": "approved",
                        "sequence_no": 1,
                        "total_qty": 5,
                        "participants_count": 1,
                        "opened_at": "2026-05-09T10:00:00+00:00",
                        "closed_at": "2026-05-09T12:00:00+00:00",
                        "approved_at": "2026-05-10T10:00:00+00:00",
                        "cancelled_at": None,
                        "updated_at": "2026-05-10T10:00:00+00:00",
                        "tag": None,
                        "fornecedor": None,
                        "pdf_status": None,
                        "pdf_file_name": None,
                        "pdf_sent_at": None,
                        "pdf_attempts": 0,
                        "confirmed_by": None,
                        "cancelled_by": None,
                        "enquete": {
                            "id": "enquete-novo",
                            "external_poll_id": "poll-novo",
                            "titulo": "Produto Novo",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-09T10:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {"drive_file_id": "drive-novo"},
                        },
                    },
                    {
                        "id": "pkg-antigo",
                        "status": "approved",
                        "sequence_no": 1,
                        "total_qty": 5,
                        "participants_count": 1,
                        "opened_at": "2026-01-01T10:00:00+00:00",
                        "closed_at": "2026-01-01T12:00:00+00:00",
                        "approved_at": "2026-01-02T10:00:00+00:00",
                        "cancelled_at": None,
                        "updated_at": "2026-01-02T10:00:00+00:00",
                        "tag": None,
                        "fornecedor": None,
                        "pdf_status": None,
                        "pdf_file_name": None,
                        "pdf_sent_at": None,
                        "pdf_attempts": 0,
                        "confirmed_by": None,
                        "cancelled_by": None,
                        "enquete": {
                            "id": "enquete-antigo",
                            "external_poll_id": "poll-antigo",
                            "titulo": "Produto Antigo",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-01-01T10:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {"drive_file_id": "drive-antigo"},
                        },
                    },
                ]
            if table in ("pacote_clientes", "votos"):
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", "2026-04-01T00:00:00+00:00")
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    confirmed_ids = [pkg["id"] for pkg in data["packages"]["confirmed_today"]]
    assert "poll-novo_0" in confirmed_ids
    assert "poll-antigo_0" not in confirmed_ids


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: pacote sem poll_id é pulado
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_skips_package_without_poll_id(monkeypatch):
    """Pacote sem external_poll_id na enquete é ignorado."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-sem-poll",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 5,
                        "participants_count": 1,
                        "opened_at": "2026-05-01T10:00:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-05-01T10:00:00+00:00",
                        "enquete": {
                            "id": "enquete-sem-poll",
                            "external_poll_id": None,  # ausente
                            "titulo": "Sem Poll",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-01T10:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table in ("pacote_clientes", "votos"):
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    # Nenhuma categoria deve ter esse pacote
    all_pkgs = (
        data["packages"]["open"]
        + data["packages"]["closed_today"]
        + data["packages"]["confirmed_today"]
        + data["packages"]["rejected_today"]
    )
    assert all_pkgs == []


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: open fora da janela 72h não aparece
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_open_outside_72h_not_in_open(monkeypatch):
    """Pacote 'open' com opened_at > 72h atrás não aparece em packages['open']."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-old-open",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 5,
                        "participants_count": 1,
                        # 5 dias antes do 'now' = fora da janela 72h
                        "opened_at": "2026-05-06T10:00:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-05-06T10:00:00+00:00",
                        "enquete": {
                            "id": "enquete-old-open",
                            "external_poll_id": "poll-old-open",
                            "titulo": "Open antigo",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-06T10:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table in ("pacote_clientes", "votos"):
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert data["packages"]["open"] == []


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: closed órfão (sem pacote_clientes) é ignorado
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_closed_orphan_without_votes_ignored(monkeypatch):
    """Pacote 'closed' sem clientes (pacote_clientes vazio) é filtrado como órfão."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-closed-orphan",
                        "status": "closed",
                        "sequence_no": 1,
                        "total_qty": 10,
                        "participants_count": 0,
                        "opened_at": "2026-05-10T08:00:00+00:00",
                        "closed_at": "2026-05-10T12:00:00+00:00",
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-05-10T12:00:00+00:00",
                        "enquete": {
                            "id": "enquete-orphan",
                            "external_poll_id": "poll-orphan",
                            "titulo": "Orphan Closed",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-10T08:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table in ("pacote_clientes", "votos"):
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert data["packages"]["closed_today"] == []
    assert data["packages"]["closed_week"] == []


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: pacote 'cancelled' com votos aparece em rejected
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_cancelled_with_votes_appears_in_rejected(monkeypatch):
    """Pacote 'cancelled' com clientes e dentro de 7 dias aparece em rejected_today."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-cancelled-with-votes",
                        "status": "cancelled",
                        "sequence_no": 1,
                        "total_qty": 10,
                        "participants_count": 1,
                        "opened_at": "2026-05-08T08:00:00+00:00",
                        "closed_at": "2026-05-08T12:00:00+00:00",
                        "approved_at": None,
                        "cancelled_at": "2026-05-09T10:00:00+00:00",
                        "updated_at": "2026-05-09T10:00:00+00:00",
                        "tag": None,
                        "fornecedor": None,
                        "pdf_status": None,
                        "pdf_file_name": None,
                        "pdf_sent_at": None,
                        "pdf_attempts": 0,
                        "confirmed_by": None,
                        "cancelled_by": "operador",
                        "enquete": {
                            "id": "enquete-cancelled-v",
                            "external_poll_id": "poll-cancelled-v",
                            "titulo": "Cancelado com votos",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-08T08:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return [
                    {
                        "pacote_id": "pkg-cancelled-with-votes",
                        "cliente_id": "cli-c",
                        "voto_id": "vote-c",
                        "qty": 5,
                        "unit_price": 10.0,
                        "subtotal": 50.0,
                        "commission_amount": 5.0,
                        "total_amount": 55.0,
                        "cliente": {"celular": "5511", "nome": "Cliente C"},
                    }
                ]
            if table == "votos":
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert len(data["packages"]["rejected_today"]) == 1
    assert data["packages"]["rejected_today"][0]["poll_id"] == "poll-cancelled-v"


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: approved aparece em confirmed_last7
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_approved_counted_in_confirmed_last7(monkeypatch):
    """Pacote 'approved' com approved_dt dentro dos últimos 7 dias incrementa confirmed_last7."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-approved-hist",
                        "status": "approved",
                        "sequence_no": 1,
                        "total_qty": 20,
                        "participants_count": 2,
                        # aprovado há 3 dias (2026-05-08) = índice 2 do array
                        "opened_at": "2026-05-07T07:00:00+00:00",
                        "closed_at": "2026-05-07T09:00:00+00:00",
                        "approved_at": "2026-05-08T15:00:00+00:00",
                        "cancelled_at": None,
                        "updated_at": "2026-05-08T15:00:00+00:00",
                        "tag": None,
                        "fornecedor": None,
                        "pdf_status": None,
                        "pdf_file_name": None,
                        "pdf_sent_at": None,
                        "pdf_attempts": 0,
                        "confirmed_by": "gerente",
                        "cancelled_by": None,
                        "enquete": {
                            "id": "enquete-approved-hist",
                            "external_poll_id": "poll-approved-hist",
                            "titulo": "Aprovado histórico",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-07T07:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table in ("pacote_clientes", "votos"):
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    # now = 2026-05-11, today_start = 2026-05-11T00:00
    # approved_at = 2026-05-08T15:00 → day_start = today - 3d = 2026-05-08T00:00
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    last7 = data["packages_summary_confirmed"]["last_7_days"]
    # approved_at 2026-05-08 = today - 3 days → índice 2 (i=3 → idx=2)
    assert last7[2] == 1
    assert data["packages_summary_confirmed"]["avg_7_days"] == pytest.approx(1 / 7)


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: votos zerados são ignorados na lista open_votes
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_zero_qty_votes_excluded_from_open(monkeypatch):
    """Votos com qty=0 ou negativo não aparecem na lista de votos do pacote open."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-open-zero",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 0,
                        "participants_count": 0,
                        "opened_at": "2026-05-10T10:00:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-05-10T10:00:00+00:00",
                        "enquete": {
                            "id": "enquete-open-zero",
                            "external_poll_id": "poll-open-zero",
                            "titulo": "Open com votos zeros",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-10T10:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return []
            if table == "votos":
                return [
                    {
                        "id": "vote-zero",
                        "enquete_id": "enquete-open-zero",
                        "cliente_id": "cli-z",
                        "qty": 0,  # qty zero — deve ser ignorado
                        "status": "in",
                        "voted_at": "2026-05-10T10:05:00+00:00",
                        "updated_at": "2026-05-10T10:05:00+00:00",
                        "cliente": {"celular": "5511", "nome": "Zero"},
                    },
                    {
                        "id": "vote-out",
                        "enquete_id": "enquete-open-zero",
                        "cliente_id": "cli-o",
                        "qty": 5,
                        "status": "out",  # status out — deve ser ignorado
                        "voted_at": "2026-05-10T10:06:00+00:00",
                        "updated_at": "2026-05-10T10:06:00+00:00",
                        "cliente": {"celular": "5522", "nome": "Out"},
                    },
                ]
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    open_pkg = data["packages"]["open"]
    assert len(open_pkg) == 1
    assert open_pkg[0]["votes"] == []


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: closed na semana mas não nas 72h
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_closed_in_week_but_not_in_72h(monkeypatch):
    """Pacote closed na semana mas fora das 72h: aparece em closed_week mas não em closed_today."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-closed-week",
                        "status": "closed",
                        "sequence_no": 1,
                        "total_qty": 10,
                        "participants_count": 1,
                        # now = 2026-05-11T13:00; 72h antes = 2026-05-08T13:00
                        # semana inicia 2026-05-04; fechou 2026-05-05 = na semana, fora das 72h
                        "opened_at": "2026-05-05T08:00:00+00:00",
                        "closed_at": "2026-05-05T12:00:00+00:00",
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-05-05T12:00:00+00:00",
                        "tag": None,
                        "fornecedor": None,
                        "pdf_status": None,
                        "pdf_file_name": None,
                        "pdf_sent_at": None,
                        "pdf_attempts": 0,
                        "confirmed_by": None,
                        "cancelled_by": None,
                        "enquete": {
                            "id": "enquete-closed-week",
                            "external_poll_id": "poll-closed-week",
                            "titulo": "Fechado na semana",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-05T08:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return [
                    {
                        "pacote_id": "pkg-closed-week",
                        "cliente_id": "cli-w",
                        "voto_id": "vote-w",
                        "qty": 5,
                        "unit_price": 10.0,
                        "subtotal": 50.0,
                        "commission_amount": 5.0,
                        "total_amount": 55.0,
                        "cliente": {"celular": "5511", "nome": "Cliente W"},
                    }
                ]
            if table == "votos":
                return []
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    assert data["packages"]["closed_today"] == []
    assert len(data["packages"]["closed_week"]) == 1
    assert data["packages"]["closed_week"][0]["poll_id"] == "poll-closed-week"


# ──────────────────────────────────────────────────────────────────────────────
# fetch_package_lists_for_metrics: voto com remaining_qty=0 não entra em open_votes
# ──────────────────────────────────────────────────────────────────────────────

def test_fetch_package_lists_assigned_qty_reduces_remaining(monkeypatch):
    """Voto totalmente atribuído (remaining_qty=0) não aparece na lista de votos open."""
    class FakeClient:
        def select_all(self, table, **kwargs):
            if table == "pacotes":
                return [
                    {
                        "id": "pkg-open-full",
                        "status": "open",
                        "sequence_no": 1,
                        "total_qty": 10,
                        "participants_count": 1,
                        "opened_at": "2026-05-10T10:00:00+00:00",
                        "closed_at": None,
                        "approved_at": None,
                        "cancelled_at": None,
                        "updated_at": "2026-05-10T10:00:00+00:00",
                        "enquete": {
                            "id": "enquete-full",
                            "external_poll_id": "poll-full",
                            "titulo": "Open full",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-10T10:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                    # pacote approved que já absorveu todo o qty do cliente
                    {
                        "id": "pkg-approved-full",
                        "status": "approved",
                        "sequence_no": 2,
                        "total_qty": 10,
                        "participants_count": 1,
                        "opened_at": "2026-05-09T08:00:00+00:00",
                        "closed_at": "2026-05-09T10:00:00+00:00",
                        "approved_at": "2026-05-10T09:00:00+00:00",
                        "cancelled_at": None,
                        "updated_at": "2026-05-10T09:00:00+00:00",
                        "tag": None,
                        "fornecedor": None,
                        "pdf_status": None,
                        "pdf_file_name": None,
                        "pdf_sent_at": None,
                        "pdf_attempts": 0,
                        "confirmed_by": "gerente",
                        "cancelled_by": None,
                        "enquete": {
                            "id": "enquete-full",  # mesma enquete!
                            "external_poll_id": "poll-full",
                            "titulo": "Open full",
                            "chat_id": "chat-ok",
                            "created_at_provider": "2026-05-10T10:00:00+00:00",
                            "drive_file_id": None,
                            "produto": {},
                        },
                    },
                ]
            if table == "pacote_clientes":
                return [
                    # cliente já foi totalmente atribuído ao pacote approved
                    {
                        "pacote_id": "pkg-approved-full",
                        "cliente_id": "cli-full",
                        "voto_id": "vote-full",
                        "qty": 10,
                        "unit_price": 10.0,
                        "subtotal": 100.0,
                        "commission_amount": 10.0,
                        "total_amount": 110.0,
                        "cliente": {"celular": "5511", "nome": "Full"},
                    }
                ]
            if table == "votos":
                return [
                    {
                        "id": "vote-full",
                        "enquete_id": "enquete-full",
                        "cliente_id": "cli-full",
                        "qty": 10,  # mesmo qty já atribuído
                        "status": "in",
                        "voted_at": "2026-05-10T10:05:00+00:00",
                        "updated_at": "2026-05-10T10:05:00+00:00",
                        "cliente": {"celular": "5511", "nome": "Full"},
                    },
                ]
            raise AssertionError(table)

    monkeypatch.setattr(supabase_clients.SupabaseRestClient, "from_settings", lambda: FakeClient())
    monkeypatch.setattr(supabase_clients.settings, "METRICS_MIN_DATE", None)
    monkeypatch.setattr(
        supabase_clients.processors,
        "get_date_range",
        lambda: {
            "now": supabase_clients.processors.parse_timestamp("2026-05-11T13:00:00"),
            "today_start": supabase_clients.processors.parse_timestamp("2026-05-11T00:00:00"),
            "week_start": supabase_clients.processors.parse_timestamp("2026-05-04T00:00:00"),
        },
    )

    data = supabase_clients.fetch_package_lists_for_metrics()

    open_pkg = next(
        (p for p in data["packages"]["open"] if p["poll_id"] == "poll-full"), None
    )
    assert open_pkg is not None
    # O voto foi 100% atribuído → remaining_qty=0 → não aparece na lista
    assert open_pkg["votes"] == []
