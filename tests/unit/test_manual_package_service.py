"""Testes de app/services/manual_package_service.

Cobre _clean_phone, fetch_enquete_row, _resolve_poll_votes_and_media,
build_preview_payload, build_manual_confirmed_package e
create_manual_package_in_supabase com clientes/enquetes fake.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import app.services.manual_package_service as svc


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_fake_client(enquete=None, alternativas=None, clientes=None, votos=None, pacotes=None):
    """Monta MagicMock do SupabaseRestClient com comportamentos mínimos."""
    fc = MagicMock()

    # rpc retorna próxima sequência
    fc.rpc.return_value = 1

    # select: comportamento por tabela
    def fake_select(table, *, columns="*", filters=None, limit=None,
                    offset=None, order=None, single=False):
        if table == "enquetes":
            rows = [enquete] if enquete else []
            return rows[0] if single and rows else (None if single else rows)
        if table == "enquete_alternativas":
            return alternativas or []
        if table == "clientes":
            return clientes or []
        if table == "votos":
            return votos or []
        if table == "pacotes":
            return pacotes or []
        return [] if not single else None

    fc.select.side_effect = fake_select

    # insert: devolve o payload com id gerado
    _inserted: list = []
    def fake_insert(table, payload, **kwargs):
        if isinstance(payload, dict):
            row = {"id": f"{table}-1", **payload}
            _inserted.append((table, row))
            return [row]
        return [{"id": f"{table}-1"}]

    fc.insert.side_effect = fake_insert
    fc._inserted = _inserted

    # upsert_one: para clientes devolve row com id determinístico
    def fake_upsert_one(table, payload, *, on_conflict=""):
        return {"id": f"CLI-{payload.get('celular', 'x')}", **payload}

    fc.upsert_one.side_effect = fake_upsert_one

    return fc


def _minimal_enquete():
    return {
        "id": "ENQUETE-1",
        "external_poll_id": "poll_abc",
        "titulo": "Vestido Azul R$50",
        "created_at_provider": None,
        "drive_file_id": None,
        "produto": {
            "id": "PROD-1",
            "nome": "Vestido Azul",
            "valor_unitario": 50.0,
            "drive_file_id": None,
        },
    }


def _patch_externals(monkeypatch, enquete=None, customers=None):
    """Patcha dependências externas do módulo."""
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        svc,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    monkeypatch.setattr(svc, "load_customers", lambda: customers or {})
    monkeypatch.setattr(svc, "drive_export_view_url", lambda _: None)
    monkeypatch.setattr(svc, "ensure_thumbnail_for_image_url", lambda _: None)
    monkeypatch.setattr(svc, "extract_price", lambda _: None)
    monkeypatch.setattr(svc, "processors", MagicMock(parse_timestamp=lambda _: None))


# ── _clean_phone ─────────────────────────────────────────────────────────────

def test_clean_phone_remove_non_digits():
    assert svc._clean_phone("+55 (11) 99999-9999") == "5511999999999"


def test_clean_phone_already_clean():
    assert svc._clean_phone("5511999999999") == "5511999999999"


def test_clean_phone_none_returns_empty():
    assert svc._clean_phone(None) == ""


def test_clean_phone_empty_string():
    assert svc._clean_phone("") == ""


def test_clean_phone_int_input():
    """Aceita int e extrai dígitos como string."""
    assert svc._clean_phone(5511999999999) == "5511999999999"


# ── fetch_enquete_row ─────────────────────────────────────────────────────────

def test_fetch_enquete_row_supabase_path(monkeypatch):
    """Quando supabase ativo, usa client.select e devolve row."""
    enquete = _minimal_enquete()
    fc = _make_fake_client(enquete=enquete)
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fc)),
    )
    result = svc.fetch_enquete_row("poll_abc")
    assert result is not None
    assert result["id"] == "ENQUETE-1"


def test_fetch_enquete_row_returns_none_when_not_found(monkeypatch):
    """Enquete não existe → retorna None."""
    fc = _make_fake_client(enquete=None)
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fc)),
    )
    result = svc.fetch_enquete_row("nao_existe")
    assert result is None


def test_fetch_enquete_row_baserow_fallback(monkeypatch):
    """Quando supabase desabilitado, tenta via clients.fetch_rows_filtered."""
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: False)
    fake_clients = MagicMock()
    fake_clients.fetch_rows_filtered.return_value = [{"id": "BR-1", "titulo": "Teste"}]
    monkeypatch.setattr(svc, "clients", fake_clients)
    result = svc.fetch_enquete_row("poll_br")
    assert result is not None
    assert result["id"] == "BR-1"


def test_fetch_enquete_row_baserow_returns_none_when_empty(monkeypatch):
    """Baserow sem resultado → None."""
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: False)
    fake_clients = MagicMock()
    fake_clients.fetch_rows_filtered.return_value = []
    monkeypatch.setattr(svc, "clients", fake_clients)
    result = svc.fetch_enquete_row("inexistente")
    assert result is None


def test_fetch_enquete_row_baserow_exception_continues(monkeypatch):
    """Exceção em fetch_rows_filtered é absorvida; segundo filtro tenta."""
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: False)
    fake_clients = MagicMock()
    fake_clients.fetch_rows_filtered.side_effect = [Exception("timeout"), []]
    monkeypatch.setattr(svc, "clients", fake_clients)
    # Ambos os filtros falham/retornam vazio → None
    result = svc.fetch_enquete_row("poll_err")
    assert result is None


# ── _resolve_poll_votes_and_media ─────────────────────────────────────────────

def test_resolve_raises_when_enquete_not_found(monkeypatch):
    """Enquete inexistente → ValueError."""
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    fc = _make_fake_client(enquete=None)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fc)),
    )
    monkeypatch.setattr(svc, "load_customers", lambda: {})
    monkeypatch.setattr(svc, "drive_export_view_url", lambda _: None)
    monkeypatch.setattr(svc, "ensure_thumbnail_for_image_url", lambda _: None)
    monkeypatch.setattr(svc, "processors", MagicMock(parse_timestamp=lambda _: None))
    with pytest.raises(ValueError, match="Enquete não encontrada"):
        svc._resolve_poll_votes_and_media("poll_xyz", [])


def test_resolve_accumulates_total_qty(monkeypatch):
    """Soma de quantidades está correta."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete, customers={"5511999999999": "Maria"})
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    votes = [
        {"phone": "5511999999999", "qty": 3},
        {"phone": "5511888888888", "qty": 5},
    ]
    _, _, total_qty, _, _ = svc._resolve_poll_votes_and_media("poll_abc", votes)
    assert total_qty == 8


def test_resolve_normalizes_phone_for_name_lookup(monkeypatch):
    """Customers carregados com phone+dígitos; busca por phone normalizado."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete, customers={"5511999999999": "Maria Oliveira"})
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    votes = [{"phone": "+55 (11) 99999-9999", "qty": 2}]
    _, votes_out, _, _, _ = svc._resolve_poll_votes_and_media("poll_abc", votes)
    assert votes_out[0]["name"] == "Maria Oliveira"
    assert votes_out[0]["phone"] == "5511999999999"


def test_resolve_unknown_customer_gets_empty_name(monkeypatch):
    """Cliente sem cadastro recebe name vazio."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete, customers={})
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    votes = [{"phone": "5519999999999", "qty": 1}]
    _, votes_out, _, _, _ = svc._resolve_poll_votes_and_media("poll_abc", votes)
    assert votes_out[0]["name"] == ""


def test_resolve_image_url_from_enquete_drive_id(monkeypatch):
    """drive_file_id da enquete gera image_url."""
    enquete = {**_minimal_enquete(), "drive_file_id": "DRIVE-XYZ"}
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    fc = _make_fake_client(enquete=enquete)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fc)),
    )
    monkeypatch.setattr(svc, "load_customers", lambda: {})
    monkeypatch.setattr(svc, "drive_export_view_url", lambda d: f"https://img/{d}")
    monkeypatch.setattr(svc, "ensure_thumbnail_for_image_url", lambda u: f"{u}?thumb")
    monkeypatch.setattr(svc, "extract_price", lambda _: None)
    monkeypatch.setattr(svc, "processors", MagicMock(parse_timestamp=lambda _: None))

    meta, _, _, image_url, image_thumb = svc._resolve_poll_votes_and_media("poll_abc", [])
    assert image_url == "https://img/DRIVE-XYZ"
    assert image_thumb == "https://img/DRIVE-XYZ?thumb"


def test_resolve_vote_as_object_with_getattr(monkeypatch):
    """vote_lines pode ter objetos com atributos em vez de dicts."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )

    class VoteObj:
        def __init__(self, phone, qty):
            self.phone = phone
            self.qty = qty

    votes = [VoteObj("5511999999999", 4)]
    _, votes_out, total_qty, _, _ = svc._resolve_poll_votes_and_media("poll_abc", votes)
    assert total_qty == 4
    assert votes_out[0]["qty"] == 4


# ── build_preview_payload ─────────────────────────────────────────────────────

def test_build_preview_payload_structure(monkeypatch):
    """build_preview_payload retorna chaves esperadas."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete, customers={"5511999999999": "Maria"})
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    votes = [{"phone": "5511999999999", "qty": 3}]
    result = svc.build_preview_payload("poll_abc", votes)
    assert result["poll_title"] == "Vestido Azul R$50"
    assert result["total_qty"] == 3
    assert len(result["votes"]) == 1
    assert result["votes"][0]["phone"] == "5511999999999"
    assert result["votes"][0]["qty"] == 3


def test_build_preview_payload_empty_votes(monkeypatch):
    """Sem votos: total_qty=0 e votes=[]."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    result = svc.build_preview_payload("poll_abc", [])
    assert result["total_qty"] == 0
    assert result["votes"] == []


def test_build_preview_payload_propagates_valor_col(monkeypatch):
    """valor_col vem do produto da enquete."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    result = svc.build_preview_payload("poll_abc", [])
    assert result["valor_col"] == 50.0


# ── build_manual_confirmed_package ───────────────────────────────────────────

def test_build_manual_confirmed_package_keys(monkeypatch):
    """Retorna todas as chaves obrigatórias de um pacote confirmado."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete, customers={"5511999999999": "Maria"})
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    votes = [{"phone": "5511999999999", "qty": 3}]
    result = svc.build_manual_confirmed_package("poll_abc", votes)

    for key in ("id", "poll_title", "valor_col", "image", "image_thumb", "qty",
                "status", "votes", "confirmed_at", "manual_creation",
                "opened_at", "closed_at", "pdf_attempts", "pdf_status", "pdf_file_name"):
        assert key in result, f"Chave ausente: {key}"


def test_build_manual_confirmed_package_status_and_pdf(monkeypatch):
    """status=confirmed, pdf_status=queued, pdf_attempts=0, manual_creation=True."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    result = svc.build_manual_confirmed_package("poll_abc", [])
    assert result["status"] == "confirmed"
    assert result["pdf_status"] == "queued"
    assert result["pdf_attempts"] == 0
    assert result["manual_creation"] is True
    assert result["pdf_file_name"] is None


def test_build_manual_confirmed_package_id_format(monkeypatch):
    """ID segue padrão <poll_id>_m_<hex12>."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    result = svc.build_manual_confirmed_package("poll_abc", [])
    pkg_id = result["id"]
    assert pkg_id.startswith("poll_abc_m_")
    suffix = pkg_id[len("poll_abc_m_"):]
    assert len(suffix) == 12
    assert suffix.isalnum()


def test_build_manual_confirmed_package_total_qty(monkeypatch):
    """qty do pacote é soma das quantidades dos votos."""
    enquete = _minimal_enquete()
    _patch_externals(monkeypatch, enquete=enquete)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=_make_fake_client(enquete=enquete))),
    )
    votes = [
        {"phone": "5511111111111", "qty": 10},
        {"phone": "5522222222222", "qty": 14},
    ]
    result = svc.build_manual_confirmed_package("poll_abc", votes)
    assert result["qty"] == 24


# ── create_manual_package_in_supabase ────────────────────────────────────────

def _full_patch(monkeypatch, enquete=None, alternativas=None, clientes=None,
                votos=None, pacotes=None, customers=None, rpc_raises=False):
    """Patcha tudo que create_manual_package_in_supabase precisa."""
    if enquete is None:
        enquete = _minimal_enquete()
    fc = _make_fake_client(
        enquete=enquete, alternativas=alternativas or [],
        clientes=clientes or [], votos=votos or [],
        pacotes=pacotes or [],
    )
    if rpc_raises:
        fc.rpc.side_effect = Exception("rpc falhou")

    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fc)),
    )
    monkeypatch.setattr(svc, "load_customers", lambda: customers or {})
    monkeypatch.setattr(svc, "drive_export_view_url", lambda _: None)
    monkeypatch.setattr(svc, "ensure_thumbnail_for_image_url", lambda _: None)
    monkeypatch.setattr(svc, "extract_price", lambda _: None)
    monkeypatch.setattr(svc, "processors", MagicMock(parse_timestamp=lambda _: None))
    return fc


def test_create_manual_package_raises_when_supabase_disabled(monkeypatch):
    """Sem domínio Supabase → RuntimeError."""
    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: False)
    with pytest.raises(RuntimeError, match="disabled"):
        svc.create_manual_package_in_supabase("poll_abc", [{"phone": "5511999999999", "qty": 3}])


def test_create_manual_package_raises_without_produto(monkeypatch):
    """Enquete sem produto_id → ValueError."""
    enquete = {
        "id": "ENQUETE-1",
        "external_poll_id": "poll_abc",
        "titulo": "Vestido",
        "created_at_provider": None,
        "drive_file_id": None,
        "produto": None,  # sem produto
    }
    _full_patch(monkeypatch, enquete=enquete)
    with pytest.raises(ValueError, match="produto associado"):
        svc.create_manual_package_in_supabase("poll_abc", [{"phone": "5511999999999", "qty": 3}])


def test_create_manual_package_raises_without_enquete_id(monkeypatch):
    """Enquete sem id → ValueError."""
    enquete = {
        "id": None,  # sem id
        "external_poll_id": "poll_abc",
        "titulo": "Vestido",
        "created_at_provider": None,
        "drive_file_id": None,
        "produto": {"id": "PROD-1", "nome": "X", "valor_unitario": 50.0, "drive_file_id": None},
    }
    _full_patch(monkeypatch, enquete=enquete)
    with pytest.raises(ValueError, match="produto associado"):
        svc.create_manual_package_in_supabase("poll_abc", [{"phone": "5511999999999", "qty": 3}])


def test_create_manual_package_inserts_pacote(monkeypatch):
    """Pacote é inserido com status=closed e enquete_id correto."""
    fc = _full_patch(monkeypatch)
    votes = [{"phone": "5511999999999", "qty": 3}]
    result = svc.create_manual_package_in_supabase("poll_abc", votes)
    assert "package_id" in result

    pacote_rows = [p for t, p in fc._inserted if t == "pacotes"]
    assert len(pacote_rows) == 1
    assert pacote_rows[0]["status"] == "closed"
    assert pacote_rows[0]["enquete_id"] == "ENQUETE-1"


def test_create_manual_package_creates_pacote_clientes(monkeypatch):
    """Uma entrada em pacote_clientes por voto."""
    fc = _full_patch(monkeypatch)
    votes = [
        {"phone": "5511111111111", "qty": 5},
        {"phone": "5522222222222", "qty": 7},
    ]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    pc_rows = [p for t, p in fc._inserted if t == "pacote_clientes"]
    assert len(pc_rows) == 2


def test_create_manual_package_calculates_financials(monkeypatch):
    """subtotal, commission_amount e total_amount calculados corretamente."""
    fc = _full_patch(monkeypatch)
    votes = [{"phone": "5511999999999", "qty": 2}]  # 2 × R$50 = R$100
    svc.create_manual_package_in_supabase("poll_abc", votes)
    pc_rows = [p for t, p in fc._inserted if t == "pacote_clientes"]
    assert len(pc_rows) == 1
    pc = pc_rows[0]
    assert pc["subtotal"] == 100.0
    assert pc["commission_amount"] == 10.0   # R$5 × 2 peças
    assert pc["total_amount"] == 110.0


def test_create_manual_package_upserts_cliente(monkeypatch):
    """upsert_one em clientes é chamado para cada voto com phone."""
    fc = _full_patch(monkeypatch)
    votes = [
        {"phone": "5511111111111", "qty": 3},
        {"phone": "5522222222222", "qty": 5},
    ]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    calls = [c for c in fc.upsert_one.call_args_list if c.args[0] == "clientes"]
    assert len(calls) == 2


def test_create_manual_package_skips_vote_without_phone(monkeypatch):
    """Voto sem phone é ignorado: sem upsert_one e sem pacote_clientes."""
    fc = _full_patch(monkeypatch)
    votes = [
        {"phone": "", "qty": 3},   # sem phone → ignorado
        {"phone": "5511111111111", "qty": 5},
    ]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    calls = [c for c in fc.upsert_one.call_args_list if c.args[0] == "clientes"]
    assert len(calls) == 1


def test_create_manual_package_creates_synthetic_vote_when_no_existing(monkeypatch):
    """Sem voto existente para o cliente, chama upsert_one em votos com status=in."""
    fc = _full_patch(monkeypatch, votos=[])  # nenhum voto existente
    votes = [{"phone": "5511999999999", "qty": 4}]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    # votos sintéticos são criados via upsert_one, não insert
    voto_calls = [c for c in fc.upsert_one.call_args_list if c.args[0] == "votos"]
    assert len(voto_calls) == 1
    payload = voto_calls[0].args[1]
    assert payload["status"] == "in"
    assert payload["qty"] == 4


def test_create_manual_package_reuses_existing_vote(monkeypatch):
    """Voto já existente para o cliente → não chama upsert_one em votos."""
    existing_voto = {"id": "VOTO-EXISTING", "enquete_id": "ENQUETE-1", "cliente_id": "CLI-5511999999999"}

    def fake_select_with_voto(table, *, columns="*", filters=None, limit=None,
                               offset=None, order=None, single=False):
        if table == "enquetes":
            return _minimal_enquete() if single else [_minimal_enquete()]
        if table == "enquete_alternativas":
            return []
        if table == "clientes":
            return []
        if table == "votos":
            return [existing_voto]
        if table == "pacotes":
            return []
        return []

    fc = _make_fake_client()
    fc.select.side_effect = fake_select_with_voto

    monkeypatch.setattr(svc, "supabase_domain_enabled", lambda: True)
    monkeypatch.setattr(
        svc, "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fc)),
    )
    monkeypatch.setattr(svc, "load_customers", lambda: {})
    monkeypatch.setattr(svc, "drive_export_view_url", lambda _: None)
    monkeypatch.setattr(svc, "ensure_thumbnail_for_image_url", lambda _: None)
    monkeypatch.setattr(svc, "extract_price", lambda _: None)
    monkeypatch.setattr(svc, "processors", MagicMock(parse_timestamp=lambda _: None))

    votes = [{"phone": "5511999999999", "qty": 4}]
    svc.create_manual_package_in_supabase("poll_abc", votes)

    # Não deve ter chamado upsert_one em votos (voto já existia)
    voto_upserts = [c for c in fc.upsert_one.call_args_list if c.args[0] == "votos"]
    assert voto_upserts == []


def test_create_manual_package_rpc_fallback_to_select(monkeypatch):
    """Quando rpc lança exceção, cai no fallback de select para sequence."""
    fc = _full_patch(monkeypatch, pacotes=[{"sequence_no": 3}], rpc_raises=True)
    votes = [{"phone": "5511999999999", "qty": 2}]
    result = svc.create_manual_package_in_supabase("poll_abc", votes)
    assert "package_id" in result
    # next_sequence deve ter sido calculado como 4 (3+1)
    pacote_rows = [p for t, p in fc._inserted if t == "pacotes"]
    assert pacote_rows[0]["sequence_no"] == 4


def test_create_manual_package_rpc_fallback_empty_pacotes(monkeypatch):
    """Fallback com pacotes vazios → sequence começa em 1."""
    fc = _full_patch(monkeypatch, pacotes=[], rpc_raises=True)
    votes = [{"phone": "5511999999999", "qty": 2}]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    pacote_rows = [p for t, p in fc._inserted if t == "pacotes"]
    assert pacote_rows[0]["sequence_no"] == 1


def test_create_manual_package_uses_alternativa_id_when_available(monkeypatch):
    """alternativa_id do voto sintético vem de enquete_alternativas."""
    alts = [{"id": "ALT-5", "qty": 5}]
    fc = _full_patch(monkeypatch, alternativas=alts)
    votes = [{"phone": "5511999999999", "qty": 5}]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    voto_calls = [c for c in fc.upsert_one.call_args_list if c.args[0] == "votos"]
    assert len(voto_calls) == 1
    payload = voto_calls[0].args[1]
    assert payload["alternativa_id"] == "ALT-5"


def test_create_manual_package_alternativa_id_none_for_unmatched_qty(monkeypatch):
    """Se não há alternativa com qty igual, alternativa_id fica None."""
    alts = [{"id": "ALT-3", "qty": 3}]
    fc = _full_patch(monkeypatch, alternativas=alts)
    votes = [{"phone": "5511999999999", "qty": 7}]  # qty 7 não bate com alt 3
    svc.create_manual_package_in_supabase("poll_abc", votes)
    voto_calls = [c for c in fc.upsert_one.call_args_list if c.args[0] == "votos"]
    assert len(voto_calls) == 1
    payload = voto_calls[0].args[1]
    assert payload["alternativa_id"] is None


def test_create_manual_package_returns_package_id_and_legacy(monkeypatch):
    """Retorno inclui package_id e legacy_package_id."""
    fc = _full_patch(monkeypatch)
    votes = [{"phone": "5511999999999", "qty": 3}]
    result = svc.create_manual_package_in_supabase("poll_abc", votes)
    assert "package_id" in result
    assert "legacy_package_id" in result
    assert result["legacy_package_id"].startswith("poll_abc_")


def test_create_manual_package_pacote_qty_sum(monkeypatch):
    """total_qty do pacote é soma de todos os votos."""
    fc = _full_patch(monkeypatch)
    votes = [
        {"phone": "5511111111111", "qty": 10},
        {"phone": "5522222222222", "qty": 14},
    ]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    pacote_rows = [p for t, p in fc._inserted if t == "pacotes"]
    assert pacote_rows[0]["total_qty"] == 24
    assert pacote_rows[0]["participants_count"] == 2


def test_create_manual_package_commission_zero_price(monkeypatch):
    """Com unit_price=0, subtotal=0 mas comissão é R$5/peça (flat fee independe do preço)."""
    enquete = {**_minimal_enquete()}
    enquete["produto"] = {
        "id": "PROD-1",
        "nome": "Item Zero",
        "valor_unitario": 0.0,
        "drive_file_id": None,
    }
    fc = _full_patch(monkeypatch, enquete=enquete)
    votes = [{"phone": "5511999999999", "qty": 5}]
    svc.create_manual_package_in_supabase("poll_abc", votes)
    pc_rows = [p for t, p in fc._inserted if t == "pacote_clientes"]
    assert pc_rows[0]["subtotal"] == 0.0
    assert pc_rows[0]["commission_amount"] == 25.0   # R$5 × 5 peças
    assert pc_rows[0]["total_amount"] == 25.0
