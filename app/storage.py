from pathlib import Path
from typing import Any, Optional
import json
import os
import logging
import threading

from app.utils.fileio import atomic_write, safe_read_json

logger = logging.getLogger("raylook.storage")

class JsonFileStorage:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        # try to use portalocker for cross-process locking if available
        try:
            import portalocker  # type: ignore
            self._portalocker = portalocker
        except Exception:
            self._portalocker = None

    def load(self) -> Optional[Any]:
        if not self.path.exists():
            return None
        if self._portalocker:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    with self._portalocker.Lock(f, timeout=5):
                        return json.load(f)
            except Exception as e:
                logger.warning("JsonFileStorage.load failed with portalocker: %s", e)
                return safe_read_json(self.path)
        else:
            # fallback: read without inter-process lock
            return safe_read_json(self.path)

    def save(self, data: Any) -> None:
        # attempt cross-process lock if portalocker available
        if self._portalocker:
            try:
                # write to temp then replace while holding lock on the file path
                tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                # Acquire file lock on the destination before replacing
                with open(self.path, "a+", encoding="utf-8") as f:
                    try:
                        with self._portalocker.Lock(f, timeout=5):
                            tmp_path.replace(self.path)
                            return
                    except Exception:
                        # fallback to atomic write via helper
                        pass
            except Exception as e:
                logger.warning("JsonFileStorage.save with portalocker failed: %s", e)
        # fallback to in-process lock with atomic_write
        with self._lock:
            atomic_write(self.path, data)

