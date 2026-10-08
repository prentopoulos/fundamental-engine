"""Record and replay model responses for identical requests.

The key includes the model, prompts, generation settings, and input content.
Replaying a response avoids a new API call; it does not measure model stability.
Cache files are local working data and are never included in the public repository.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path("build/model_cache")


def cache_key(payload: dict[str, Any]) -> str:
    """A stable hash over the full request.

    `sort_keys` and a fixed separator make the encoding canonical, so two logically equal
    requests cannot hash differently because a dict happened to be built in another order.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_stringify)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stringify(value: Any) -> str:
    """Bytes are hashed by content; anything else falls back to its repr."""
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return repr(value)


@dataclass(frozen=True)
class CacheStats:
    hits: int
    misses: int

    @property
    def total(self) -> int:
        return self.hits + self.misses

    def describe(self) -> str:
        if self.total == 0:
            return "cache: no calls"
        return f"cache: {self.hits} replayed, {self.misses} billed ({self.hits}/{self.total})"


class ReplayCache:
    """Reads and writes model responses on disk, one JSON file per input hash."""

    def __init__(self, directory: Path = DEFAULT_CACHE_DIR) -> None:
        self.directory = directory
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> CacheStats:
        return CacheStats(hits=self._hits, misses=self._misses)

    def path_for(self, key: str) -> Path:
        # Sharded by prefix: a flat directory of thousands of files is slow to list on
        # Windows and unpleasant to inspect by hand.
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.path_for(key)
        if not path.exists():
            self._misses += 1
            return None
        self._hits += 1
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def put(
        self,
        key: str,
        response: dict[str, Any],
        note: str | None = None,
        *,
        request: dict[str, Any] | None = None,
    ) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "note": note, "request": request, "response": response}
        path.write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )

    def entries(self) -> int:
        return sum(1 for _ in self.directory.rglob("*.json")) if self.directory.exists() else 0
