"""Testes de app/services/staging_dry_run_service.

Cobre: is_staging_dry_run, _clone_metrics, _iter_packages,
_package_matches, simulate_confirm_package, simulate_reject_package,
simulate_tag_package, simulate_update_confirmed_package_votes,
simulate_manual_confirm_package, build_customer_rows,
simulate_customer_rows, simulate_delete_charge.

Todas as funções são puras ou quase-puras (sem IO nem DB).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

import app.services.staging_dry_run_service as svc

# ─── fixtures de dados ─────────────────────────────────────────────────────────

PACKAGE_OPEN = {"id": "p1", "status": "open", "qty": 2}
PACKAGE_CLOSED = {"id": "p2", "status": "closed", "closed_at": "2026-05-10T10:00:00Z"}


def _base_data(open_pkgs=None, closed_today=None, closed_week=None,
               confirmed_today=None, rejected_today=None) -> Dict[str, Any]:
    """Monta estrutura mínima de dados de métricas."""
    return {
        "votos": {
            "packages": {
                "open": open_pkgs or [],
                "closed_today": closed_today or [],
                "closed_week": closed_week or [],
                "confirmed_today": confirmed_today or [],
                "rejected_today": rejected_today or [],
            }
        }
    }


# ─── is_staging_dry_run ────────────────────────────────────────────────────────

def test_is_staging_dry_run_false_por_padrao():
    """Sem TEST_MODE/STAGING_DRY_RUN habilitados, retorna False."""
    with patch.object(svc.settings, "TEST_MODE", False), \
         patch.object(svc.settings, "STAGING_DRY_RUN", False):
        assert svc.is_staging_dry_run() is False


def test_is_staging_dry_run_true_quando_ambos_habilitados():
    """Retorna True apenas quando TEST_MODE e STAGING_DRY_RUN são verdadeiros."""
    with patch.object(svc.settings, "TEST_MODE", True), \
         patch.object(svc.settings, "STAGING_DRY_RUN", True):
        assert svc.is_staging_dry_run() is True


def test_is_staging_dry_run_false_quando_apenas_test_mode():
    with patch.object(svc.settings, "TEST_MODE", True), \
         patch.object(svc.settings, "STAGING_DRY_RUN", False):
        assert svc.is_staging_dry_run() is False


def test_is_staging_dry_run_false_quando_apenas_staging():
    with patch.object(svc.settings, "TEST_MODE", False), \
         patch.object(svc.settings, "STAGING_DRY_RUN", True):
        assert svc.is_staging_dry_run() is False


# ─── _clone_metrics ────────────────────────────────────────────────────────────

def test_clone_metrics_nao_muta_original():
    """Resultado é cópia profunda; alterar o clone não afeta o original."""
    original = _base_data(open_pkgs=[{"id": "p1"}])
    clone = svc._clone_metrics(original)
    clone["votos"]["packages"]["open"][0]["id"] = "MODIFICADO"
    assert original["votos"]["packages"]["open"][0]["id"] == "p1"


def test_clone_metrics_inicializa_todas_as_secoes():
    """Todas as seções de PACKAGE_SECTIONS existem no clone."""
    clone = svc._clone_metrics({})
    for section in svc.PACKAGE_SECTIONS:
        assert section in clone["votos"]["packages"]


def test_clone_metrics_adiciona_generated_at():
    clone = svc._clone_metrics({})
    assert "generated_at" in clone
    # Deve ser um ISO string válido
    from datetime import datetime
    datetime.fromisoformat(clone["generated_at"])


def test_clone_metrics_com_none_retorna_estrutura_vazia():
    clone = svc._clone_metrics(None)
    assert "votos" in clone
    assert "packages" in clone["votos"]


def test_clone_metrics_preserva_campos_extras():
    """Campos fora de votos/packages são preservados."""
    data = {"campo_extra": "valor", "votos": {"packages": {}}}
    clone = svc._clone_metrics(data)
    assert clone["campo_extra"] == "valor"


# ─── _iter_packages ────────────────────────────────────────────────────────────

def test_iter_packages_itera_todas_as_secoes():
    """Itera por pacotes em todas as 5 seções."""
    pkgs = {
        "open": [{"id": "a"}],
        "closed_today": [{"id": "b"}],
        "closed_week": [{"id": "c"}],
        "confirmed_today": [{"id": "d"}],
        "rejected_today": [{"id": "e"}],
    }
    resultado = list(svc._iter_packages(pkgs))
    ids = [row["id"] for _, row in resultado]
    assert sorted(ids) == ["a", "b", "c", "d", "e"]


def test_iter_packages_pula_secao_nao_lista():
    """Seção com valor não-lista (ex: None) é ignorada sem erro."""
    pkgs = {sec: None for sec in svc.PACKAGE_SECTIONS}
    assert list(svc._iter_packages(pkgs)) == []


def test_iter_packages_pula_item_nao_dict():
    """Item não-dict dentro de seção é ignorado."""
    pkgs = {sec: [] for sec in svc.PACKAGE_SECTIONS}
    pkgs["open"] = ["string_nao_dict", {"id": "valido"}]
    resultado = list(svc._iter_packages(pkgs))
    assert len(resultado) == 1
    assert resultado[0][1]["id"] == "valido"


def test_iter_packages_retorna_nome_da_secao():
    pkgs = {sec: [] for sec in svc.PACKAGE_SECTIONS}
    pkgs["confirmed_today"] = [{"id": "x"}]
    secao, _ = list(svc._iter_packages(pkgs))[0]
    assert secao == "confirmed_today"


# ─── _package_matches ──────────────────────────────────────────────────────────

def test_package_matches_por_id():
    row = {"id": "p1", "source_package_id": None}
    assert svc._package_matches(row, "p1") is True


def test_package_matches_por_source_package_id():
    row = {"id": "p99", "source_package_id": "src1"}
    assert svc._package_matches(row, "x", source_package_id="src1") is True


def test_package_matches_falha_quando_diferente():
    row = {"id": "p99", "source_package_id": None}
    assert svc._package_matches(row, "p1") is False


def test_package_matches_ignora_espacos():
    row = {"id": " p1 ", "source_package_id": None}
    assert svc._package_matches(row, "p1") is True


def test_package_matches_row_sem_campos():
    assert svc._package_matches({}, "p1") is False


# ─── simulate_confirm_package ──────────────────────────────────────────────────

def test_confirm_package_move_de_open_para_confirmed_today():
    data = _base_data(open_pkgs=[{"id": "p1", "status": "open"}])
    cloned, moved = svc.simulate_confirm_package(data, "p1")

    assert moved is not None
    assert moved["status"] == "approved"
    assert any(r["id"] == "p1" for r in cloned["votos"]["packages"]["confirmed_today"])
    assert not any(r["id"] == "p1" for r in cloned["votos"]["packages"]["open"])


def test_confirm_package_adiciona_confirmed_at():
    data = _base_data(open_pkgs=[{"id": "p1"}])
    _, moved = svc.simulate_confirm_package(data, "p1")
    assert "confirmed_at" in moved


def test_confirm_package_aplica_tag():
    data = _base_data(open_pkgs=[{"id": "p1"}])
    _, moved = svc.simulate_confirm_package(data, "p1", tag="vip")
    assert moved["tag"] == "vip"


def test_confirm_package_retorna_none_quando_nao_encontrado():
    data = _base_data()
    _, moved = svc.simulate_confirm_package(data, "inexistente")
    assert moved is None


def test_confirm_package_nao_muta_original():
    data = _base_data(open_pkgs=[{"id": "p1"}])
    original_open = list(data["votos"]["packages"]["open"])
    svc.simulate_confirm_package(data, "p1")
    assert data["votos"]["packages"]["open"] == original_open


def test_confirm_package_move_de_closed_today():
    data = _base_data(closed_today=[{"id": "p2", "status": "closed"}])
    cloned, moved = svc.simulate_confirm_package(data, "p2")
    assert moved is not None
    assert moved["status"] == "approved"
    assert not any(r["id"] == "p2" for r in cloned["votos"]["packages"]["closed_today"])


def test_confirm_package_move_de_rejected_today():
    data = _base_data(rejected_today=[{"id": "p3"}])
    cloned, moved = svc.simulate_confirm_package(data, "p3")
    assert moved is not None
    assert not any(r["id"] == "p3" for r in cloned["votos"]["packages"]["rejected_today"])


def test_confirm_package_por_source_package_id():
    data = _base_data(open_pkgs=[{"id": "p99", "source_package_id": "src1"}])
    _, moved = svc.simulate_confirm_package(data, "x", source_package_id="src1")
    assert moved is not None


def test_confirm_package_sem_tag_preserva_tag_existente():
    data = _base_data(open_pkgs=[{"id": "p1", "tag": "existente"}])
    _, moved = svc.simulate_confirm_package(data, "p1", tag=None)
    # tag=None não sobrescreve
    assert moved.get("tag") == "existente"


def test_confirm_package_closed_at_preenchido_quando_ausente():
    data = _base_data(open_pkgs=[{"id": "p1"}])
    _, moved = svc.simulate_confirm_package(data, "p1")
    assert "closed_at" in moved


# ─── simulate_reject_package ──────────────────────────────────────────────────

def test_reject_package_move_de_open_para_rejected_today():
    data = _base_data(open_pkgs=[{"id": "p1"}])
    cloned, moved = svc.simulate_reject_package(data, "p1")

    assert moved is not None
    assert moved["status"] == "cancelled"
    assert any(r["id"] == "p1" for r in cloned["votos"]["packages"]["rejected_today"])
    assert not any(r["id"] == "p1" for r in cloned["votos"]["packages"]["open"])


def test_reject_package_retorna_none_quando_nao_encontrado():
    data = _base_data()
    _, moved = svc.simulate_reject_package(data, "inexistente")
    assert moved is None


def test_reject_package_adiciona_rejected_at():
    data = _base_data(open_pkgs=[{"id": "p1"}])
    _, moved = svc.simulate_reject_package(data, "p1")
    assert "rejected_at" in moved


def test_reject_package_move_de_confirmed_today():
    data = _base_data(confirmed_today=[{"id": "p2", "status": "approved"}])
    cloned, moved = svc.simulate_reject_package(data, "p2")
    assert moved is not None
    assert not any(r["id"] == "p2" for r in cloned["votos"]["packages"]["confirmed_today"])


def test_reject_package_nao_muta_original():
    data = _base_data(open_pkgs=[{"id": "p1"}])
    original_open = list(data["votos"]["packages"]["open"])
    svc.simulate_reject_package(data, "p1")
    assert data["votos"]["packages"]["open"] == original_open


# ─── simulate_tag_package ─────────────────────────────────────────────────────

def test_tag_package_atualiza_tag_existente():
    data = _base_data(open_pkgs=[{"id": "p1", "tag": "velha"}])
    cloned, ok = svc.simulate_tag_package(data, "p1", tag="nova")
    assert ok is True
    assert cloned["votos"]["packages"]["open"][0]["tag"] == "nova"


def test_tag_package_define_none():
    data = _base_data(open_pkgs=[{"id": "p1", "tag": "algo"}])
    cloned, ok = svc.simulate_tag_package(data, "p1", tag=None)
    assert ok is True
    assert cloned["votos"]["packages"]["open"][0]["tag"] is None


def test_tag_package_retorna_false_quando_nao_encontrado():
    data = _base_data()
    _, ok = svc.simulate_tag_package(data, "inexistente", tag="x")
    assert ok is False


def test_tag_package_nao_muta_original():
    data = _base_data(open_pkgs=[{"id": "p1", "tag": "original"}])
    svc.simulate_tag_package(data, "p1", tag="nova")
    assert data["votos"]["packages"]["open"][0]["tag"] == "original"


# ─── simulate_update_confirmed_package_votes ──────────────────────────────────

def test_update_votes_atualiza_campos_do_pacote():
    data = _base_data(confirmed_today=[{"id": "p1"}])
    votes = [{"phone": "5511999", "name": "Ana", "qty": 3}]
    cloned, ok = svc.simulate_update_confirmed_package_votes(data, "p1", votes=votes)

    assert ok is True
    pkg = cloned["votos"]["packages"]["confirmed_today"][0]
    assert pkg["qty"] == 3
    assert pkg["pdf_status"] == "queued"
    assert pkg["pdf_attempts"] == 0
    assert pkg["pdf_file_name"] is None


def test_update_votes_normaliza_campos_do_voto():
    data = _base_data(confirmed_today=[{"id": "p1"}])
    # voto com campos ausentes → defaults
    votes = [{"phone": None, "qty": None}]
    cloned, ok = svc.simulate_update_confirmed_package_votes(data, "p1", votes=votes)

    assert ok is True
    pkg = cloned["votos"]["packages"]["confirmed_today"][0]
    assert pkg["votes"][0]["phone"] == ""
    assert pkg["votes"][0]["name"] == "Cliente"
    assert pkg["votes"][0]["qty"] == 0


def test_update_votes_soma_qty_total():
    data = _base_data(confirmed_today=[{"id": "p1"}])
    votes = [{"phone": "a", "qty": 2}, {"phone": "b", "qty": 5}]
    cloned, ok = svc.simulate_update_confirmed_package_votes(data, "p1", votes=votes)
    assert cloned["votos"]["packages"]["confirmed_today"][0]["qty"] == 7


def test_update_votes_retorna_false_quando_nao_encontrado():
    data = _base_data()
    _, ok = svc.simulate_update_confirmed_package_votes(data, "inexistente", votes=[])
    assert ok is False


def test_update_votes_nao_muta_original():
    data = _base_data(confirmed_today=[{"id": "p1", "qty": 99}])
    svc.simulate_update_confirmed_package_votes(data, "p1", votes=[])
    assert data["votos"]["packages"]["confirmed_today"][0]["qty"] == 99


# ─── simulate_manual_confirm_package ──────────────────────────────────────────

def test_manual_confirm_insere_em_confirmed_today():
    data = _base_data()
    snapshot = {"id": "p1", "status": "open"}
    cloned = svc.simulate_manual_confirm_package(data, snapshot)
    confirmed = cloned["votos"]["packages"]["confirmed_today"]
    assert len(confirmed) == 1
    assert confirmed[0]["id"] == "p1"
    assert confirmed[0]["status"] == "approved"


def test_manual_confirm_adiciona_confirmed_at():
    data = _base_data()
    cloned = svc.simulate_manual_confirm_package(data, {"id": "p1"})
    assert "confirmed_at" in cloned["votos"]["packages"]["confirmed_today"][0]


def test_manual_confirm_nao_muta_snapshot_original():
    data = _base_data()
    snapshot = {"id": "p1", "status": "original"}
    svc.simulate_manual_confirm_package(data, snapshot)
    assert snapshot["status"] == "original"


def test_manual_confirm_prepende_ao_existente():
    data = _base_data(confirmed_today=[{"id": "antigo"}])
    svc.simulate_manual_confirm_package(data, {"id": "novo"})
    # original não muta; o clone já deve ter o novo na frente
    cloned = svc.simulate_manual_confirm_package(data, {"id": "novo"})
    assert cloned["votos"]["packages"]["confirmed_today"][0]["id"] == "novo"


# ─── build_customer_rows ──────────────────────────────────────────────────────

def test_build_customer_rows_agrega_qty_e_total_paid():
    customers = {"5511999": "Ana"}
    charges = [
        {"customer_phone": "5511999", "quantity": 2, "status": "paid", "total_amount": 30.0},
        {"customer_phone": "5511999", "quantity": 1, "status": "paid", "total_amount": 15.0},
    ]
    rows = svc.build_customer_rows(customers, charges)
    assert len(rows) == 1
    assert rows[0]["qty"] == 3
    assert rows[0]["total_paid"] == 45.0


def test_build_customer_rows_so_conta_paid_no_total():
    customers = {"5511999": "Ana"}
    charges = [
        {"customer_phone": "5511999", "quantity": 1, "status": "created", "total_amount": 50.0},
        {"customer_phone": "5511999", "quantity": 2, "status": "paid", "total_amount": 20.0},
    ]
    rows = svc.build_customer_rows(customers, charges)
    assert rows[0]["total_paid"] == 20.0
    assert rows[0]["qty"] == 3  # qty conta criado+pago


def test_build_customer_rows_cliente_sem_cobranças():
    customers = {"5511999": "Ana"}
    rows = svc.build_customer_rows(customers, [])
    assert rows[0]["qty"] == 0
    assert rows[0]["total_paid"] == 0.0


def test_build_customer_rows_ignora_cobranca_sem_phone():
    customers = {"5511999": "Ana"}
    charges = [
        {"customer_phone": "", "quantity": 5, "status": "paid", "total_amount": 100.0},
    ]
    rows = svc.build_customer_rows(customers, charges)
    assert rows[0]["qty"] == 0


def test_build_customer_rows_multiplos_clientes():
    customers = {"111": "A", "222": "B"}
    charges = [
        {"customer_phone": "111", "quantity": 1, "status": "paid", "total_amount": 10.0},
        {"customer_phone": "222", "quantity": 2, "status": "paid", "total_amount": 20.0},
    ]
    rows = svc.build_customer_rows(customers, charges)
    by_phone = {r["phone"]: r for r in rows}
    assert by_phone["111"]["qty"] == 1
    assert by_phone["222"]["qty"] == 2


def test_build_customer_rows_arredonda_total_paid():
    customers = {"111": "A"}
    charges = [
        {"customer_phone": "111", "quantity": 1, "status": "paid", "total_amount": 10.005},
    ]
    rows = svc.build_customer_rows(customers, charges)
    assert rows[0]["total_paid"] == round(10.005, 2)


# ─── simulate_customer_rows ───────────────────────────────────────────────────

def test_simulate_customer_rows_adiciona_novo_cliente():
    customers = {"111": "Existente"}
    charges = [{"customer_phone": "222", "quantity": 3, "status": "paid", "total_amount": 30.0}]
    rows = svc.simulate_customer_rows(customers, charges, phone="222", name="Novo")
    by_phone = {r["phone"]: r for r in rows}
    assert "222" in by_phone
    assert by_phone["222"]["qty"] == 3


def test_simulate_customer_rows_nao_altera_customers_original():
    customers = {"111": "A"}
    svc.simulate_customer_rows(customers, [], phone="222", name="B")
    assert "222" not in customers


def test_simulate_customer_rows_sobrescreve_nome_existente():
    customers = {"111": "Antigo"}
    rows = svc.simulate_customer_rows(customers, [], phone="111", name="Novo")
    by_phone = {r["phone"]: r for r in rows}
    assert by_phone["111"]["name"] == "Novo"


# ─── simulate_delete_charge ───────────────────────────────────────────────────

def test_delete_charge_remove_por_id():
    charges = [{"id": "c1", "valor": 10}, {"id": "c2", "valor": 20}]
    result = svc.simulate_delete_charge(charges, "c1")
    ids = [c["id"] for c in result]
    assert "c1" not in ids
    assert "c2" in ids


def test_delete_charge_nao_muta_lista_original():
    charges = [{"id": "c1"}]
    svc.simulate_delete_charge(charges, "c1")
    assert len(charges) == 1


def test_delete_charge_id_inexistente_retorna_todos():
    charges = [{"id": "c1"}, {"id": "c2"}]
    result = svc.simulate_delete_charge(charges, "c99")
    assert len(result) == 2


def test_delete_charge_lista_vazia():
    assert svc.simulate_delete_charge([], "c1") == []


def test_delete_charge_retorna_copias_profundas():
    """Mutação no resultado não afeta original."""
    charges = [{"id": "c1", "campo": "original"}]
    result = svc.simulate_delete_charge(charges, "c99")
    result[0]["campo"] = "modificado"
    assert charges[0]["campo"] == "original"
