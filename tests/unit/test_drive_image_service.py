"""
Testes unitários para app/services/drive_image_service.py.

Cobre o fluxo completo de _attach_poll_image_sync e attach_poll_image:
- cache hit → baixa mídia → cria pasta → sobe arquivo → atualiza enquete
- sem cache → busca mensagens WHAPI → encontra imagem → fluxo normal
- falhas em cada etapa (get_recent_messages, download_media, create_folder, upload_file)
- _update_enquete_drive_ids com domínio habilitado e desabilitado
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from tests._helpers.fake_supabase import FakeSupabaseClient, empty_tables


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class FakeDriveClient:
    """FakeClient que espelha GoogleDriveClient/LocalImageStorage."""

    def __init__(self, *a, **kw):
        self.folders: Dict[str, str] = {}   # name → id
        self.files: Dict[str, bytes] = {}   # file_id → bytes

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        fid = f"folder_{name}"
        self.folders[name] = fid
        return fid

    def upload_file(
        self,
        name: str,
        content_bytes: bytes,
        parent_folder_id: Optional[str] = None,
        mime_type: str = "image/jpeg",
    ) -> str:
        fid = f"file_{name}"
        self.files[fid] = content_bytes
        return fid

    def get_public_url(self, file_id: str) -> str:
        return f"/files/{file_id}"


class FakeWHAPIClient:
    """Stub do WHAPIClient que controla o retorno de cada método."""

    def __init__(
        self,
        messages: Optional[List[Dict[str, Any]]] = None,
        image_msg: Optional[Dict[str, Any]] = None,
        media_bytes: Optional[bytes] = None,
        raise_on_messages: bool = False,
        raise_on_download: bool = False,
    ):
        self._messages = messages or []
        self._image_msg = image_msg
        self._media_bytes = media_bytes or b"\xff\xd8\xff"  # JPEG stub
        self._raise_on_messages = raise_on_messages
        self._raise_on_download = raise_on_download

    def get_recent_messages(self, chat_id: str, time_to=None, limit: int = 30):
        if self._raise_on_messages:
            raise RuntimeError("WHAPI offline")
        return self._messages

    def find_image_before_poll(self, messages):
        return self._image_msg

    def download_media(self, media_id: str) -> bytes:
        if self._raise_on_download:
            raise RuntimeError("download failed")
        return self._media_bytes


# ---------------------------------------------------------------------------
# Fixtures comuns
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_db():
    """Banco em memória com uma enquete pré-existente."""
    tables = empty_tables()
    tables["enquetes"] = [
        {
            "id": "enq-uuid-1",
            "external_poll_id": "POLL_001",
            "titulo": "Enquete Teste",
            "chat_id": "120363@g.us",
            "drive_file_id": None,
            "drive_folder_id": None,
        }
    ]
    return FakeSupabaseClient(tables)


class _ExtendedFake(FakeSupabaseClient):
    """FakeSupabaseClient que aceita o kwarg `returning` usado em _update_enquete_drive_ids."""

    def update(self, table, values, *, filters=None, returning=None):
        return super().update(table, values, filters=filters)


def _patch_all(monkeypatch, fake_db, fake_whapi, fake_drive=None):
    """Aplica todos os patches necessários para _attach_poll_image_sync.

    Envolve fake_db em _ExtendedFake para aceitar o kwarg `returning` do update.
    """
    if fake_drive is None:
        fake_drive = FakeDriveClient()

    ext_db = _ExtendedFake(fake_db.tables)

    monkeypatch.setattr(
        "app.services.drive_image_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: ext_db),
    )
    monkeypatch.setattr(
        "app.services.supabase_service.SupabaseRestClient.from_settings",
        staticmethod(lambda: ext_db),
    )
    import app.services.supabase_service as ss
    monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: True)

    # WHAPIClient e GoogleDriveClient são importados dentro de _attach_poll_image_sync
    monkeypatch.setattr(
        "integrations.whapi.WHAPIClient",
        lambda *a, **kw: fake_whapi,
    )
    monkeypatch.setattr(
        "integrations.google_drive.GoogleDriveClient",
        lambda *a, **kw: fake_drive,
    )
    return fake_drive, ext_db


# ---------------------------------------------------------------------------
# Testes de _attach_poll_image_sync (caminho síncrono)
# ---------------------------------------------------------------------------

class TestAttachPollImageSync:

    def test_fluxo_completo_via_cache(self, monkeypatch, fake_db):
        """Cache hit: usa media_id do cache, pula get_recent_messages."""
        cached_entry = {
            "media_id": "media-123",
            "message_id": "msg-abc",
            "timestamp": 1_700_000_000,
        }
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: cached_entry,
        )

        fake_whapi = FakeWHAPIClient(media_bytes=b"\xff\xd8\xffDATA")
        fake_drive, ext_db = _patch_all(monkeypatch, fake_db, fake_whapi)

        # Precisa configurar WHAPI_TOKEN para WHAPIClient não explodir
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")

        assert result == "file_msg-abc.jpg"
        assert "POLL_001" in fake_drive.folders
        enquete = ext_db.tables["enquetes"][0]
        assert enquete["drive_file_id"] == "file_msg-abc.jpg"
        assert enquete["drive_folder_id"] == "folder_POLL_001"

    def test_fluxo_completo_sem_cache(self, monkeypatch, fake_db):
        """Sem cache: busca mensagens WHAPI e encontra imagem antes do poll."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: None,
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        image_msg = {
            "id": "imgmsg-99",
            "type": "image",
            "image": {"id": "mediaxyz"},
        }
        fake_whapi = FakeWHAPIClient(
            messages=[{"type": "poll", "id": "POLL_001"}, image_msg],
            image_msg=image_msg,
            media_bytes=b"IMGDATA",
        )
        fake_drive, _ = _patch_all(monkeypatch, fake_db, fake_whapi)

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")

        assert result == "file_imgmsg-99.jpg"
        assert fake_drive.files["file_imgmsg-99.jpg"] == b"IMGDATA"

    def test_retorna_none_quando_get_recent_messages_falha(self, monkeypatch, fake_db):
        """Falha em get_recent_messages deve retornar None sem propagar exceção."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: None,
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        fake_whapi = FakeWHAPIClient(raise_on_messages=True)
        _patch_all(monkeypatch, fake_db, fake_whapi)

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        assert result is None

    def test_retorna_none_quando_nenhuma_imagem_antes_do_poll(self, monkeypatch, fake_db):
        """find_image_before_poll retorna None → sem imagem → retorna None."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: None,
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        fake_whapi = FakeWHAPIClient(messages=[], image_msg=None)
        _patch_all(monkeypatch, fake_db, fake_whapi)

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        assert result is None

    def test_retorna_none_quando_image_msg_sem_media_id(self, monkeypatch, fake_db):
        """Mensagem de imagem sem campo image.id → retorna None."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: None,
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        image_msg_sem_id = {"id": "imgmsg-no-media", "type": "image", "image": {}}
        fake_whapi = FakeWHAPIClient(messages=[], image_msg=image_msg_sem_id)
        _patch_all(monkeypatch, fake_db, fake_whapi)

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        assert result is None

    def test_retorna_none_quando_download_media_falha(self, monkeypatch, fake_db):
        """Falha em download_media deve retornar None."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: {
                "media_id": "m-err",
                "message_id": "msg-err",
                "timestamp": 1_700_000_000,
            },
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        fake_whapi = FakeWHAPIClient(raise_on_download=True)
        _patch_all(monkeypatch, fake_db, fake_whapi)

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        assert result is None

    def test_retorna_none_quando_create_folder_falha(self, monkeypatch, fake_db):
        """Falha em create_folder deve retornar None."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: {
                "media_id": "m-ok",
                "message_id": "msg-ok",
                "timestamp": 1_700_000_000,
            },
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        class BrokenDriveCreate(FakeDriveClient):
            def create_folder(self, name, parent_id=None):
                raise RuntimeError("drive error")

        fake_whapi = FakeWHAPIClient()
        _patch_all(monkeypatch, fake_db, fake_whapi, fake_drive=BrokenDriveCreate())

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        assert result is None

    def test_retorna_none_quando_upload_file_falha(self, monkeypatch, fake_db):
        """Falha em upload_file deve retornar None."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: {
                "media_id": "m-ok",
                "message_id": "msg-ok",
                "timestamp": 1_700_000_000,
            },
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        class BrokenDriveUpload(FakeDriveClient):
            def upload_file(self, name, content_bytes, parent_folder_id=None, mime_type="image/jpeg"):
                raise RuntimeError("upload error")

        fake_whapi = FakeWHAPIClient()
        _patch_all(monkeypatch, fake_db, fake_whapi, fake_drive=BrokenDriveUpload())

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        assert result is None

    def test_cache_sem_media_id_cai_para_whapi(self, monkeypatch, fake_db):
        """Cache encontrado mas sem media_id válido → busca via WHAPI."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: {"media_id": "", "message_id": "msg-x", "timestamp": 1_700_000_000},
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")

        image_msg = {"id": "img-whapi", "type": "image", "image": {"id": "mwapi"}}
        fake_whapi = FakeWHAPIClient(messages=[], image_msg=image_msg, media_bytes=b"WD")
        fake_drive, _ = _patch_all(monkeypatch, fake_db, fake_whapi)

        from app.services.drive_image_service import _attach_poll_image_sync

        result = _attach_poll_image_sync("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        assert result == "file_img-whapi.jpg"


# ---------------------------------------------------------------------------
# Testes de _update_enquete_drive_ids
# ---------------------------------------------------------------------------

class TestUpdateEnqueteDriveIds:

    def _make_db_accepting_returning(self, monkeypatch, fake_db):
        """Subclasse de FakeSupabaseClient que aceita kwarg `returning`."""
        from tests._helpers.fake_supabase import FakeSupabaseClient as _FSC

        class ExtendedFake(_FSC):
            def update(self, table, values, *, filters=None, returning=None):
                return super().update(table, values, filters=filters)

        tables = fake_db.tables
        ext = ExtendedFake(tables)
        monkeypatch.setattr(
            "app.services.drive_image_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: ext),
        )
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: True)
        return ext, tables

    def test_atualiza_quando_dominio_habilitado(self, monkeypatch, fake_db):
        """Deve fazer UPDATE na enquete quando domínio está habilitado."""
        ext, tables = self._make_db_accepting_returning(monkeypatch, fake_db)

        from app.services.drive_image_service import _update_enquete_drive_ids

        _update_enquete_drive_ids("POLL_001", "file-99", "folder-99", "msg-img-1")

        enquete = tables["enquetes"][0]
        assert enquete["drive_file_id"] == "file-99"
        assert enquete["drive_folder_id"] == "folder-99"
        assert enquete["image_message_id"] == "msg-img-1"

    def test_sem_image_message_id_nao_inclui_campo(self, monkeypatch, fake_db):
        """Sem image_message_id, payload não deve incluir o campo."""
        ext, tables = self._make_db_accepting_returning(monkeypatch, fake_db)

        from app.services.drive_image_service import _update_enquete_drive_ids

        _update_enquete_drive_ids("POLL_001", "file-77", "folder-77")

        enquete = tables["enquetes"][0]
        assert enquete["drive_file_id"] == "file-77"
        assert "image_message_id" not in enquete

    def test_skipa_quando_dominio_desabilitado(self, monkeypatch, fake_db):
        """Domínio desabilitado → nenhuma atualização no banco."""
        import app.services.supabase_service as ss
        monkeypatch.setattr(ss, "supabase_domain_enabled", lambda: False)

        from app.services.drive_image_service import _update_enquete_drive_ids

        _update_enquete_drive_ids("POLL_001", "file-x", "folder-x", "msg-x")

        enquete = fake_db.tables["enquetes"][0]
        assert enquete["drive_file_id"] is None  # não foi tocado


# ---------------------------------------------------------------------------
# Testes do wrapper assíncrono attach_poll_image
# ---------------------------------------------------------------------------

class TestAttachPollImageAsync:

    def test_retorna_file_id_em_sucesso(self, monkeypatch, fake_db):
        """attach_poll_image deve retornar o file_id em caso de sucesso."""
        monkeypatch.setattr(
            "app.services.drive_image_service.find_recent_image",
            lambda chat_id, poll_ts: {
                "media_id": "m-async",
                "message_id": "msg-async",
                "timestamp": 1_700_000_000,
            },
        )
        monkeypatch.setenv("WHAPI_TOKEN", "tok-test")
        fake_whapi = FakeWHAPIClient(media_bytes=b"ASYNCDATA")
        _patch_all(monkeypatch, fake_db, fake_whapi)

        from app.services.drive_image_service import attach_poll_image

        result = asyncio.run(
            attach_poll_image("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        )
        assert result == "file_msg-async.jpg"

    def test_retorna_none_quando_sync_levanta_excecao(self, monkeypatch):
        """Exceções em _attach_poll_image_sync devem ser capturadas e retornar None."""
        monkeypatch.setattr(
            "app.services.drive_image_service._attach_poll_image_sync",
            lambda *a: (_ for _ in ()).throw(RuntimeError("internal error")),
        )

        from app.services.drive_image_service import attach_poll_image

        result = asyncio.run(
            attach_poll_image("POLL_001", "120363@g.us", 1_700_000_100, "prod-1")
        )
        assert result is None
