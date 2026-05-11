"""
Testes unitários para app/services/domain_lookup.py.

Cobre todas as funções públicas:
- normalize_phone
- parse_legacy_package_id
- resolve_supabase_package_id
- get_poll_title_by_poll_id
- get_poll_chat_id_by_poll_id
- get_latest_vote_qty
- load_poll_chat_map
- _supabase_client (via funções que o chamam)
- _select_one (via funções que o chamam)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import pytest

from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables


# ---------------------------------------------------------------------------
# Fixture: banco com dados de referência
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_db() -> FakeSupabaseClient:
    tables = empty_tables()
    tables["enquetes"] = [
        {
            "id": "enq-uuid-1",
            "external_poll_id": "POLL-A",
            "titulo": "Cesta Básica Premium",
            "chat_id": "120363@g.us",
        },
        {
            "id": "enq-uuid-2",
            "external_poll_id": "POLL-B",
            "titulo": "Kit Higiene",
            "chat_id": "999999@g.us",
        },
    ]
    tables["pacotes"] = [
        {
            "id": "pac-uuid-1",
            "enquete_id": "enq-uuid-1",
            "sequence_no": 2,
        },
        {
            "id": "pac-uuid-2",
            "enquete_id": "enq-uuid-2",
            "sequence_no": 1,
        },
    ]
    tables["clientes"] = [
        {"id": "cli-uuid-1", "celular": "11987654321"},
        {"id": "cli-uuid-2", "celular": "21912345678"},
    ]
    tables["votos"] = [
        {"id": "voto-1", "enquete_id": "enq-uuid-1", "cliente_id": "cli-uuid-1", "qty": 3},
        {"id": "voto-2", "enquete_id": "enq-uuid-2", "cliente_id": "cli-uuid-2", "qty": 1},
    ]
    return FakeSupabaseClient(tables)


def _install(monkeypatch, fake_db: FakeSupabaseClient) -> None:
    """Injeta fake_db em todos os caminhos de from_settings usados pelo módulo."""
    monkeypatch.setattr(
        "app.services.domain_lookup.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_db),
    )
    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: fake_db),
    )
    import app.services.supabase_service as ss
    monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: True)


# ---------------------------------------------------------------------------
# normalize_phone
# ---------------------------------------------------------------------------

class TestNormalizePhone:

    def test_remove_caracteres_nao_numericos(self):
        from app.services.domain_lookup import normalize_phone
        assert normalize_phone("+55 (11) 98765-4321") == "5511987654321"

    def test_string_somente_digitos_permanece(self):
        from app.services.domain_lookup import normalize_phone
        assert normalize_phone("11987654321") == "11987654321"

    def test_string_vazia(self):
        from app.services.domain_lookup import normalize_phone
        assert normalize_phone("") == ""

    def test_none_retorna_string_vazia(self):
        from app.services.domain_lookup import normalize_phone
        # none ou falsy é tratado via `phone or ""`
        assert normalize_phone(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_legacy_package_id
# ---------------------------------------------------------------------------

class TestParseLegacyPackageId:

    def test_formato_valido(self):
        from app.services.domain_lookup import parse_legacy_package_id
        poll_id, seq = parse_legacy_package_id("POLL-A_1")
        assert poll_id == "POLL-A"
        assert seq == 2  # int(1) + 1

    def test_indice_zero(self):
        from app.services.domain_lookup import parse_legacy_package_id
        poll_id, seq = parse_legacy_package_id("POLL-X_0")
        assert poll_id == "POLL-X"
        assert seq == 1

    def test_string_vazia_retorna_none(self):
        from app.services.domain_lookup import parse_legacy_package_id
        assert parse_legacy_package_id("") == (None, None)

    def test_sem_underscore_retorna_none(self):
        from app.services.domain_lookup import parse_legacy_package_id
        assert parse_legacy_package_id("semunderscore") == (None, None)

    def test_sufixo_nao_numerico_retorna_none(self):
        from app.services.domain_lookup import parse_legacy_package_id
        assert parse_legacy_package_id("POLL-A_abc") == (None, None)

    def test_underscore_no_inicio(self):
        from app.services.domain_lookup import parse_legacy_package_id
        # "_0" → head="" → inválido
        assert parse_legacy_package_id("_0") == (None, None)

    def test_multiplos_underscores_usa_ultimo(self):
        from app.services.domain_lookup import parse_legacy_package_id
        # rpartition usa a última ocorrência
        poll_id, seq = parse_legacy_package_id("A_B_C_3")
        assert poll_id == "A_B_C"
        assert seq == 4


# ---------------------------------------------------------------------------
# resolve_supabase_package_id
# ---------------------------------------------------------------------------

class TestResolveSupabasePackageId:

    def test_uuid_direto_retorna_proprio_uuid(self, monkeypatch, fake_db):
        """UUID válido é retornado diretamente sem bater no banco."""
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import resolve_supabase_package_id
        uid = "12345678-1234-5678-1234-567812345678"
        assert resolve_supabase_package_id(uid) == uid

    def test_legacy_key_resolve_para_uuid_pacote(self, monkeypatch, fake_db):
        """Chave legada POLL-A_1 → enquete enq-uuid-1 + seq 2 → pac-uuid-1."""
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import resolve_supabase_package_id
        result = resolve_supabase_package_id("POLL-A_1")
        assert result == "pac-uuid-1"

    def test_string_vazia_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import resolve_supabase_package_id
        assert resolve_supabase_package_id("") is None

    def test_chave_invalida_sem_underscore_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import resolve_supabase_package_id
        assert resolve_supabase_package_id("INVALIDO") is None

    def test_poll_inexistente_retorna_none(self, monkeypatch, fake_db):
        """Enquete não encontrada no banco → retorna None."""
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import resolve_supabase_package_id
        assert resolve_supabase_package_id("NAOEXISTE_0") is None

    def test_pacote_inexistente_retorna_none(self, monkeypatch, fake_db):
        """Enquete existe mas pacote com sequence_no não existe → retorna None."""
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import resolve_supabase_package_id
        # POLL-A com seq_no=99 (índice 98) não existe
        assert resolve_supabase_package_id("POLL-A_98") is None

    def test_dominio_desabilitado_retorna_none(self, monkeypatch, fake_db):
        """Domínio Supabase desabilitado → retorna None sem bater no banco."""
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: False)
        from app.services.domain_lookup import resolve_supabase_package_id
        assert resolve_supabase_package_id("POLL-A_0") is None


# ---------------------------------------------------------------------------
# get_poll_title_by_poll_id
# ---------------------------------------------------------------------------

class TestGetPollTitleByPollId:

    def test_retorna_titulo_existente(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_poll_title_by_poll_id
        assert get_poll_title_by_poll_id("POLL-A") == "Cesta Básica Premium"

    def test_poll_inexistente_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_poll_title_by_poll_id
        assert get_poll_title_by_poll_id("NAO_EXISTE") is None

    def test_dominio_desabilitado_retorna_none(self, monkeypatch):
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: False)
        from app.services.domain_lookup import get_poll_title_by_poll_id
        assert get_poll_title_by_poll_id("POLL-A") is None

    def test_titulo_em_branco_retorna_none(self, monkeypatch):
        """Título armazenado como string vazia deve retornar None."""
        tables = empty_tables()
        tables["enquetes"] = [{"id": "e1", "external_poll_id": "P1", "titulo": "", "chat_id": "c1"}]
        blank_db = FakeSupabaseClient(tables)
        _install(monkeypatch, blank_db)
        from app.services.domain_lookup import get_poll_title_by_poll_id
        assert get_poll_title_by_poll_id("P1") is None

    def test_exception_retorna_none(self, monkeypatch):
        """Exceção em client.select deve ser capturada e retornar None."""
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: True)

        class ExplodingClient:
            def select(self, *a, **kw):
                raise RuntimeError("DB offline")

        monkeypatch.setattr(
            "app.services.domain_lookup.SupabaseRestClient.from_settings",
            staticmethod(lambda: ExplodingClient()),
        )
        from app.services.domain_lookup import get_poll_title_by_poll_id
        assert get_poll_title_by_poll_id("POLL-A") is None


# ---------------------------------------------------------------------------
# get_poll_chat_id_by_poll_id
# ---------------------------------------------------------------------------

class TestGetPollChatIdByPollId:

    def test_retorna_chat_id_existente(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_poll_chat_id_by_poll_id
        assert get_poll_chat_id_by_poll_id("POLL-A") == "120363@g.us"

    def test_poll_inexistente_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_poll_chat_id_by_poll_id
        assert get_poll_chat_id_by_poll_id("NAO_EXISTE") is None

    def test_dominio_desabilitado_retorna_none(self, monkeypatch):
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: False)
        from app.services.domain_lookup import get_poll_chat_id_by_poll_id
        assert get_poll_chat_id_by_poll_id("POLL-A") is None

    def test_exception_retorna_none(self, monkeypatch):
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: True)

        class ExplodingClient:
            def select(self, *a, **kw):
                raise RuntimeError("DB offline")

        monkeypatch.setattr(
            "app.services.domain_lookup.SupabaseRestClient.from_settings",
            staticmethod(lambda: ExplodingClient()),
        )
        from app.services.domain_lookup import get_poll_chat_id_by_poll_id
        assert get_poll_chat_id_by_poll_id("POLL-A") is None


# ---------------------------------------------------------------------------
# get_latest_vote_qty
# ---------------------------------------------------------------------------

class TestGetLatestVoteQty:

    def test_retorna_qty_correto(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("POLL-A", "11987654321") == 3

    def test_phone_com_formatacao(self, monkeypatch, fake_db):
        """Phone com máscara deve ser normalizado antes da busca — usa número exato do banco."""
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_latest_vote_qty
        # normalize_phone("(11) 98765-4321") == "11987654321" que é o valor no banco
        assert get_latest_vote_qty("POLL-A", "(11) 98765-4321") == 3

    def test_poll_inexistente_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("NAO_EXISTE", "11987654321") is None

    def test_cliente_inexistente_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("POLL-A", "00000000000") is None

    def test_poll_id_vazio_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("", "11987654321") is None

    def test_phone_vazio_retorna_none(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("POLL-A", "") is None

    def test_dominio_desabilitado_retorna_none(self, monkeypatch):
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: False)
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("POLL-A", "11987654321") is None

    def test_voto_sem_match_retorna_none(self, monkeypatch, fake_db):
        """Enquete e cliente existem mas não há voto cruzado → retorna None."""
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import get_latest_vote_qty
        # POLL-B com cliente 1 (que só votou em POLL-A)
        assert get_latest_vote_qty("POLL-B", "11987654321") is None

    def test_qty_como_float_converte_para_int(self, monkeypatch):
        """qty=2.7 deve ser retornado como int(2)."""
        tables = empty_tables()
        tables["enquetes"] = [{"id": "e1", "external_poll_id": "P1", "titulo": "T", "chat_id": "c"}]
        tables["clientes"] = [{"id": "c1", "celular": "11111111111"}]
        tables["votos"] = [{"id": "v1", "enquete_id": "e1", "cliente_id": "c1", "qty": 2.7}]
        float_db = FakeSupabaseClient(tables)
        _install(monkeypatch, float_db)
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("P1", "11111111111") == 2

    def test_exception_retorna_none(self, monkeypatch):
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: True)

        class ExplodingClient:
            def select(self, *a, **kw):
                raise RuntimeError("DB offline")

        monkeypatch.setattr(
            "app.services.domain_lookup.SupabaseRestClient.from_settings",
            staticmethod(lambda: ExplodingClient()),
        )
        from app.services.domain_lookup import get_latest_vote_qty
        assert get_latest_vote_qty("POLL-A", "11987654321") is None


# ---------------------------------------------------------------------------
# load_poll_chat_map
# ---------------------------------------------------------------------------

class TestLoadPollChatMap:

    def test_retorna_mapeamento_correto(self, monkeypatch, fake_db):
        _install(monkeypatch, fake_db)
        from app.services.domain_lookup import load_poll_chat_map
        mapping = load_poll_chat_map()
        assert mapping == {
            "POLL-A": "120363@g.us",
            "POLL-B": "999999@g.us",
        }

    def test_dominio_desabilitado_retorna_dict_vazio(self, monkeypatch):
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: False)
        from app.services.domain_lookup import load_poll_chat_map
        assert load_poll_chat_map() == {}

    def test_linha_sem_poll_id_ignorada(self, monkeypatch):
        """Linha com external_poll_id vazio não deve aparecer no mapa."""
        tables = empty_tables()
        tables["enquetes"] = [
            {"id": "e1", "external_poll_id": "", "titulo": "T", "chat_id": "c1"},
            {"id": "e2", "external_poll_id": "POLL-C", "titulo": "T2", "chat_id": "c2"},
        ]
        partial_db = FakeSupabaseClient(tables)
        _install(monkeypatch, partial_db)
        from app.services.domain_lookup import load_poll_chat_map
        mapping = load_poll_chat_map()
        assert mapping == {"POLL-C": "c2"}

    def test_linha_sem_chat_id_ignorada(self, monkeypatch):
        """Linha com chat_id vazio não deve aparecer no mapa."""
        tables = empty_tables()
        tables["enquetes"] = [
            {"id": "e1", "external_poll_id": "POLL-D", "titulo": "T", "chat_id": ""},
            {"id": "e2", "external_poll_id": "POLL-E", "titulo": "T2", "chat_id": "chat-real"},
        ]
        partial_db = FakeSupabaseClient(tables)
        _install(monkeypatch, partial_db)
        from app.services.domain_lookup import load_poll_chat_map
        mapping = load_poll_chat_map()
        assert mapping == {"POLL-E": "chat-real"}

    def test_exception_retorna_dict_vazio(self, monkeypatch):
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: True)

        class ExplodingClient:
            def select_all(self, *a, **kw):
                raise RuntimeError("DB offline")

        monkeypatch.setattr(
            "app.services.domain_lookup.SupabaseRestClient.from_settings",
            staticmethod(lambda: ExplodingClient()),
        )
        from app.services.domain_lookup import load_poll_chat_map
        assert load_poll_chat_map() == {}

    def test_banco_vazio_retorna_dict_vazio(self, monkeypatch):
        empty_db = FakeSupabaseClient(empty_tables())
        _install(monkeypatch, empty_db)
        from app.services.domain_lookup import load_poll_chat_map
        assert load_poll_chat_map() == {}
