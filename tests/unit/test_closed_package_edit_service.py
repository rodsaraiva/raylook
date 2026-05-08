"""Testes do closed_package_edit_service.

Cobrem:
  - get_edit_data monta available (fila) e selected (membros atuais) corretamente.
  - apply_edit aplica diff só em pacote_clientes.
  - Não mexe em vendas/pagamentos.
  - Bloqueia se status != 'closed'.
"""
from unittest.mock import MagicMock

import pytest


def _make_fake_client():
    fake = MagicMock()
    # select padrão — sobrescreva side_effect por teste
    fake.select.side_effect = lambda *a, **k: None
    fake.select_all.side_effect = lambda *a, **k: []
    fake.update.side_effect = lambda *a, **k: None
    fake.insert.side_effect = lambda *a, **k: [{}]
    fake.upsert_one.side_effect = lambda *a, **k: {"id": "NEW-CLI"}

    patches = []
    def req(method, path, payload=None, prefer=None):
        r = MagicMock()
        r.status_code = 204
        patches.append({"method": method, "path": path, "payload": payload})
        return r
    fake._request.side_effect = req
    fake.patches = patches
    return fake


def _install(monkeypatch, fake):
    from app.services import closed_package_edit_service as cpes
    monkeypatch.setattr(
        cpes,
        "SupabaseRestClient",
        MagicMock(from_settings=MagicMock(return_value=fake)),
    )
    return cpes


def test_get_edit_data_returns_available_and_selected(monkeypatch):
    fake = _make_fake_client()

    def select(table, columns=None, filters=None, **kw):
        if table == "pacotes":
            # contexto do pacote OU lista de pacotes da enquete
            if any(f[0] == "id" for f in (filters or [])):
                return {
                    "id": "PKG-1", "enquete_id": "E-1", "status": "closed",
                    "enquete": {"produto_id": "P-1", "produtos": {"valor_unitario": 25}},
                }
            # lista de pacotes da enquete (busy clientes)
            return [{"id": "PKG-1", "status": "closed"}, {"id": "PKG-2", "status": "approved"}]
        if table == "pacote_clientes":
            # membros do próprio pacote
            if any(f[0] == "pacote_id" and f[2] == "PKG-1" for f in (filters or [])):
                return [
                    {"cliente_id": "C1", "qty": 12, "cliente": {"nome": "Ana", "celular": "5511111"}},
                    {"cliente_id": "C2", "qty": 12, "cliente": {"nome": "Bia", "celular": "5522222"}},
                ]
            return []
        if table == "votos":
            # fila da enquete
            return [
                {"id": "V1", "cliente_id": "C1", "qty": 12, "voted_at": "2026-04-19", "cliente": {"nome": "Ana", "celular": "5511111"}},
                {"id": "V2", "cliente_id": "C2", "qty": 12, "voted_at": "2026-04-19", "cliente": {"nome": "Bia", "celular": "5522222"}},
                {"id": "V3", "cliente_id": "C3", "qty": 6, "voted_at": "2026-04-19", "cliente": {"nome": "Carol", "celular": "5533333"}},
                {"id": "V4", "cliente_id": "C4", "qty": 6, "voted_at": "2026-04-19", "cliente": {"nome": "Dani", "celular": "5544444"}},
            ]
        return None

    def select_all(table, columns=None, filters=None, **kw):
        if table == "pacote_clientes":
            # clientes em pacotes não-cancelados da enquete
            return [
                {"cliente_id": "C1", "pacote_id": "PKG-1"},
                {"cliente_id": "C2", "pacote_id": "PKG-1"},
                {"cliente_id": "C4", "pacote_id": "PKG-2"},  # em outro pacote — não pode estar disponível
            ]
        return []

    fake.select.side_effect = select
    fake.select_all.side_effect = select_all
    cpes = _install(monkeypatch, fake)

    data = cpes.get_edit_data("PKG-1")
    sel_phones = {v["phone"] for v in data["selected_votes"]}
    avail_phones = {v["phone"] for v in data["available_votes"]}

    assert sel_phones == {"5511111", "5522222"}
    # C3 (Carol) livre, C4 (Dani) em outro pacote — fora
    assert avail_phones == {"5533333"}
    assert data["selected_qty"] == 24


def test_apply_edit_only_touches_pacote_clientes(monkeypatch):
    fake = _make_fake_client()

    def select(table, columns=None, filters=None, **kw):
        if table == "pacotes":
            return {
                "id": "PKG-1", "enquete_id": "E-1", "status": "closed",
                "enquete": {"produto_id": "P-1", "produtos": {"valor_unitario": 25}},
            }
        if table == "pacote_clientes":
            # membros atuais (SELECT no _fetch_selected_votes)
            pacote_filter = [f for f in (filters or []) if f[0] == "pacote_id"]
            if pacote_filter and not any(f[0] == "cliente_id" for f in (filters or [])):
                return [
                    {"cliente_id": "C1", "qty": 12, "cliente": {"nome": "Ana", "celular": "5511111"}},
                    {"cliente_id": "C2", "qty": 12, "cliente": {"nome": "Bia", "celular": "5522222"}},
                ]
            # DELETE lookup — remove Bia
            cliente_filter = [f[2] for f in (filters or []) if f[0] == "cliente_id"]
            if cliente_filter and cliente_filter[0] == "C2":
                return {"id": "PC-BIA"}
            return None
        if table == "clientes":
            cliente_filter = [f[2] for f in (filters or []) if f[0] == "celular"]
            if cliente_filter:
                mapping = {"5511111": {"id": "C1"}, "5522222": {"id": "C2"}, "5533333": {"id": "C3"}}
                return mapping.get(cliente_filter[0])
            return None
        if table == "votos":
            return [{"id": "V3"}]
        return None

    fake.select.side_effect = select
    cpes = _install(monkeypatch, fake)

    # Trocar Bia por Carol (6 pçs) + aumentar Ana pra 18 pçs (mantém total = 24)
    new_votes = [
        {"phone": "5511111", "name": "Ana", "qty": 18},
        {"phone": "5533333", "name": "Carol", "qty": 6},
    ]
    summary = cpes.apply_edit("PKG-1", new_votes)

    assert summary["removed"] == 1  # Bia
    assert summary["added"] == 1  # Carol
    assert summary["changed_qty"] == 1  # Ana 12→18

    # Confere que NÃO tocou em vendas/pagamentos
    paths = [p["path"] for p in fake.patches]
    assert not any("/vendas" in p for p in paths)
    assert not any("/pagamentos" in p for p in paths)
    # Deve ter 1 DELETE em pacote_clientes (Bia)
    assert any(p["method"] == "DELETE" and "/pacote_clientes" in p["path"] for p in fake.patches)


def test_apply_edit_validates_total_24(monkeypatch):
    fake = _make_fake_client()
    fake.select.side_effect = lambda table, **kw: (
        {"id": "PKG-1", "enquete_id": "E-1", "status": "closed",
         "enquete": {"produto_id": "P-1", "produtos": {"valor_unitario": 25}}}
        if table == "pacotes" else None
    )
    cpes = _install(monkeypatch, fake)

    with pytest.raises(ValueError, match="24 peças"):
        cpes.apply_edit("PKG-1", [{"phone": "5511111", "qty": 10}])


def test_apply_edit_rejects_non_closed(monkeypatch):
    fake = _make_fake_client()
    fake.select.side_effect = lambda table, **kw: (
        {"id": "PKG-1", "enquete_id": "E-1", "status": "approved",
         "enquete": {"produto_id": "P-1", "produtos": {"valor_unitario": 25}}}
        if table == "pacotes" else None
    )
    cpes = _install(monkeypatch, fake)

    with pytest.raises(ValueError, match="closed"):
        cpes.apply_edit("PKG-1", [{"phone": "5511111", "qty": 24}])


def test_get_edit_data_not_found(monkeypatch):
    fake = _make_fake_client()
    fake.select.side_effect = lambda table, **kw: None
    cpes = _install(monkeypatch, fake)

    with pytest.raises(cpes.ClosedPackageNotFound):
        cpes.get_edit_data("NOPE")
