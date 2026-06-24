# tests/unit/test_whatsapp_domain_accumulate.py
from copy import deepcopy
import importlib

from app.services.whatsapp_domain_service import PackageService

fake_mod = importlib.import_module("tests.unit.test_whatsapp_domain_package_rebuild")
FakeClient = fake_mod.FakeClient


def _base_tables(titulo, votos):
    return {
        "enquetes": [{"id": "e1", "titulo": titulo, "produto_id": "prod1", "fornecedor": None,
                      "produtos": {"id": "prod1", "valor_unitario": 10.0}}],
        "produtos": [{"id": "prod1", "valor_unitario": 10.0}],
        "votos": votos,
        "pacotes": [],
        "pacote_clientes": [],
    }


def _vote(vid, cid, qty):
    return {"id": vid, "enquete_id": "e1", "cliente_id": cid, "alternativa_id": None, "qty": qty,
            "voted_at": f"2026-06-24T10:0{vid[-1]}:00Z", "status": "in"}


def test_accumulate_does_not_close_at_24():
    # 16 + 16 = 32 (passaria de 24): no modo Bernardo NÃO fecha nada.
    tables = _base_tables("Lote Bernardo 1", [_vote("v1", "c1", 16), _vote("v2", "c2", 16)])
    client = FakeClient(tables)
    res = PackageService(client).rebuild_for_poll("e1")
    assert res["mode"] == "accumulate"
    pacotes = client.tables["pacotes"]
    assert len(pacotes) == 1
    assert pacotes[0]["status"] == "open"
    assert pacotes[0]["sequence_no"] == 0
    assert pacotes[0]["total_qty"] == 32
    assert pacotes[0]["participants_count"] == 2


def test_accumulate_never_deletes_closed_package():
    tables = _base_tables("Bernardo", [_vote("v3", "c3", 9)])
    tables["pacotes"].append({"id": "old", "enquete_id": "e1", "sequence_no": 1,
                              "status": "closed", "total_qty": 24, "capacidade_total": 24})
    client = FakeClient(tables)
    PackageService(client).rebuild_for_poll("e1")
    ids = {p["id"] for p in client.tables["pacotes"]}
    assert "old" in ids  # pacote closed preservado


def test_accumulate_subtracts_votes_already_in_closed_package():
    # c1 já tem 6 num pacote closed; agora aumentou pra 9 -> só +3 fica pendente.
    tables = _base_tables("Bernardo", [_vote("v1", "c1", 9)])
    tables["pacotes"].append({"id": "old", "enquete_id": "e1", "sequence_no": 1,
                              "status": "closed", "total_qty": 6, "capacidade_total": 6})
    tables["pacote_clientes"].append({"id": "pc1", "pacote_id": "old", "cliente_id": "c1",
                                      "voto_id": "v1", "produto_id": "prod1", "qty": 6})
    client = FakeClient(tables)
    PackageService(client).rebuild_for_poll("e1")
    opens = [p for p in client.tables["pacotes"] if p["status"] == "open"]
    assert len(opens) == 1
    assert opens[0]["total_qty"] == 3


def test_non_bernardo_still_closes_at_24():
    # Não-regressão: título sem match fecha exatamente 24 (12+12).
    tables = _base_tables("Camisa lisa", [_vote("v1", "c1", 12), _vote("v2", "c2", 12)])
    client = FakeClient(tables)
    res = PackageService(client).rebuild_for_poll("e1")
    assert res.get("closed_count") == 1
    closed = [p for p in client.tables["pacotes"] if p["status"] == "closed"]
    assert len(closed) == 1
    assert closed[0]["total_qty"] == 24


def test_accumulate_removes_open_when_all_votes_consumed():
    # c1 tem qty 6; já consumido integralmente num pacote closed.
    # Existe também um pacote open seq-0 stale -> deve ser removido.
    tables = _base_tables("Bernardo", [_vote("v1", "c1", 6)])
    tables["pacotes"].append({
        "id": "old", "enquete_id": "e1", "sequence_no": 1,
        "status": "closed", "total_qty": 6, "capacidade_total": 6,
    })
    tables["pacote_clientes"].append({
        "id": "pc1", "pacote_id": "old", "cliente_id": "c1",
        "voto_id": "v1", "produto_id": "prod1", "qty": 6,
    })
    tables["pacotes"].append({
        "id": "open0", "enquete_id": "e1", "sequence_no": 0,
        "status": "open", "total_qty": 6, "capacidade_total": 6, "participants_count": 1,
    })
    client = FakeClient(tables)
    PackageService(client).rebuild_for_poll("e1")
    opens = [p for p in client.tables["pacotes"] if p["status"] == "open"]
    assert opens == [], "pacote open seq-0 stale deve ser removido quando pending == 0"
    assert any(p["id"] == "old" for p in client.tables["pacotes"]), "pacote closed deve ser preservado"


def test_close_accumulated_freezes_current_votes():
    tables = _base_tables("Bernardo", [_vote("v1", "c1", 16), _vote("v2", "c2", 16)])
    client = FakeClient(tables)
    svc = PackageService(client)
    svc.rebuild_for_poll("e1")  # cria open com 32
    res = svc.close_accumulated("e1")
    assert res["status"] == "ok"
    assert res["total_qty"] == 32
    assert res["participants"] == 2
    closed = [p for p in client.tables["pacotes"] if p["status"] == "closed"]
    assert len(closed) == 1
    assert closed[0]["total_qty"] == 32
    assert closed[0]["capacidade_total"] == 32
    assert closed[0]["sequence_no"] == 1
    pcs = [pc for pc in client.tables["pacote_clientes"] if pc["pacote_id"] == closed[0]["id"]]
    assert {pc["cliente_id"] for pc in pcs} == {"c1", "c2"}
    # open some (votos consumidos)
    assert not [p for p in client.tables["pacotes"] if p["status"] == "open"]


def test_close_accumulated_empty_returns_no_votes():
    tables = _base_tables("Bernardo", [])
    client = FakeClient(tables)
    assert PackageService(client).close_accumulated("e1")["status"] == "no_votes"


def test_close_then_new_vote_starts_second_package():
    tables = _base_tables("Bernardo", [_vote("v1", "c1", 9)])
    client = FakeClient(tables)
    svc = PackageService(client)
    svc.rebuild_for_poll("e1")
    svc.close_accumulated("e1")          # pacote 1 (seq 1) com 9
    client.tables["votos"].append(_vote("v2", "c2", 12))  # voto novo
    svc.rebuild_for_poll("e1")           # reabre acúmulo
    opens = [p for p in client.tables["pacotes"] if p["status"] == "open"]
    assert len(opens) == 1
    assert opens[0]["total_qty"] == 12   # só o voto novo
    res = svc.close_accumulated("e1")    # pacote 2 (seq 2)
    seqs = sorted(p["sequence_no"] for p in client.tables["pacotes"] if p["status"] == "closed")
    assert seqs == [1, 2]
    assert res["total_qty"] == 12


def test_close_rejects_non_session_enquete():
    tables = _base_tables("Camisa lisa", [_vote("v1", "c1", 6)])
    client = FakeClient(tables)
    assert PackageService(client).close_accumulated("e1")["status"] == "not_session"
