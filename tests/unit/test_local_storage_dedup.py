"""Testes do dedup em LocalImageStorage.upload_file.

Bug histórico: cada upload do mesmo (parent, name) gerava UUID novo + grava
o arquivo de novo. 1 imagem virou 17k cópias. Estes testes garantem que a
segunda chamada com mesmo (parent, name) retorna o id existente sem regravar.
"""
from __future__ import annotations

import pytest

from tests._helpers.fake_supabase import FakeSupabaseClient


@pytest.fixture()
def fake_client():
    return FakeSupabaseClient(tables={"drive_files": []})


@pytest.fixture()
def storage(tmp_path, fake_client, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import integrations.local_storage as mod
    importlib.reload(mod)

    # _client é @property que importa SupabaseRestClient lazy; substitui pra fake.
    monkeypatch.setattr(
        mod.LocalImageStorage,
        "_client",
        property(lambda self: fake_client),
    )
    return mod.LocalImageStorage(parent_folder_id="folder-1")


def test_upload_duplicado_reusa_id(storage, fake_client, tmp_path):
    first_id = storage.upload_file("KMEDIA_xyz.jpg", b"\xff\xd8\xff\x00abc", "folder-1")
    second_id = storage.upload_file("KMEDIA_xyz.jpg", b"\xff\xd8\xff\x00abc", "folder-1")

    assert first_id == second_id
    rows = [r for r in fake_client.tables["drive_files"] if r.get("is_folder") == 0]
    assert len(rows) == 1, "Não deve inserir 2ª linha pra mesmo (parent, name)"


def test_upload_em_pasta_diferente_cria_id_novo(storage, fake_client):
    first_id = storage.upload_file("KMEDIA_xyz.jpg", b"a", "folder-1")
    second_id = storage.upload_file("KMEDIA_xyz.jpg", b"a", "folder-2")

    assert first_id != second_id


def test_upload_nome_diferente_cria_id_novo(storage, fake_client):
    first_id = storage.upload_file("KMEDIA_xyz.jpg", b"a", "folder-1")
    second_id = storage.upload_file("KMEDIA_zzz.jpg", b"a", "folder-1")

    assert first_id != second_id


def test_upload_apos_delete_cria_id_novo(storage, fake_client):
    first_id = storage.upload_file("KMEDIA_xyz.jpg", b"a", "folder-1")
    storage.delete_file(first_id)
    second_id = storage.upload_file("KMEDIA_xyz.jpg", b"a", "folder-1")

    # Após soft-delete, dedup não enxerga o id antigo (filtro deleted=0) e
    # cria id novo — comportamento intencional pra evitar "ressuscitar" arquivo.
    assert first_id != second_id
