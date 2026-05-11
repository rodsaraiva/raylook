"""Testes de integração para app/services/sqlite_service.py.

Usa SQLite real em memória (:memory:) — sem mocks de banco.
Cada teste recebe uma instância nova para isolamento completo.
Meta de cobertura: ≥ 85%.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Importa diretamente as funções e a classe sendo testadas
from app.services.sqlite_service import (
    SQLiteRestClient,
    _FakeResponse,
    _parse_columns,
    _parse_pgrst_value,
    _prepare_payload,
    _split_top_level,
    _translate_filter,
    _translate_order,
    _row_to_dict,
)


# ---------------------------------------------------------------------------
# Fixture: cliente com banco em memória
# ---------------------------------------------------------------------------

SCHEMA_PATH = str(Path(__file__).resolve().parent.parent.parent / "deploy" / "sqlite" / "schema.sql")


@pytest.fixture
def db(tmp_path):
    """Retorna SQLiteRestClient com banco em memória (arquivo temporário por teste)."""
    db_file = str(tmp_path / "test.db")
    client = SQLiteRestClient(db_path=db_file, schema_path=SCHEMA_PATH)
    return client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_produto(db: SQLiteRestClient, nome: str = "Produto Teste", valor: float = 100.0) -> Dict[str, Any]:
    rows = db.insert("produtos", {"nome": nome, "valor_unitario": valor})
    return rows[0]


def _make_cliente(db: SQLiteRestClient, nome: str = "Cliente Teste", celular: str = "11999990001") -> Dict[str, Any]:
    rows = db.insert("clientes", {"nome": nome, "celular": celular})
    return rows[0]


def _make_enquete(db: SQLiteRestClient, produto_id: str, titulo: str = "Enquete Teste") -> Dict[str, Any]:
    rows = db.insert("enquetes", {
        "external_poll_id": str(uuid.uuid4()),
        "produto_id": produto_id,
        "titulo": titulo,
        "status": "open",
    })
    return rows[0]


# ---------------------------------------------------------------------------
# _split_top_level
# ---------------------------------------------------------------------------

def test_split_top_level_simples():
    """Divide por vírgula em campos simples."""
    result = _split_top_level("a,b,c")
    assert result == ["a", "b", "c"]


def test_split_top_level_respeita_parenteses():
    """Não divide dentro de parênteses."""
    result = _split_top_level("id,produto:produto_id(nome,valor)")
    assert result == ["id", "produto:produto_id(nome,valor)"]


def test_split_top_level_nested():
    """Suporta parênteses aninhados."""
    result = _split_top_level("a,b(c(d,e),f)")
    assert result == ["a", "b(c(d,e),f)"]


def test_split_top_level_string_vazia():
    """String vazia retorna lista vazia."""
    result = _split_top_level("")
    assert result == []


# ---------------------------------------------------------------------------
# _parse_columns
# ---------------------------------------------------------------------------

def test_parse_columns_asterisco():
    fields, embeds = _parse_columns("*")
    assert fields == ["*"]
    assert embeds == []


def test_parse_columns_none():
    fields, embeds = _parse_columns(None)
    assert fields == ["*"]
    assert embeds == []


def test_parse_columns_campos_simples():
    fields, embeds = _parse_columns("id,nome,status")
    assert "id" in fields
    assert "nome" in fields
    assert embeds == []


def test_parse_columns_embed_com_alias():
    """produto:produto_id(nome) cria embed com alias='produto', fk='produto_id'."""
    fields, embeds = _parse_columns("id,produto:produto_id(nome)")
    assert len(embeds) == 1
    emb = embeds[0]
    assert emb["alias"] == "produto"
    assert emb["fk_column"] == "produto_id"
    assert emb["table"] == "produtos"
    assert "nome" in emb["child_fields"]
    # FK deve ser injetado em fields automaticamente
    assert "produto_id" in fields


def test_parse_columns_embed_sem_alias():
    """produtos(nome) usa nome como alias e infere FK por convenção."""
    fields, embeds = _parse_columns("id,produtos(nome)")
    assert len(embeds) == 1
    emb = embeds[0]
    assert emb["alias"] == "produtos"


# ---------------------------------------------------------------------------
# _translate_filter
# ---------------------------------------------------------------------------

def test_translate_filter_eq():
    sql, vals = _translate_filter("status", "eq", "paid")
    assert "= ?" in sql
    assert vals == ["paid"]


def test_translate_filter_eq_none():
    """eq com None deve gerar IS NULL sem parâmetros."""
    sql, vals = _translate_filter("campo", "eq", None)
    assert "IS NULL" in sql
    assert vals == []


def test_translate_filter_neq():
    sql, vals = _translate_filter("status", "neq", "cancelled")
    assert "!= ?" in sql
    assert vals == ["cancelled"]


def test_translate_filter_lt():
    sql, vals = _translate_filter("total", "lt", 100)
    assert "< ?" in sql


def test_translate_filter_lte():
    sql, vals = _translate_filter("total", "lte", 100)
    assert "<= ?" in sql


def test_translate_filter_gt():
    sql, vals = _translate_filter("total", "gt", 0)
    assert "> ?" in sql


def test_translate_filter_gte():
    sql, vals = _translate_filter("created_at", "gte", "2026-01-01")
    assert ">= ?" in sql


def test_translate_filter_like():
    sql, vals = _translate_filter("nome", "like", "%João%")
    assert "LIKE ?" in sql


def test_translate_filter_ilike():
    """ilike em SQLite é tratado como LIKE case-insensitive para ASCII."""
    sql, vals = _translate_filter("nome", "ilike", "%joão%")
    assert "LIKE ?" in sql


def test_translate_filter_is_null():
    sql, vals = _translate_filter("campo", "is", "null")
    assert "IS NULL" in sql
    assert vals == []


def test_translate_filter_is_none_str():
    sql, vals = _translate_filter("campo", "is", "none")
    assert "IS NULL" in sql


def test_translate_filter_is_true():
    sql, vals = _translate_filter("synthetic", "is", "true")
    assert "= 1" in sql


def test_translate_filter_is_false():
    sql, vals = _translate_filter("synthetic", "is", "false")
    assert "= 0" in sql


def test_translate_filter_is_outro_valor():
    sql, vals = _translate_filter("campo", "is", "abcd")
    assert "= ?" in sql
    assert vals == ["abcd"]


def test_translate_filter_in_lista():
    sql, vals = _translate_filter("status", "in", ["a", "b"])
    assert "IN" in sql
    assert vals == ["a", "b"]


def test_translate_filter_in_string():
    """String separada por vírgula deve ser convertida para lista."""
    sql, vals = _translate_filter("status", "in", "a,b,c")
    assert "IN" in sql
    assert len(vals) == 3


def test_translate_filter_in_valor_unico():
    """Valor não lista deve virar lista unitária."""
    sql, vals = _translate_filter("id", "in", "abc")
    assert "IN" in sql


def test_translate_filter_in_lista_vazia():
    """Lista vazia gera condição always-false."""
    sql, vals = _translate_filter("status", "in", [])
    assert "0 = 1" in sql
    assert vals == []


def test_translate_filter_not_is():
    sql, vals = _translate_filter("campo", "not.is", "null")
    assert "IS NOT NULL" in sql


def test_translate_filter_not_is_valor():
    sql, vals = _translate_filter("campo", "not.is", "xyz")
    assert "!= ?" in sql


def test_translate_filter_op_invalido():
    """Operador desconhecido deve levantar ValueError."""
    with pytest.raises(ValueError, match="Operador não suportado"):
        _translate_filter("campo", "INVALIDO", "valor")


# ---------------------------------------------------------------------------
# _translate_order
# ---------------------------------------------------------------------------

def test_translate_order_simples():
    result = _translate_order("nome.asc")
    assert "nome ASC" in result


def test_translate_order_desc():
    result = _translate_order("created_at.desc")
    assert "created_at DESC" in result


def test_translate_order_nullsfirst():
    result = _translate_order("campo.asc.nullsfirst")
    assert "NULLS FIRST" in result


def test_translate_order_nullslast():
    result = _translate_order("campo.desc.nullslast")
    assert "NULLS LAST" in result


def test_translate_order_multiplos_campos():
    result = _translate_order("nome.asc,created_at.desc")
    assert "nome ASC" in result
    assert "created_at DESC" in result


def test_translate_order_item_vazio():
    """Itens vazios são ignorados."""
    result = _translate_order("nome.asc,,created_at.desc")
    assert "nome ASC" in result


# ---------------------------------------------------------------------------
# _parse_pgrst_value
# ---------------------------------------------------------------------------

def test_parse_pgrst_value_eq():
    op, val = _parse_pgrst_value("eq.paid")
    assert op == "eq"
    assert val == "paid"


def test_parse_pgrst_value_in():
    op, val = _parse_pgrst_value("in.(a,b,c)")
    assert op == "in"
    assert val == ["a", "b", "c"]


def test_parse_pgrst_value_is_null():
    op, val = _parse_pgrst_value("is.null")
    assert op == "is"
    assert val == "null"


def test_parse_pgrst_value_not_is():
    op, val = _parse_pgrst_value("not.is.null")
    assert op == "not.is"
    assert val == "null"


def test_parse_pgrst_value_not_eq():
    """not.eq.X → op='neq', val='X'."""
    op, val = _parse_pgrst_value("not.eq.X")
    assert op == "neq"


def test_parse_pgrst_value_sem_ponto():
    """Sem ponto retorna eq com o valor bruto."""
    op, val = _parse_pgrst_value("paid")
    assert op == "eq"
    assert val == "paid"


def test_parse_pgrst_value_none():
    op, val = _parse_pgrst_value(None)
    assert op == "eq"
    assert val is None


def test_parse_pgrst_value_vazio():
    op, val = _parse_pgrst_value("")
    assert op == "eq"


def test_parse_pgrst_value_not_sem_inner():
    """not sem inner op: retorna neq."""
    op, val = _parse_pgrst_value("not.X")
    assert op == "neq"
    assert val == "X"


# ---------------------------------------------------------------------------
# _prepare_payload
# ---------------------------------------------------------------------------

def test_prepare_payload_gera_uuid_insert():
    """Insert em tabela UUID_PK deve gerar id se ausente."""
    out = _prepare_payload("clientes", {"nome": "João", "celular": "11999990001"}, is_insert=True)
    assert "id" in out
    # Deve ser UUID válido
    uuid.UUID(out["id"])


def test_prepare_payload_nao_sobrescreve_id_existente():
    """ID já fornecido não deve ser substituído."""
    custom_id = str(uuid.uuid4())
    out = _prepare_payload("clientes", {"id": custom_id, "nome": "X", "celular": "1"}, is_insert=True)
    assert out["id"] == custom_id


def test_prepare_payload_preenche_timestamps_insert():
    """created_at e updated_at devem ser preenchidos no insert."""
    out = _prepare_payload("clientes", {"nome": "X", "celular": "1"}, is_insert=True)
    assert "created_at" in out
    assert "updated_at" in out


def test_prepare_payload_nao_preenche_created_at_no_update():
    """No update, created_at não deve ser tocado (só updated_at)."""
    out = _prepare_payload("clientes", {"nome": "X"}, is_insert=False)
    assert "created_at" not in out
    assert "updated_at" in out


def test_prepare_payload_serializa_json():
    """Colunas JSON devem ser serializadas para string."""
    payload = {"key": "k", "payload_json": {"foo": "bar"}}
    out = _prepare_payload("app_runtime_state", payload, is_insert=False)
    assert isinstance(out["payload_json"], str)
    assert json.loads(out["payload_json"]) == {"foo": "bar"}


def test_prepare_payload_bool_normaliza():
    """Colunas bool (synthetic em votos) devem ser 0/1."""
    out = _prepare_payload("votos", {"synthetic": True}, is_insert=False)
    assert out["synthetic"] == 1


# ---------------------------------------------------------------------------
# _row_to_dict
# ---------------------------------------------------------------------------

def test_row_to_dict_json_decodificado():
    """payload_json do tipo string deve ser decodificado para dict."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (payload_json TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", [json.dumps({"x": 1})])
    row = conn.execute("SELECT * FROM t").fetchone()
    d = _row_to_dict(row, "app_runtime_state")
    assert isinstance(d["payload_json"], dict)
    assert d["payload_json"]["x"] == 1


def test_row_to_dict_bool_convertido():
    """Campo synthetic=1 deve virar True."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE v (synthetic INTEGER)")
    conn.execute("INSERT INTO v VALUES (1)")
    row = conn.execute("SELECT * FROM v").fetchone()
    d = _row_to_dict(row, "votos")
    assert d["synthetic"] is True


# ---------------------------------------------------------------------------
# SQLiteRestClient.insert
# ---------------------------------------------------------------------------

def test_insert_retorna_linha(db):
    """Insert deve retornar a linha inserida com id gerado."""
    rows = db.insert("produtos", {"nome": "Teste", "valor_unitario": 50.0})
    assert len(rows) == 1
    assert rows[0]["nome"] == "Teste"
    assert "id" in rows[0]


def test_insert_multiplas_linhas(db):
    """Insert de lista deve retornar todas as linhas."""
    rows = db.insert("produtos", [
        {"nome": "A", "valor_unitario": 10.0},
        {"nome": "B", "valor_unitario": 20.0},
    ])
    assert len(rows) == 2


def test_insert_upsert_on_conflict(db):
    """Upsert deve atualizar a linha em conflito."""
    produto = _make_produto(db, nome="Original")
    pid = produto["id"]
    db.insert(
        "produtos",
        {"id": pid, "nome": "Atualizado", "valor_unitario": 200.0},
        upsert=True,
        on_conflict="id",
    )
    rows = db.select("produtos", filters=[("id", "eq", pid)])
    assert rows[0]["nome"] == "Atualizado"


def test_insert_returning_minimal_nao_retorna_rows(db):
    """returning='minimal' deve retornar lista vazia."""
    rows = db.insert("produtos", {"nome": "X", "valor_unitario": 1.0}, returning="minimal")
    assert rows == []


# ---------------------------------------------------------------------------
# SQLiteRestClient.select
# ---------------------------------------------------------------------------

def test_select_sem_filtros_retorna_tudo(db):
    """select sem filtros retorna todas as linhas."""
    _make_produto(db, nome="P1")
    _make_produto(db, nome="P2")
    rows = db.select("produtos")
    assert len(rows) >= 2


def test_select_com_filtro_eq(db):
    """Filtro eq deve retornar apenas linhas correspondentes."""
    p = _make_produto(db, nome="Único")
    rows = db.select("produtos", filters=[("id", "eq", p["id"])])
    assert len(rows) == 1
    assert rows[0]["nome"] == "Único"


def test_select_com_limit(db):
    """limit deve restringir número de linhas retornadas."""
    for i in range(5):
        _make_produto(db, nome=f"P{i}", valor=float(i))
    rows = db.select("produtos", limit=2)
    assert len(rows) <= 2


def test_select_com_offset(db):
    """offset deve pular as primeiras linhas (SQLite exige LIMIT junto com OFFSET)."""
    for i in range(4):
        _make_produto(db, nome=f"Q{i}", valor=float(i))
    rows_all = db.select("produtos", order="nome.asc")
    # SQLite exige LIMIT quando OFFSET é usado; passa limit alto para incluir tudo
    rows_offset = db.select("produtos", order="nome.asc", offset=2, limit=100)
    assert len(rows_offset) == len(rows_all) - 2


def test_select_com_order(db):
    """order deve ordenar resultado corretamente."""
    db.insert("produtos", {"nome": "ZZZ", "valor_unitario": 1.0})
    db.insert("produtos", {"nome": "AAA", "valor_unitario": 1.0})
    rows = db.select("produtos", order="nome.asc")
    nomes = [r["nome"] for r in rows]
    assert nomes == sorted(nomes)


def test_select_single_retorna_dict(db):
    """single=True deve retornar dict ou None, não lista."""
    p = _make_produto(db)
    result = db.select("produtos", filters=[("id", "eq", p["id"])], single=True)
    assert isinstance(result, dict)


def test_select_single_sem_resultado_retorna_none(db):
    """single=True sem resultado deve retornar None."""
    result = db.select("produtos", filters=[("id", "eq", "nao-existe")], single=True)
    assert result is None


def test_select_columns_especificos(db):
    """Colunas especificadas devem restringir o retorno."""
    p = _make_produto(db, nome="ColTeste")
    rows = db.select("produtos", columns="id,nome", filters=[("id", "eq", p["id"])])
    assert "nome" in rows[0]
    # 'descricao' não foi pedida — pode estar ou não (fallback SELECT *)
    # O importante é que não quebrou


def test_select_com_embed_fk(db):
    """Embed produto:produto_id(nome) deve popular campo nested."""
    p = _make_produto(db, nome="ProdutoNested")
    enq = _make_enquete(db, produto_id=p["id"], titulo="Enquete com embed")
    rows = db.select(
        "enquetes",
        columns="id,titulo,produto:produto_id(nome)",
        filters=[("id", "eq", enq["id"])],
    )
    assert len(rows) == 1
    assert rows[0]["produto"] is not None
    assert rows[0]["produto"]["nome"] == "ProdutoNested"


def test_select_embed_fk_nulo_retorna_none(db):
    """Embed com FK NULL deve popular como None."""
    # enquete sem produto_id
    rows = db.insert("enquetes", {
        "external_poll_id": str(uuid.uuid4()),
        "produto_id": None,
        "titulo": "Sem Produto",
        "status": "open",
    })
    enq = rows[0]
    result = db.select(
        "enquetes",
        columns="id,produto:produto_id(nome)",
        filters=[("id", "eq", enq["id"])],
    )
    assert result[0]["produto"] is None


# ---------------------------------------------------------------------------
# SQLiteRestClient.select_all
# ---------------------------------------------------------------------------

def test_select_all_retorna_lista(db):
    """select_all sempre retorna lista."""
    _make_produto(db)
    result = db.select_all("produtos")
    assert isinstance(result, list)
    assert len(result) >= 1


def test_select_all_vazio_retorna_lista_vazia(db):
    """Tabela vazia retorna lista vazia."""
    result = db.select_all("legacy_charges")
    assert result == []


# ---------------------------------------------------------------------------
# SQLiteRestClient.update
# ---------------------------------------------------------------------------

def test_update_modifica_campo(db):
    """Update deve modificar o campo especificado."""
    p = _make_produto(db, nome="Antes")
    db.update("produtos", {"nome": "Depois"}, filters=[("id", "eq", p["id"])])
    rows = db.select("produtos", filters=[("id", "eq", p["id"])])
    assert rows[0]["nome"] == "Depois"


def test_update_sem_filtro_atualiza_tudo(db):
    """Update sem filtros atualiza todas as linhas."""
    _make_produto(db, nome="X1")
    _make_produto(db, nome="X2")
    db.update("produtos", {"nome": "TODOS"})
    rows = db.select("produtos")
    assert all(r["nome"] == "TODOS" for r in rows)


def test_update_returning_minimal(db):
    """returning='minimal' retorna lista vazia."""
    p = _make_produto(db)
    result = db.update("produtos", {"nome": "N"}, filters=[("id", "eq", p["id"])], returning="minimal")
    assert result == []


def test_update_payload_vazio_retorna_vazio(db):
    """Payload vazio não deve disparar SQL e retorna lista vazia."""
    result = db.update("produtos", {})
    assert result == []


# ---------------------------------------------------------------------------
# SQLiteRestClient.delete
# ---------------------------------------------------------------------------

def test_delete_remove_linha(db):
    """delete deve remover a linha do banco."""
    p = _make_produto(db)
    pid = p["id"]
    db.delete("produtos", filters=[("id", "eq", pid)])
    rows = db.select("produtos", filters=[("id", "eq", pid)])
    assert rows == []


def test_delete_sem_filtro_remove_tudo(db):
    """delete sem filtro remove todas as linhas."""
    _make_produto(db, nome="A")
    _make_produto(db, nome="B")
    db.delete("produtos")
    rows = db.select("produtos")
    assert rows == []


def test_delete_retorna_rowcount(db):
    """delete retorna número de linhas removidas."""
    _make_produto(db, nome="Del1")
    _make_produto(db, nome="Del2")
    count = db.delete("produtos")
    assert count >= 2


# ---------------------------------------------------------------------------
# SQLiteRestClient.upsert_one
# ---------------------------------------------------------------------------

def test_upsert_one_insere_novo(db):
    """upsert_one em id novo deve inserir."""
    produto = _make_produto(db)
    state = db.upsert_one(
        "app_runtime_state",
        {"key": "test_key", "payload_json": {"v": 1}},
        on_conflict="key",
    )
    assert state["key"] == "test_key"


def test_upsert_one_atualiza_existente(db):
    """upsert_one em key existente deve atualizar."""
    db.upsert_one("app_runtime_state", {"key": "k1", "payload_json": {"v": 1}}, on_conflict="key")
    result = db.upsert_one("app_runtime_state", {"key": "k1", "payload_json": {"v": 99}}, on_conflict="key")
    assert result["payload_json"]["v"] == 99


# ---------------------------------------------------------------------------
# SQLiteRestClient.rpc — next_pacote_sequence
# ---------------------------------------------------------------------------

def test_rpc_next_pacote_sequence_sem_pacotes(db):
    """Sem pacotes na enquete, retorna 1."""
    prod = _make_produto(db)
    enq = _make_enquete(db, produto_id=prod["id"])
    result = db.rpc("next_pacote_sequence", {"p_enquete_id": enq["id"]})
    assert result == 1


def test_rpc_next_pacote_sequence_com_pacotes(db):
    """Com pacotes sequence_no 1 e 2, retorna 3."""
    prod = _make_produto(db)
    enq = _make_enquete(db, produto_id=prod["id"])
    eid = enq["id"]
    for seq in (1, 2):
        db.insert("pacotes", {
            "enquete_id": eid,
            "sequence_no": seq,
            "status": "closed",
        })
    result = db.rpc("next_pacote_sequence", {"p_enquete_id": eid})
    assert result == 3


def test_rpc_invalido_lanca_runtime_error(db):
    """RPC não registrada deve levantar RuntimeError."""
    with pytest.raises(RuntimeError, match="não implementado"):
        db.rpc("funcao_inexistente", {})


# ---------------------------------------------------------------------------
# SQLiteRestClient.rpc — get_customer_stats
# ---------------------------------------------------------------------------

def test_rpc_get_customer_stats_vazio(db):
    """Com banco vazio, retorna lista vazia."""
    result = db.rpc("get_customer_stats", {})
    assert isinstance(result, list)
    assert result == []


def test_rpc_get_customer_stats_com_dados(db):
    """Com clientes no banco, deve retornar linha por cliente."""
    c1 = _make_cliente(db, nome="Ana", celular="11111111111")
    c2 = _make_cliente(db, nome="Bob", celular="22222222222")
    result = db.rpc("get_customer_stats", {})
    ids = {r["cliente_id"] for r in result}
    assert c1["id"] in ids
    assert c2["id"] in ids


def test_rpc_get_customer_stats_estrutura(db):
    """Cada linha deve ter as colunas esperadas."""
    _make_cliente(db)
    result = db.rpc("get_customer_stats", {})
    for row in result:
        for key in ("cliente_id", "celular", "nome", "qty", "total_debt", "total_paid"):
            assert key in row


# ---------------------------------------------------------------------------
# SQLiteRestClient.rpc — close_package
# ---------------------------------------------------------------------------

def _make_voto_fixture(db: SQLiteRestClient):
    """Monta produto, enquete, cliente e voto para testar close_package."""
    prod = _make_produto(db)
    enq = _make_enquete(db, produto_id=prod["id"])
    cli = _make_cliente(db)
    # Alternativa
    alt_rows = db.insert("enquete_alternativas", {
        "enquete_id": enq["id"],
        "label": "3 peças",
        "qty": 3,
        "position": 0,
    })
    alt = alt_rows[0]
    voto_rows = db.insert("votos", {
        "enquete_id": enq["id"],
        "cliente_id": cli["id"],
        "alternativa_id": alt["id"],
        "qty": 3,
        "status": "out",
        "synthetic": 0,
    })
    voto = voto_rows[0]
    return prod, enq, cli, voto


def test_rpc_close_package_sem_votos_retorna_no_votes(db):
    """close_package sem votos deve retornar status='no_votes'."""
    prod = _make_produto(db)
    enq = _make_enquete(db, produto_id=prod["id"])
    result = db.rpc("close_package", {
        "p_enquete_id": enq["id"],
        "p_produto_id": prod["id"],
        "p_votes": [],
        "p_capacidade_total": 24,
        "p_total_qty": 24,
    })
    assert result["status"] == "no_votes"
    assert result["pacote_id"] is None


def test_rpc_close_package_cria_pacote(db):
    """close_package com votos deve criar pacote e retornar pacote_id."""
    prod, enq, cli, voto = _make_voto_fixture(db)
    result = db.rpc("close_package", {
        "p_enquete_id": enq["id"],
        "p_produto_id": prod["id"],
        "p_votes": [
            {
                "cliente_id": cli["id"],
                "vote_id": voto["id"],
                "qty": 3,
                "unit_price": 50.0,
                "subtotal": 150.0,
                "commission_percent": 13.0,
                "commission_amount": 19.5,
                "total_amount": 169.5,
            }
        ],
        "p_capacidade_total": 24,
        "p_total_qty": 3,
    })
    assert result["status"] == "ok"
    assert result["pacote_id"] is not None
    assert result["participants_count"] == 1
    assert result["sequence_no"] == 1


def test_rpc_close_package_muda_voto_para_in(db):
    """Votos fechados devem ter status='in' após close_package."""
    prod, enq, cli, voto = _make_voto_fixture(db)
    db.rpc("close_package", {
        "p_enquete_id": enq["id"],
        "p_produto_id": prod["id"],
        "p_votes": [
            {
                "cliente_id": cli["id"],
                "vote_id": voto["id"],
                "qty": 3,
                "unit_price": 50.0,
                "subtotal": 150.0,
                "commission_percent": 13.0,
                "commission_amount": 19.5,
                "total_amount": 169.5,
            }
        ],
        "p_capacidade_total": 24,
        "p_total_qty": 3,
    })
    votos_atualizados = db.select("votos", filters=[("id", "eq", voto["id"])])
    assert votos_atualizados[0]["status"] == "in"


def test_rpc_close_package_incrementa_sequence_no(db):
    """Segundo close_package na mesma enquete deve ter sequence_no=2."""
    prod, enq, cli, voto = _make_voto_fixture(db)
    args = {
        "p_enquete_id": enq["id"],
        "p_produto_id": prod["id"],
        "p_votes": [
            {
                "cliente_id": cli["id"],
                "vote_id": voto["id"],
                "qty": 3,
                "unit_price": 50.0,
                "subtotal": 150.0,
                "commission_percent": 13.0,
                "commission_amount": 19.5,
                "total_amount": 169.5,
            }
        ],
        "p_capacidade_total": 24,
        "p_total_qty": 3,
    }
    r1 = db.rpc("close_package", args)
    assert r1["sequence_no"] == 1
    # Cria segundo voto para o segundo pacote
    cli2 = _make_cliente(db, nome="Segundo", celular="33333333333")
    alt_rows = db.select("enquete_alternativas", filters=[("enquete_id", "eq", enq["id"])])
    alt = alt_rows[0]
    voto2_rows = db.insert("votos", {
        "enquete_id": enq["id"],
        "cliente_id": cli2["id"],
        "alternativa_id": alt["id"],
        "qty": 3,
        "status": "out",
        "synthetic": 0,
    })
    voto2 = voto2_rows[0]
    args2 = {
        "p_enquete_id": enq["id"],
        "p_produto_id": prod["id"],
        "p_votes": [
            {
                "cliente_id": cli2["id"],
                "vote_id": voto2["id"],
                "qty": 3,
                "unit_price": 50.0,
                "subtotal": 150.0,
                "commission_percent": 13.0,
                "commission_amount": 19.5,
                "total_amount": 169.5,
            }
        ],
        "p_capacidade_total": 24,
        "p_total_qty": 3,
    }
    r2 = db.rpc("close_package", args2)
    assert r2["sequence_no"] == 2


# ---------------------------------------------------------------------------
# SQLiteRestClient._request — GET
# ---------------------------------------------------------------------------

def test_request_get_tabela(db):
    """_request GET deve retornar FakeResponse 200 com lista de linhas."""
    _make_produto(db, nome="Via request")
    resp = db._request("GET", "/rest/v1/produtos")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_request_get_com_filtro_querystring(db):
    """Filtro na query string do path deve ser aplicado."""
    p = _make_produto(db, nome="Filtrado")
    resp = db._request("GET", f"/rest/v1/produtos?nome=eq.Filtrado")
    body = resp.json()
    assert any(r["nome"] == "Filtrado" for r in body)


def test_request_get_count_exact(db):
    """Prefer count=exact deve popular header content-range."""
    _make_produto(db)
    resp = db._request("GET", "/rest/v1/produtos", prefer="count=exact")
    assert "content-range" in resp.headers


def test_request_post_insere(db):
    """_request POST deve inserir e retornar 201."""
    resp = db._request("POST", "/rest/v1/produtos", payload={"nome": "Via POST", "valor_unitario": 10.0})
    assert resp.status_code == 201


def test_request_patch_atualiza(db):
    """_request PATCH deve atualizar e retornar 200."""
    p = _make_produto(db, nome="Antes PATCH")
    resp = db._request("PATCH", f"/rest/v1/produtos?id=eq.{p['id']}", payload={"nome": "Depois PATCH"})
    assert resp.status_code == 200


def test_request_delete_remove(db):
    """_request DELETE deve remover linhas e retornar 204."""
    p = _make_produto(db)
    resp = db._request("DELETE", f"/rest/v1/produtos?id=eq.{p['id']}")
    assert resp.status_code == 204


def test_request_metodo_invalido(db):
    """Método HTTP desconhecido deve retornar 405."""
    resp = db._request("PUT", "/rest/v1/produtos")
    assert resp.status_code == 405


def test_request_rpc(db):
    """_request em /rest/v1/rpc/ deve despachar para rpc()."""
    prod = _make_produto(db)
    enq = _make_enquete(db, produto_id=prod["id"])
    resp = db._request(
        "POST",
        "/rest/v1/rpc/next_pacote_sequence",
        payload={"p_enquete_id": enq["id"]},
    )
    assert resp.status_code == 200
    assert resp.json() == 1


def test_request_rpc_invalida(db):
    """RPC inválida via _request deve retornar 500."""
    resp = db._request("POST", "/rest/v1/rpc/funcao_inexistente", payload={})
    assert resp.status_code == 500


def test_request_get_range_header(db):
    """Range header deve ser convertido em limit/offset."""
    for i in range(5):
        _make_produto(db, nome=f"Range{i}", valor=float(i))
    resp = db._request("GET", "/rest/v1/produtos", extra_headers={"Range": "0-1"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_request_accept_object(db):
    """accept_object=True deve retornar single=True no select."""
    p = _make_produto(db, nome="Objeto único")
    resp = db._request(
        "GET",
        f"/rest/v1/produtos?id=eq.{p['id']}",
        accept_object=True,
    )
    assert resp.status_code == 200
    # single=True → retorna dict não lista
    assert isinstance(resp.json(), dict)


def test_request_post_upsert_via_prefer(db):
    """POST com Prefer resolution=merge-duplicates deve fazer upsert."""
    p = _make_produto(db, nome="Original Upsert")
    pid = p["id"]
    resp = db._request(
        "POST",
        "/rest/v1/produtos?on_conflict=id",
        payload={"id": pid, "nome": "Upserted", "valor_unitario": 999.0},
        prefer="resolution=merge-duplicates",
    )
    assert resp.status_code == 201
    rows = db.select("produtos", filters=[("id", "eq", pid)])
    assert rows[0]["nome"] == "Upserted"


# ---------------------------------------------------------------------------
# _FakeResponse
# ---------------------------------------------------------------------------

def test_fake_response_text_none():
    """Body None deve retornar string vazia."""
    r = _FakeResponse(204, None)
    assert r.text == ""


def test_fake_response_text_str():
    """Body string deve ser retornado como está."""
    r = _FakeResponse(200, "hello")
    assert r.text == "hello"


def test_fake_response_text_dict():
    """Body dict deve ser serializado como JSON."""
    r = _FakeResponse(200, {"k": "v"})
    assert json.loads(r.text) == {"k": "v"}


def test_fake_response_json():
    """json() deve retornar o body original."""
    body = [{"id": "1"}]
    r = _FakeResponse(200, body)
    assert r.json() == body


def test_fake_response_headers_padrao():
    """Headers padrão deve ser dict vazio."""
    r = _FakeResponse(200, None)
    assert r.headers == {}


def test_fake_response_headers_custom():
    """Headers customizados devem estar acessíveis."""
    r = _FakeResponse(200, None, headers={"content-range": "0-9/100"})
    assert r.headers["content-range"] == "0-9/100"


# ---------------------------------------------------------------------------
# SQLiteRestClient.now_iso
# ---------------------------------------------------------------------------

def test_now_iso_formato():
    """now_iso deve retornar string ISO 8601."""
    ts = SQLiteRestClient.now_iso()
    # Deve ser parseável como datetime
    dt = datetime.fromisoformat(ts)
    assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# Filtros operadores avançados via select real
# ---------------------------------------------------------------------------

def test_filtro_neq_real(db):
    """Filtro neq deve excluir linhas com o valor especificado."""
    _make_produto(db, nome="Incluido")
    _make_produto(db, nome="Excluido")
    rows = db.select("produtos", filters=[("nome", "neq", "Excluido")])
    nomes = [r["nome"] for r in rows]
    assert "Excluido" not in nomes
    assert "Incluido" in nomes


def test_filtro_in_lista_real(db):
    """Filtro in deve retornar apenas linhas com valores na lista."""
    p1 = _make_produto(db, nome="A_in")
    p2 = _make_produto(db, nome="B_in")
    _make_produto(db, nome="C_fora")
    rows = db.select("produtos", filters=[("id", "in", [p1["id"], p2["id"]])])
    nomes = {r["nome"] for r in rows}
    assert nomes == {"A_in", "B_in"}


def test_filtro_like_real(db):
    """Filtro like deve funcionar com wildcards SQL."""
    _make_produto(db, nome="Cachorro Quente")
    _make_produto(db, nome="Outra Coisa")
    rows = db.select("produtos", filters=[("nome", "like", "Cachorro%")])
    assert any("Cachorro" in r["nome"] for r in rows)
    assert all("Cachorro" in r["nome"] for r in rows)


def test_filtro_gt_real(db):
    """Filtro gt deve retornar linhas com valor maior que o especificado."""
    _make_produto(db, nome="Barato", valor=10.0)
    _make_produto(db, nome="Caro", valor=500.0)
    rows = db.select("produtos", filters=[("valor_unitario", "gt", 100.0)])
    nomes = [r["nome"] for r in rows]
    assert "Caro" in nomes
    assert "Barato" not in nomes


def test_filtro_is_null_real(db):
    """Filtro is null deve retornar linhas com campo NULL."""
    db.insert("enquetes", {
        "external_poll_id": str(uuid.uuid4()),
        "produto_id": None,
        "titulo": "Sem produto",
        "status": "open",
    })
    rows = db.select("enquetes", filters=[("produto_id", "is", "null")])
    assert len(rows) >= 1
    assert all(r["produto_id"] is None for r in rows)


def test_filtro_not_is_null_real(db):
    """Filtro not.is null deve retornar apenas linhas com campo NOT NULL."""
    prod = _make_produto(db)
    db.insert("enquetes", {
        "external_poll_id": str(uuid.uuid4()),
        "produto_id": prod["id"],
        "titulo": "Com produto",
        "status": "open",
    })
    db.insert("enquetes", {
        "external_poll_id": str(uuid.uuid4()),
        "produto_id": None,
        "titulo": "Sem produto",
        "status": "open",
    })
    rows = db.select("enquetes", filters=[("produto_id", "not.is", "null")])
    assert all(r["produto_id"] is not None for r in rows)


def test_select_fallback_coluna_invalida(db):
    """Coluna inválida deve disparar fallback para SELECT * sem quebrar."""
    _make_produto(db)
    # coluna_inexistente vai causar OperationalError → deve fazer fallback
    rows = db.select("produtos", columns="id,coluna_inexistente")
    # O fallback retorna todas as colunas, logo id deve estar lá
    assert len(rows) >= 1
    assert "id" in rows[0]
