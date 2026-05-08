"""Storage local de imagens — substitui Google Drive em sandbox.

Mesma interface pública do GoogleDriveClient (8 métodos), com bytes
em data/images/<parent_id>/<file_id>.<ext> e metadados na tabela
drive_files do raylook.db.

Nada aqui faz chamada de rede. Rodando 100% offline.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("raylook.integrations.local_storage")

ROOT_FOLDER_ID = "raylook-root"
IMAGES_DIR = Path("data/images")


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
    """Drop-in pro GoogleDriveClient. Persistência em data/images + SQLite."""

    _init_lock = threading.Lock()
    DEFAULT_FOLDER_COLOR = "#7e57c2"  # mantido por compatibilidade

    def __init__(self, parent_folder_id: Optional[str] = None):
        self.parent_folder_id = parent_folder_id or ROOT_FOLDER_ID
        self._ensure_table()
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # -------- infra --------

    @staticmethod
    def _db_path() -> str:
        from app.services.sqlite_service import _default_db_path
        return _default_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path(), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_table(self) -> None:
        with self._init_lock:
            conn = self._connect()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS drive_files (
                        id TEXT PRIMARY KEY,
                        parent_id TEXT,
                        name TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        ext TEXT,
                        is_folder INTEGER NOT NULL DEFAULT 0,
                        deleted INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_drive_files_parent ON drive_files (parent_id, deleted);
                    CREATE INDEX IF NOT EXISTS idx_drive_files_name ON drive_files (name, deleted);
                    CREATE INDEX IF NOT EXISTS idx_drive_files_created ON drive_files (created_at DESC);
                """)
            finally:
                conn.close()

    # -------- API pública (espelha GoogleDriveClient) --------

    def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        parent = parent_id or self.parent_folder_id
        existing = self.find_latest_folder_id_by_name(name, parent_id=parent)
        if existing:
            logger.info("local-storage: reusing folder '%s' id=%s", name, existing)
            return existing

        fid = _new_id()
        now = _now_iso()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO drive_files (id, parent_id, name, mime_type, is_folder, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?)",
                (fid, parent, name, "application/vnd.google-apps.folder", now, now),
            )
        finally:
            conn.close()
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
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, created_at FROM drive_files "
                "WHERE deleted=0 AND is_folder=1 AND name=? AND parent_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (name, parent, page_size),
            ).fetchall()
        finally:
            conn.close()
        return [{"id": r["id"], "name": r["name"], "createdTime": r["created_at"]} for r in rows]

    def find_latest_folder_id_by_name(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> Optional[str]:
        folders = self.list_folders_by_name(name, parent_id=parent_id, page_size=1)
        return folders[0]["id"] if folders else None

    def find_latest_file_id_in_folder(self, folder_id: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM drive_files "
                "WHERE deleted=0 AND is_folder=0 AND parent_id=? AND mime_type LIKE 'image/%' "
                "ORDER BY created_at DESC LIMIT 1",
                (folder_id,),
            ).fetchone()
        finally:
            conn.close()
        return row["id"] if row else None

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
        file_id = _new_id()
        folder_dir = IMAGES_DIR / parent_folder_id
        folder_dir.mkdir(parents=True, exist_ok=True)
        path = folder_dir / f"{file_id}.{ext}"
        path.write_bytes(content_bytes)

        now = _now_iso()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO drive_files (id, parent_id, name, mime_type, ext, is_folder, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (file_id, parent_folder_id, name, mime_type, ext, now, now),
            )
        finally:
            conn.close()
        logger.info("local-storage: uploaded '%s' id=%s folder=%s bytes=%d", name, file_id, parent_folder_id, len(content_bytes))
        return file_id

    def get_public_url(self, file_id: str) -> str:
        # URL relativa — o browser monta com o host atual.
        return f"/files/{file_id}"

    def delete_file(self, file_id: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT parent_id, ext, is_folder FROM drive_files WHERE id=? AND deleted=0",
                (file_id,),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE drive_files SET deleted=1, updated_at=? WHERE id=?",
                (_now_iso(), file_id),
            )
        finally:
            conn.close()
        if not row["is_folder"]:
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
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, name, created_at FROM drive_files "
                "WHERE deleted=0 AND is_folder=1 AND parent_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (parent, page_size),
            ).fetchall()
        finally:
            conn.close()
        return [{"id": r["id"], "name": r["name"], "createdTime": r["created_at"]} for r in rows]

    # -------- API interna usada pela rota /files/<id> --------

    def resolve_file_path(self, file_id: str) -> Optional[Tuple[Path, str]]:
        """Retorna (path no disco, mime_type) pra um file_id, ou None se não existe."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT parent_id, ext, mime_type, is_folder FROM drive_files WHERE id=? AND deleted=0",
                (file_id,),
            ).fetchone()
        finally:
            conn.close()
        if not row or row["is_folder"]:
            return None
        path = IMAGES_DIR / row["parent_id"] / f"{file_id}.{row['ext']}"
        if not path.exists():
            return None
        return path, row["mime_type"]
