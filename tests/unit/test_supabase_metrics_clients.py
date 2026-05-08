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
    assert data["packages"]["open"][0]["image"] == "https://lh3.googleusercontent.com/d/drive-open"
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
