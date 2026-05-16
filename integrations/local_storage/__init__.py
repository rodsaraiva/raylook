"""Storage local de imagens — substitui Google Drive em sandbox.

Mesma interface pública do GoogleDriveClient (8 métodos), com bytes
em data/images/<parent_id>/<file_id>.<ext> e metadados na tabela
drive_files (via SQLiteRestClient/SupabaseRestClient — vai pro Postgres
dedicado em prod, SQLite local em dev, transparente).

Nada aqui faz chamada de rede pra Google. Bytes ficam em filesystem
(volume Docker em prod), metadata vai pro banco escolhido por DATA_BACKEND.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("raylook.integrations.local_storage")

ROOT_FOLDER_ID = "raylook-root"
IMAGES_DIR = Path(os.environ.get("DATA_DIR", "data")) / "images"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _ext_from_name(name: str, mime_type: str) -> str:
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    guess = mimetypes.guess_extension(mime_type or "") or ".bin"
    return guess.lstrip(".").lower()


class LocalImageStorage:
    """Drop-in pro GoogleDriveClient. Persistência: bytes em filesystem,
    metadata na tabela drive_files via client abstrato (postgres ou sqlite)."""

    DEFAULT_FOLDER_COLOR = "#7e57c2"  # mantido por compatibilidade

    def __init__(self, parent_folder_id: Optional[str] = None):
        self.parent_folder_id = parent_folder_id or ROOT_FOLDER_ID
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def _client(self):
        # Lazy: evita ciclo de import e respeita DATA_BACKEND no momento da chamada.
        from app.services.supabase_service import SupabaseRestClient
        return SupabaseRestClient.from_settings()

    # -------- API pública (espelha GoogleDriveClient) --------

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        parent = parent_id or self.parent_folder_id
        existing = self.find_latest_folder_id_by_name(name, parent_id=parent)
        if existing:
            logger.info("local-storage: reusing folder '%s' id=%s", name, existing)
            return existing

        fid = _new_id()
        now = _now_iso()
        self._client.insert("drive_files", {
            "id": fid,
            "parent_id": parent,
            "name": name,
            "mime_type": "application/vnd.google-apps.folder",
            "is_folder": 1,
            "deleted": 0,
            "created_at": now,
            "updated_at": now,
        })
        logger.info("local-storage: created folder '%s' id=%s parent=%s", name, fid, parent)
        return fid

    def list_folders_by_name(
        self,
        name: str,
        parent_id: Optional[str] = None,
        *,
        page_size: int = 50,
    ) -> List[Dict[str, str]]:
        parent = parent_id or self.parent_folder_id
        rows = self._client.select(
            "drive_files",
            columns="id,name,created_at",
            filters=[
                ("deleted", "eq", 0),
                ("is_folder", "eq", 1),
                ("name", "eq", name),
                ("parent_id", "eq", parent),
            ],
            order="created_at.desc",
            limit=page_size,
        ) or []
        return [{"id": r["id"], "name": r["name"], "createdTime": r["created_at"]} for r in rows]

    def find_latest_folder_id_by_name(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> Optional[str]:
        folders = self.list_folders_by_name(name, parent_id=parent_id, page_size=1)
        return folders[0]["id"] if folders else None

    def find_latest_file_id_in_folder(self, folder_id: str) -> Optional[str]:
        rows = self._client.select(
            "drive_files",
            columns="id",
            filters=[
                ("deleted", "eq", 0),
                ("is_folder", "eq", 0),
                ("parent_id", "eq", folder_id),
                ("mime_type", "like", "image/*"),
            ],
            order="created_at.desc",
            limit=1,
        ) or []
        return rows[0]["id"] if rows else None

    def find_latest_folder_and_file_by_name(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        folders = self.list_folders_by_name(name, parent_id=parent_id)
        if not folders:
            return None, None
        for folder in folders:
            fid = folder["id"]
            file_id = self.find_latest_file_id_in_folder(fid)
            if file_id:
                return fid, file_id
        return folders[0]["id"], None

    def upload_file(
        self,
        name: str,
        content_bytes: bytes,
        parent_folder_id: Optional[str] = None,
        mime_type: str = "image/jpeg",
    ) -> str:
        parent_folder_id = parent_folder_id or self.parent_folder_id
        ext = _ext_from_name(name, mime_type)

        # Dedup: o mesmo (parent, name) chega N vezes via reprocessamento de
        # webhook/backfill. Reusar o id existente evita explodir o disco
        # (sem isso, 1 imagem virou 17k cópias com UUIDs distintos).
        existing = self._client.select(
            "drive_files",
            columns="id",
            filters=[
                ("parent_id", "eq", parent_folder_id),
                ("name", "eq", name),
                ("deleted", "eq", 0),
                ("is_folder", "eq", 0),
            ],
            limit=1,
        ) or []
        if existing:
            existing_id = existing[0]["id"]
            logger.info("local-storage: reusing file '%s' id=%s folder=%s",
                        name, existing_id, parent_folder_id)
            return existing_id

        file_id = _new_id()
        folder_dir = IMAGES_DIR / parent_folder_id
        folder_dir.mkdir(parents=True, exist_ok=True)
        path = folder_dir / f"{file_id}.{ext}"
        path.write_bytes(content_bytes)

        now = _now_iso()
        self._client.insert("drive_files", {
            "id": file_id,
            "parent_id": parent_folder_id,
            "name": name,
            "mime_type": mime_type,
            "ext": ext,
            "is_folder": 0,
            "deleted": 0,
            "created_at": now,
            "updated_at": now,
        })
        logger.info("local-storage: uploaded '%s' id=%s folder=%s bytes=%d",
                    name, file_id, parent_folder_id, len(content_bytes))
        return file_id

    def get_public_url(self, file_id: str) -> str:
        return f"/files/{file_id}"

    def delete_file(self, file_id: str) -> bool:
        rows = self._client.select(
            "drive_files",
            columns="parent_id,ext,is_folder",
            filters=[("id", "eq", file_id), ("deleted", "eq", 0)],
            limit=1,
        ) or []
        if not rows:
            return False
        row = rows[0]
        self._client.update(
            "drive_files",
            {"deleted": 1, "updated_at": _now_iso()},
            filters=[("id", "eq", file_id)],
        )
        if not row.get("is_folder"):
            try:
                (IMAGES_DIR / row["parent_id"] / f"{file_id}.{row['ext']}").unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("local-storage: delete bytes falhou id=%s: %s", file_id, exc)
        logger.info("local-storage: deleted id=%s", file_id)
        return True

    def list_all_folders(
        self,
        parent_id: Optional[str] = None,
        *,
        page_size: int = 1000,
    ) -> List[Dict[str, str]]:
        parent = parent_id or self.parent_folder_id
        rows = self._client.select(
            "drive_files",
            columns="id,name,created_at",
            filters=[
                ("deleted", "eq", 0),
                ("is_folder", "eq", 1),
                ("parent_id", "eq", parent),
            ],
            order="created_at.desc",
            limit=page_size,
        ) or []
        return [{"id": r["id"], "name": r["name"], "createdTime": r["created_at"]} for r in rows]

    # -------- API interna usada pela rota /files/<id> --------

    def resolve_file_path(self, file_id: str) -> Optional[Tuple[Path, str]]:
        """Retorna (path no disco, mime_type) pra um file_id, ou None se não existe."""
        rows = self._client.select(
            "drive_files",
            columns="parent_id,ext,mime_type,is_folder",
            filters=[("id", "eq", file_id), ("deleted", "eq", 0)],
            limit=1,
        ) or []
        if not rows:
            return None
        row = rows[0]
        if row.get("is_folder"):
            return None
        path = IMAGES_DIR / row["parent_id"] / f"{file_id}.{row['ext']}"
        if not path.exists():
            return None
        return path, row["mime_type"]
