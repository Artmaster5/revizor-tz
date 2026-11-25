"""Disk cache of validated model answers: ``<dir>/<sha256>.json``.

Key = sha256 over provider, model, effort, system and user text. Writes are atomic
(temporary file in the same folder + ``os.replace``), so parallel threads and an interrupted
run never leave a half-written entry. Only successful, validated answers are stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.dir = Path(directory)
        self.last_error = ""

    @staticmethod
    def key(provider: str, model: str, effort: str, system: str, user: str) -> str:
        blob = json.dumps([1, provider, model, effort, system, user], ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def path(self, key: str) -> Path:
        return self.dir / (key + ".json")

    def get(self, key: str) -> Any:
        """The cached data or None (missing or unreadable entries count as a miss)."""
        try:
            with open(self.path(key), "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or "data" not in payload:
            return None
        return payload["data"]

    def put(self, key: str, data: Any, meta: dict[str, Any] | None = None) -> bool:
        payload = dict(meta or {})
        payload["created"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        payload["data"] = data
        tmp = ""
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="." + key[:12] + ".", suffix=".tmp", dir=str(self.dir))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self.path(key))
            return True
        except (OSError, TypeError, ValueError) as e:
            self.last_error = "%s: %s" % (type(e).__name__, e)
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False
