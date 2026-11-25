"""Scripted backend for tests: never used at runtime, never touches the network."""
from __future__ import annotations

import json
from typing import Any, Callable

from .base import Backend, LLMBadOutput, Usage
from .cache import DiskCache


class FakeBackend(Backend):
    """``script[stage]`` is one item or a list used as a queue (the last item repeats).

    An item is: dict/list → returned as the model's JSON text; str → returned verbatim;
    an Exception instance → raised; a callable(system, user) → its result treated the same way.
    The whole script may also be a callable(stage, system, user). Repair calls consume the same
    queue. ``.log`` keeps (stage, system, user) for every raw call.
    """

    provider = "fake"

    def __init__(self, script: dict[str, Any] | Callable[[str, str, str], Any], *, model: str = "fake-1",
                 concurrency: int = 4, cache_dir: str | None = None, use_cache: bool = True,
                 max_attempts: int = 4) -> None:
        super().__init__(model=model, effort="", concurrency=concurrency,
                         cache=DiskCache(cache_dir) if cache_dir else None, use_cache=use_cache,
                         max_attempts=max_attempts, sleep=lambda _s: None)
        self.script = script
        self.log: list[tuple[str, str, str]] = []
        self._pos: dict[str, int] = {}

    def _next(self, stage: str, system: str, user: str) -> Any:
        if callable(self.script) and not isinstance(self.script, dict):
            return self.script(stage, system, user)
        if stage not in self.script:
            raise LLMBadOutput("нет сценария для этапа «%s»" % stage)
        entry = self.script[stage]
        if isinstance(entry, list):
            if not entry:
                raise LLMBadOutput("пустой сценарий для этапа «%s»" % stage)
            with self._lock:
                i = self._pos.get(stage, 0)
                self._pos[stage] = i + 1
            return entry[min(i, len(entry) - 1)]
        return entry

    def _raw(self, system: str, user: str, max_tokens: int, *, effort: str | None = None) -> tuple[str, Usage]:
        stage = self.current_stage
        with self._lock:
            self.log.append((stage, system, user))
        item = self._next(stage, system, user)
        if callable(item) and not isinstance(item, BaseException):
            item = item(system, user)
        if isinstance(item, BaseException):
            raise item
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        usage = Usage(input_tokens=max(1, (len(system) + len(user)) // 4), output_tokens=max(1, len(text) // 4))
        return text, usage
