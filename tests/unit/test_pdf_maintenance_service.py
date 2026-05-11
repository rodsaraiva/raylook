"""Testes de app/services/pdf_maintenance_service.

Cobre: fix_pdf_filenames_and_metadata e run_maintenance.

Toda operação de filesystem é isolada via monkeypatch no cwd,
apontando para tmp_path do pytest, sem tocar em dados reais.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import app.services.pdf_maintenance_service as svc


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_pdf_dir(base: Path) -> Path:
    """Cria etiquetas_estoque/ dentro de base."""
    d = base / "etiquetas_estoque"
    d.mkdir()
    return d


def _make_data_dir(base: Path) -> Path:
    """Cria data/ dentro de base."""
    d = base / "data"
    d.mkdir(exist_ok=True)
    return d


def _write_json(path: Path, content) -> None:
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# ─── fix_pdf_filenames_and_metadata — diretório ausente ───────────────────────

def test_sem_diretorio_retorna_sem_erro(tmp_path, monkeypatch, caplog):
    """Quando etiquetas_estoque/ não existe, não lança exceção."""
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.INFO, logger="raylook.pdf_maintenance"):
        svc.fix_pdf_filenames_and_metadata()
    assert "pulando" in caplog.text.lower()


# ─── fix_pdf_filenames_and_metadata — renomeação de arquivos físicos ──────────

def test_renomeia_pdf_com_nome_nao_sanitizado(tmp_path, monkeypatch):
    """PDF com caracteres especiais é renomeado para forma sanitizada."""
    monkeypatch.chdir(tmp_path)
    pdf_dir = _make_pdf_dir(tmp_path)
    original = pdf_dir / "Pacote (1).pdf"
    original.write_bytes(b"%PDF-1.4")

    svc.fix_pdf_filenames_and_metadata()

    # Deve existir o arquivo sanitizado e não o original com parênteses
    sanitized = pdf_dir / "pacote_1.pdf"
    assert sanitized.exists()
    assert not original.exists()


def test_nao_renomeia_pdf_ja_sanitizado(tmp_path, monkeypatch):
    """PDF cujo nome já é sanitizado não é modificado."""
    monkeypatch.chdir(tmp_path)
    pdf_dir = _make_pdf_dir(tmp_path)
    original = pdf_dir / "pacote_teste.pdf"
    original.write_bytes(b"%PDF-1.4")

    svc.fix_pdf_filenames_and_metadata()

    assert original.exists()


def test_remove_duplicata_quando_sanitizado_ja_existe(tmp_path, monkeypatch):
    """Se o nome sanitizado já existe, o arquivo não-sanitizado é removido."""
    monkeypatch.chdir(tmp_path)
    pdf_dir = _make_pdf_dir(tmp_path)
    sanitized = pdf_dir / "pacote_1.pdf"
    sanitized.write_bytes(b"%PDF-1.4 sanitized")
    # arquivo não-sanitizado com parênteses, cujo sanitize resulta em pacote_1
    nao_sanitizado = pdf_dir / "Pacote (1).pdf"
    nao_sanitizado.write_bytes(b"%PDF-1.4 original")

    svc.fix_pdf_filenames_and_metadata()

    # duplicata removida, sanitizado permanece
    assert sanitized.exists()
    assert not nao_sanitizado.exists()


def test_renomeia_multiplos_pdfs(tmp_path, monkeypatch):
    """Vários PDFs não-sanitizados são todos renomeados."""
    monkeypatch.chdir(tmp_path)
    pdf_dir = _make_pdf_dir(tmp_path)
    arquivos = ["Pacote (1).pdf", "Pacote (2).pdf", "Pacote (3).pdf"]
    for nome in arquivos:
        (pdf_dir / nome).write_bytes(b"%PDF")

    svc.fix_pdf_filenames_and_metadata()

    pdfs = list(pdf_dir.glob("*.pdf"))
    # nenhum nome deve conter parênteses
    for p in pdfs:
        assert "(" not in p.name and ")" not in p.name


def test_pdf_sem_nome_alterado_permanece(tmp_path, monkeypatch):
    """PDFs cujo stem já é sanitizado são preservados sem toque."""
    monkeypatch.chdir(tmp_path)
    pdf_dir = _make_pdf_dir(tmp_path)
    ok = pdf_dir / "sem_problemas.pdf"
    ok.write_bytes(b"%PDF")

    svc.fix_pdf_filenames_and_metadata()

    assert ok.exists()


# ─── fix_pdf_filenames_and_metadata — atualização de confirmed_packages.json ──

def test_atualiza_confirmed_packages_lista(tmp_path, monkeypatch):
    """confirmed_packages.json (lista) tem pdf_file_name atualizado para forma sanitizada."""
    monkeypatch.chdir(tmp_path)
    _make_pdf_dir(tmp_path)
    data_dir = _make_data_dir(tmp_path)
    json_path = data_dir / "confirmed_packages.json"

    original_data = [
        {"id": "1", "pdf_file_name": "Pacote (1).pdf"},
        {"id": "2", "pdf_file_name": "pacote_ok.pdf"},  # já sanitizado
    ]
    _write_json(json_path, original_data)

    svc.fix_pdf_filenames_and_metadata()

    result = _read_json(json_path)
    assert result[0]["pdf_file_name"] == "pacote_1.pdf"
    # já sanitizado não muda
    assert result[1]["pdf_file_name"] == "pacote_ok.pdf"


def test_confirmed_packages_sem_pdf_file_name_nao_modifica(tmp_path, monkeypatch):
    """Entrada sem pdf_file_name não é tocada."""
    monkeypatch.chdir(tmp_path)
    _make_pdf_dir(tmp_path)
    data_dir = _make_data_dir(tmp_path)
    json_path = data_dir / "confirmed_packages.json"

    original_data = [{"id": "1", "nome": "teste"}]
    _write_json(json_path, original_data)

    svc.fix_pdf_filenames_and_metadata()

    result = _read_json(json_path)
    assert result[0] == {"id": "1", "nome": "teste"}


def test_atualiza_packages_state_dicionario(tmp_path, monkeypatch):
    """packages_state.json (dict) tem pdf_file_name atualizado por pkg_id."""
    monkeypatch.chdir(tmp_path)
    _make_pdf_dir(tmp_path)
    data_dir = _make_data_dir(tmp_path)
    json_path = data_dir / "packages_state.json"

    original_data = {
        "pkg1": {"pdf_file_name": "Pacote (A).pdf"},
        "pkg2": {"pdf_file_name": "normal.pdf"},
    }
    _write_json(json_path, original_data)

    svc.fix_pdf_filenames_and_metadata()

    result = _read_json(json_path)
    assert result["pkg1"]["pdf_file_name"] == "pacote_a.pdf"
    assert result["pkg2"]["pdf_file_name"] == "normal.pdf"


def test_json_inexistente_e_ignorado(tmp_path, monkeypatch):
    """Arquivo JSON ausente é simplesmente pulado sem erro."""
    monkeypatch.chdir(tmp_path)
    _make_pdf_dir(tmp_path)
    _make_data_dir(tmp_path)
    # Não cria nenhum JSON

    svc.fix_pdf_filenames_and_metadata()  # não deve lançar


def test_confirmed_packages_sem_alteracao_nao_reescreve(tmp_path, monkeypatch):
    """Quando não há nada para atualizar, o arquivo não é reescrito."""
    monkeypatch.chdir(tmp_path)
    _make_pdf_dir(tmp_path)
    data_dir = _make_data_dir(tmp_path)
    json_path = data_dir / "confirmed_packages.json"

    original_data = [{"id": "1", "pdf_file_name": "ja_sanitizado.pdf"}]
    _write_json(json_path, original_data)
    mtime_antes = json_path.stat().st_mtime

    svc.fix_pdf_filenames_and_metadata()

    mtime_depois = json_path.stat().st_mtime
    assert mtime_antes == mtime_depois


def test_json_corrompido_nao_explode(tmp_path, monkeypatch):
    """JSON inválido no disco não propaga exceção."""
    monkeypatch.chdir(tmp_path)
    _make_pdf_dir(tmp_path)
    data_dir = _make_data_dir(tmp_path)
    (data_dir / "confirmed_packages.json").write_text("INVALIDO", encoding="utf-8")

    svc.fix_pdf_filenames_and_metadata()  # não deve lançar


# ─── run_maintenance ──────────────────────────────────────────────────────────

def test_run_maintenance_executa_sem_erro(tmp_path, monkeypatch):
    """run_maintenance() é wrapper seguro: não propaga exceções."""
    monkeypatch.chdir(tmp_path)
    svc.run_maintenance()


def test_run_maintenance_chama_fix(tmp_path, monkeypatch):
    """run_maintenance delega para fix_pdf_filenames_and_metadata."""
    monkeypatch.chdir(tmp_path)
    chamado = {"ok": False}

    def _fake_fix():
        chamado["ok"] = True

    monkeypatch.setattr(svc, "fix_pdf_filenames_and_metadata", _fake_fix)
    svc.run_maintenance()
    assert chamado["ok"] is True


def test_run_maintenance_captura_excecao(monkeypatch, caplog):
    """Se fix explodir, run_maintenance registra erro mas não propaga."""
    def _explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "fix_pdf_filenames_and_metadata", _explode)
    with caplog.at_level(logging.ERROR, logger="raylook.pdf_maintenance"):
        svc.run_maintenance()
    assert "falha" in caplog.text.lower() or "boom" in caplog.text.lower()
