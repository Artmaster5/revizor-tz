"""Common model-layer interface: usage accounting, errors, retries, JSON checking, cache.

Every provider implements one method, ``_raw(system, user, max_tokens) -> (text, Usage)``.
Everything else (disk cache, transient retries, loose JSON parsing, schema validation,
one repair round-trip, thread-safe counters) lives here, so all providers behave alike.
"""
from __future__ import annotations

import email.utils
import inspect
import random
import re
import threading
import time
from dataclasses import dataclass, fields
from typing import Any, Callable

from .schema import parse_json_loose, salvage_items, validate, validate_ex

# --- usage -------------------------------------------------------------------


@dataclass
class Usage:
    input_tokens: int = 0  # uncached input tokens
    output_tokens: int = 0  # includes thinking tokens where the provider reports them
    cache_read: int = 0
    cache_write: int = 0
    seconds: float = 0.0
    from_cache: bool = False
    calls: int = 0  # provider requests behind these numbers (filled by Backend accounting)

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.seconds += other.seconds

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


# --- secrets -------------------------------------------------------------------

_SECRETS: set[str] = set()
_SECRETS_LOCK = threading.Lock()
_KEY_PATTERNS = [
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\bAQ\.[0-9A-Za-z_\-.]{20,}"),  # newer Google AI Studio keys
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)((?:api[_-]?key|x-goog-api-key|x-api-key|access[_-]?token|token|key)[\"']?\s*[=:]\s*[\"']?)[A-Za-z0-9._\-]{16,}"),
]


def register_secret(value: str | None) -> None:
    """Remember a key so that it is masked in every error text."""
    if value and len(value.strip()) >= 6:
        with _SECRETS_LOCK:
            _SECRETS.add(value.strip())


def redact(text: Any) -> str:
    """Mask registered keys and anything that looks like a key."""
    s = "" if text is None else str(text)
    with _SECRETS_LOCK:
        secrets = sorted(_SECRETS, key=len, reverse=True)
    for sec in secrets:
        if sec in s:
            s = s.replace(sec, "***")
    for rx in _KEY_PATTERNS:
        s = rx.sub(lambda m: (m.group(1) if m.groups() else "") + "***", s)
    return s


# --- errors -----------------------------------------------------------------


class LLMError(Exception):
    """Base error. ``user_message`` is Russian, safe to print, never contains a key."""

    def __init__(self, user_message: str, *, detail: str = "", retry_after: float | None = None,
                 kind: str = "") -> None:
        msg = redact(user_message)
        super().__init__(msg)
        self.user_message = msg
        self.detail = redact(detail)[:2000]
        self.retry_after = retry_after
        self.kind = kind  # "network" | "timeout" | "rate" | "server" | ""
        self.usage: Usage | None = None  # tokens spent by the failed call, if known
        self.limit: int | None = None  # token limit that was hit (kind == "truncated")
        self.final = False  # True: the backend already retried this itself, the base loop must not repeat it

    def __str__(self) -> str:
        return self.user_message


class LLMUnavailable(LLMError):
    """No key / auth / model not found / package missing / no network after retries."""


class LLMTransient(LLMError):
    """Rate limit, 5xx, timeout: raised only after the retries are exhausted."""


class LLMBadOutput(LLMError):
    """Not JSON or schema mismatch after the repair retry; also a cut-off answer."""


class LLMRefused(LLMError):
    """The model declined to answer."""


# --- helpers for providers -----------------------------------------------------


def parse_retry_after(headers: Any) -> float | None:
    """Seconds from ``retry-after-ms`` / ``retry-after`` (seconds or HTTP date)."""
    if headers is None:
        return None
    try:
        get = headers.get
    except AttributeError:
        return None
    ms = get("retry-after-ms")
    if ms:
        try:
            return max(0.0, float(ms) / 1000.0)
        except ValueError:
            pass
    ra = get("retry-after")
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(ra)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def build_repair_prompt(user: str, bad_answer: str, errors: list[str]) -> str:
    shown = bad_answer if len(bad_answer) <= 12000 else bad_answer[:12000] + "\n…(ответ обрезан)…"
    err_lines = "\n".join("- " + e for e in errors[:20])
    return (
        user
        + "\n\n---\nТвой предыдущий ответ не прошёл автоматическую проверку формы.\n"
        + "Ошибки:\n" + err_lines + "\n\n"
        + "Предыдущий ответ:\n<<<\n" + shown + "\n>>>\n\n"
        + "Верни только исправленный JSON — один объект в запрошенной форме, без пояснений до и после."
    )


def _short_errors(errors: list[str], n: int = 3) -> str:
    s = "; ".join(errors[:n])
    if len(errors) > n:
        s += "; и ещё %d" % (len(errors) - n)
    return s


# --- effort per stage -------------------------------------------------------

EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
# Defaults used when ANTHROPIC_EFFORT is not set explicitly. Thinking is most of the output bill:
# mechanical stages get "low"; only the search for contradictions between sections keeps "high".
# Requirement review runs at "low": measured on 25.11.2026, the three lenses found the same defects as at
# "medium" with 29 % fewer output tokens.
STAGE_EFFORT: dict[str, str] = {
    "slice": "low", "slice_rest": "low",
    "review": "low", "review_clarity": "low", "review_completeness": "low",
    "review_correctness": "low",
    "set_consistency": "high", "set_completeness": "medium", "set_standards": "medium",
    "set_gaps": "medium",
    "skeptic": "medium", "dedup": "medium", "questions": "low", "answers": "medium", "tests": "low",
    "checklist": "low",
}
STAGE_EFFORT_NOTE = ("Усилие модели задано по этапам: поиск противоречий между разделами — high, остальные проверки "
                     "набора, перепроверка и разбор ответов — medium, проверка требований, нарезка, вопросы и черновики "
                     "тестов — low, ответы на чек-лист из документа — тоже low. "
                     "Задать одно усилие для всех этапов: ANTHROPIC_EFFORT.")
MAX_RETRY_TOKENS = 64000
# One step down for a retry after a cut-off answer: thinking is billed as output and is what ate the limit.
LOWER_EFFORT = {"max": "high", "xhigh": "high", "high": "medium", "medium": "low"}


def parse_stage_overrides(text: str) -> tuple[dict[str, str], list[str]]:
    """``review=low,set_consistency=high`` -> ({...}, [Russian problems]). Bad pairs are skipped."""
    out: dict[str, str] = {}
    bad: list[str] = []
    for part in re.split(r"[,;\s]+", (text or "").strip()):
        if not part:
            continue
        name, sep, val = part.partition("=")
        name, val = name.strip().lower(), val.strip().lower()
        if not sep or not name or val not in EFFORT_VALUES:
            bad.append(part[:40])
            continue
        out[name] = val
    return out, bad


# A stage without an entry of its own takes the effort of another one: the dedup call of stage 7 works like the re-check,
# so ``ANTHROPIC_EFFORT_STAGES=skeptic=high`` raises both (``dedup=…`` still sets it alone).
STAGE_EFFORT_ALIAS = {"dedup": "skeptic"}


def _stage_lookup(table: dict[str, str], stage: str) -> str:
    if not stage:
        return ""
    if stage in table:
        return table[stage]
    if stage in STAGE_EFFORT_ALIAS and STAGE_EFFORT_ALIAS[stage] in table:
        return table[STAGE_EFFORT_ALIAS[stage]]
    return table.get(stage.split("_", 1)[0], "")


# --- backend -----------------------------------------------------------------


class Backend:
    provider: str = "base"
    # Requirements per review request (one request per batch and lens). None = prompts.BATCH_SIZE (12).
    # A provider with a large context sets more (fewer requests); LLM_BATCH_SIZE overrides it for every provider.
    review_batch_size: int | None = None
    # «Requests are scarce» (free tiers that count requests per day): the pipeline then sends the most valuable
    # steps first, slices without the model and repeats steps that got no answer once. LLM_ECONOMY overrides it.
    economy: bool = False
    # What the backend is waiting for right now («Gemini перегружен (503): жду 25 с, попытка 3»), or "".
    # The pipeline's heartbeat line shows it; set only by backends that pause on their own.
    wait_status: str = ""

    def __init__(self, *, model: str = "", effort: str = "", concurrency: int = 1, cache: Any = None,
                 use_cache: bool = True, max_attempts: int = 4,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.model = model
        self.effort = effort
        self.concurrency = max(1, int(concurrency))
        self.notes: list[str] = []
        self.calls = 0  # requests sent to the provider (retries and repairs included)
        self.cache_hits = 0
        self.retries = 0
        self.totals = Usage()
        self.cache = cache  # DiskCache or None
        self.use_cache = use_cache
        self.max_attempts = max(1, int(max_attempts))
        self.max_retry_wait = 120.0  # a longer server-requested wait is not worth it
        self.sleep: Callable[[float], None] = sleep or time.sleep
        self.jitter: Callable[[], float] = random.random
        self._lock = threading.RLock()
        self._tls = threading.local()
        self.stage_usage: dict[str, Usage] = {}
        self.effort_locked = True  # True: self.effort for every stage; subclasses may unlock
        self.stage_effort: dict[str, str] = dict(STAGE_EFFORT)
        self.stage_overrides: dict[str, str] = {}
        self._raw_takes_effort = "effort" in inspect.signature(self._raw).parameters

    # -- provider hook
    def _raw(self, system: str, user: str, max_tokens: int, *, effort: str | None = None) -> tuple[str, Usage]:
        raise NotImplementedError

    # -- effort per stage
    def effort_for(self, stage: str) -> str:
        """Effective effort for a stage: locked value, else override, else default map, else base."""
        if self.effort_locked or not self.effort:
            return self.effort
        return (_stage_lookup(self.stage_overrides, stage) or _stage_lookup(self.stage_effort, stage)
                or self.effort)

    @property
    def effort_label(self) -> str:
        """Short text for the passport: one value, or «по этапам»."""
        if self.effort_locked or not self.effort:
            return self.effort
        return "по этапам"

    def effort_applies(self) -> bool:
        """True when this backend sends the effort to the provider (a lower effort can then shorten thinking)."""
        return self._raw_takes_effort

    # -- economy mode hooks (no-ops unless a backend overrides them)
    def set_round(self, n: int) -> None:
        """The pipeline's round of review/set jobs in economy mode: 1 = first pass, 2 = repeat of the steps that
        got no answer, 0 = outside the rounds. A backend with its own waiting budget may share it by round."""

    def can_answer(self) -> bool:
        """False when the backend knows it cannot answer any more in this run (e.g. every model out of quota)."""
        return True

    # -- small helpers
    @property
    def current_stage(self) -> str:
        return getattr(self._tls, "stage", "")

    def add_note(self, text: str) -> None:
        with self._lock:
            if text not in self.notes:
                self.notes.append(text)

    def _account(self, usage: Usage | None) -> None:
        if usage is None:
            return
        stage = self.current_stage or "?"
        with self._lock:
            self.totals.add(usage)
            st = self.stage_usage.get(stage)
            if st is None:
                st = self.stage_usage[stage] = Usage()
            st.add(usage)
            st.calls += 1

    def _delay(self, attempt: int, retry_after: float | None) -> float | None:
        if retry_after is not None:
            if retry_after > self.max_retry_wait:
                return None
            return max(0.5, retry_after) + 0.5 * self.jitter()
        return min(30.0, 2.0 * (2 ** (attempt - 1))) + self.jitter()

    def _call_raw(self, system: str, user: str, max_tokens: int, effort: str = "") -> tuple[str, Usage]:
        last: LLMTransient | None = None
        attempts = 0
        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            with self._lock:
                self.calls += 1
            try:
                if self._raw_takes_effort:
                    text, usage = self._raw(system, user, max_tokens, effort=effort or None)
                else:
                    text, usage = self._raw(system, user, max_tokens)
            except LLMTransient as e:
                self._account(e.usage)
                if e.final:
                    raise
                last = e
                if attempt >= self.max_attempts:
                    break
                delay = self._delay(attempt, e.retry_after)
                if delay is None:
                    break
                with self._lock:
                    self.retries += 1
                self.sleep(delay)
                continue
            except LLMError as e:
                self._account(e.usage)
                raise
            self._account(usage)
            return text, usage
        assert last is not None
        tail = " Попыток: %d." % attempts
        if last.kind == "network":
            raise LLMUnavailable(last.user_message + tail + " Проверьте подключение к интернету.",
                                 detail=last.detail, kind="network")
        raise LLMTransient(last.user_message + tail, detail=last.detail, retry_after=last.retry_after,
                           kind=last.kind)

    def _check(self, text: str, schema: dict | None) -> tuple[Any, list[str]]:
        try:
            data = parse_json_loose(text)
        except ValueError as e:
            return None, ["ответ не является JSON (%s)" % e]
        if schema:
            data, errors, notes = validate_ex(data, schema)
            self._tls.dropped = list(getattr(self._tls, "dropped", [])) + notes
            if errors:
                return data, errors
        if not isinstance(data, dict):
            return data, ["$: ожидался объект JSON {…}, получено %s" % type(data).__name__]
        return data, []

    def _salvage(self, text2: str, text: str, schema: dict | None, stage: str) -> dict | None:
        """Both answers broke the form: keep every whole item of the better one (``salvage_items``) instead of
        losing the whole batch. None when nothing whole is left. The skipped items are named in the notes."""
        best = None
        for t in (text2, text):
            got = salvage_items(t, schema)
            if got is None:
                continue
            data, errors, notes = validate_ex(got[0], schema)
            if errors:
                continue
            key = next(iter(got[0]))
            n = len(data.get(key) or [])
            if best is None or n > best[1]:
                best = (data, n, got[1], notes)
        if best is None:
            return None
        data, n, broken, notes = best
        self._tls.dropped = list(getattr(self._tls, "dropped", [])) + notes
        self.add_note("Ответ модели на этапе «%s» дважды пришёл с испорченным JSON: взято %d целых записей%s."
                      % (stage or "?", n, ", испорченных пропущено: %d" % broken if broken else ""))
        return data

    @property
    def cache_model(self) -> str:
        """Model name in the answer-cache key. A backend that may change models during a run returns the
        CONFIGURED name here, so a second launch of the same command finds every stored answer."""
        return self.model

    def take_dropped(self) -> list[dict[str, str]]:
        """Items dropped from the last answer of THIS thread ({"path", "reason"}); reading clears them."""
        got = list(getattr(self._tls, "dropped", []))
        self._tls.dropped = []
        return got

    # -- main entry
    def complete_json(self, system: str, user: str, *, schema: dict | None = None,
                      max_tokens: int = 16000, stage: str = "") -> tuple[dict, Usage]:
        t0 = time.monotonic()
        self._tls.stage = stage
        self._tls.dropped = []
        effort = self.effort_for(stage)
        cache = self.cache
        if cache is not None and self.use_cache:
            key = cache.key(self.provider, self.cache_model, effort, system, user)
            hit = cache.get(key)
            if hit is not None:
                data, errors = (validate(hit, schema) if schema else (hit, []))
                if not errors and isinstance(data, dict):
                    with self._lock:
                        self.cache_hits += 1
                    return data, Usage(seconds=time.monotonic() - t0, from_cache=True)

        total = Usage()
        salvaged = False  # a partial answer rescued item by item is used but not cached: a rerun asks again
        sent_effort = effort  # what goes to the provider; the cache key keeps the REQUESTED effort
        try:
            text, usage = self._call_raw(system, user, max_tokens, effort)
        except LLMBadOutput as e:
            if e.kind != "truncated" or getattr(self, "max_tokens_override", None):
                raise
            # Cut off by the token limit: a "repair" with the same budget would be cut again. Exactly one retry.
            # Thinking is billed as output and is what ate the limit, so lower the effort one step (limit kept or
            # raised to the ceiling); when the effort cannot be lowered, double the limit up to the ceiling.
            # Usage of the failed attempt is kept.
            used = e.limit or max_tokens
            lower = LOWER_EFFORT.get(effort, "") if self.effort_applies() else ""
            if lower:
                retry_limit = max(used, max_tokens, MAX_RETRY_TOKENS)
                note = ("Ответ модели на этапе «%s» был обрезан по лимиту токенов (лимит %d, усилие %s) — запрос "
                        "повторён один раз с лимитом %d и усилием %s." % (stage or "?", used, effort, retry_limit, lower))
            else:
                retry_limit = min(MAX_RETRY_TOKENS, max(used, max_tokens) * 2)
                if retry_limit <= used:
                    raise
                note = ("Ответ модели на этапе «%s» был обрезан по лимиту токенов — запрос повторён с лимитом "
                        "%d вместо %d." % (stage or "?", retry_limit, used)
                        + (" Усилие %s ниже не опустить." % effort if effort and self.effort_applies() else ""))
            if e.usage is not None:
                total.add(e.usage)
            self.add_note(note)
            sent_effort = lower or effort
            text, usage = self._call_raw(system, user, retry_limit, sent_effort)
            max_tokens = retry_limit
        total.add(usage)
        data, errors = self._check(text, schema)
        if errors:
            repair_user = build_repair_prompt(user, text, errors)
            text2, usage2 = self._call_raw(system, repair_user, max_tokens, sent_effort)
            total.add(usage2)
            self._tls.dropped = []
            data, errors2 = self._check(text2, schema)
            if errors2:
                data = self._salvage(text2, text, schema, stage)
                salvaged = data is not None
            if errors2 and data is None:
                raise LLMBadOutput(
                    "Модель дважды вернула ответ не в нужной форме (этап «%s»): %s"
                    % (stage or "?", _short_errors(errors2)),
                    detail="первая попытка: %s | ответ после исправления: %s"
                    % (_short_errors(errors, 5), text2[:600]),
                )
        total.seconds = time.monotonic() - t0
        if cache is not None and not salvaged:
            # Key recomputed: the model may have been substituted during the call. The key uses the REQUESTED
            # effort even after a lower-effort retry, so the next identical run finds this answer and pays nothing.
            key = cache.key(self.provider, self.cache_model, effort, system, user)
            if not cache.put(key, data, {"provider": self.provider, "model": self.model,
                                         "effort": effort, "effort_sent": sent_effort, "stage": stage,
                                         "usage": total.to_dict()}):
                self.add_note("Кэш ответов модели не записан: %s" % (cache.last_error or "ошибка записи"))
        return data, total
