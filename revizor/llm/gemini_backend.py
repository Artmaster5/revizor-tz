"""Google Gemini through the REST API (stdlib urllib). The key goes in ``x-goog-api-key``.

Built for the free tier (AI Studio key), where the main model is often overloaded (503) or out of its
per-day quota (429). The backend then switches ONCE per run to a spare model (``GEMINI_FALLBACK_MODEL``,
default ``gemini-flash-lite-latest``), repeats the failed request there and stays there. A retired or
inaccessible model (404) is replaced by discovery from the model list (up to 3 candidates).
"""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

from ._http import HttpResult, error_message, request_json
from .base import (MAX_RETRY_TOKENS, Backend, LLMBadOutput, LLMRefused, LLMTransient, LLMUnavailable, Usage,
                   parse_retry_after, redact, register_secret)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL = "gemini-flash-latest"
FALLBACK_MODEL = "gemini-flash-lite-latest"
KEY_HELP = ("Получите бесплатный ключ: aistudio.google.com → Get API key, и вставьте его в .env "
            "как GEMINI_API_KEY=… Без ключа запустите агент с --provider offline.")
_EXCLUDE = ("lite", "image", "tts", "live", "embedding", "audio", "8b", "computer-use", "robotics", "learnlm")
_REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY"}
_OVERLOAD_WORDS = ("overloaded", "high demand", "unavailable", "try again later", "capacity")
# Overload (503 / «high demand») on the free tier: both models are often busy, but about one request in 5–6
# gets through. Per call: the failed model once more, the spare model twice, then two more rounds over both
# models after 8 s and 25 s. At most CALL_WAIT_CAP seconds of pauses per call and OVERLOAD_WAIT_S per run
# (GEMINI_OVERLOAD_WAIT_S); when the run's waiting is spent, every call makes one attempt per model.
# In the pipeline's economy rounds the run budget is shared: during the first round one call may spend at most
# FIRST_ROUND_SHARE of what is left (early steps cannot starve the later, more valuable ones); the repeat round
# of unanswered steps gets two more rounds of attempts and may use the rest.
SAME_MODEL_PAUSE = 2.0
ROUND_PAUSES = (8.0, 25.0)
RETRY_ROUND_PAUSES = (8.0, 25.0, 45.0, 60.0)
CALL_WAIT_CAP = 75.0
RETRY_CALL_WAIT_CAP = 150.0
FIRST_ROUND_SHARE = 1.0 / 6.0
OVERLOAD_WAIT_S = 600.0
MAX_DISCOVERY_CANDIDATES = 3
REVIEW_BATCH_SIZE = 40  # Gemini flash: 1M-token input, up to 65k output — fewer, larger requests
DEFAULT_BATCH = 12  # prompts.BATCH_SIZE; the review budgets in prompts.MAX_TOKENS are sized for it
MODEL_OUTPUT_CAP = 65536  # maxOutputTokens ceiling of the flash models
FIRST_ATTEMPT_CAP = 49152  # below MAX_RETRY_TOKENS, so a MAX_TOKENS cut can still be repeated with more room
DETAIL_LEN = 300


def _strip_models(name: str) -> str:
    return name[len("models/"):] if name.startswith("models/") else name


def rank_model(name: str) -> tuple | None:
    """Sort key for a replacement model (bigger is better) or None when unsuitable."""
    low = name.lower()
    if "flash" not in low or any(x in low for x in _EXCLUDE):
        return None
    alias = 1 if low == "gemini-flash-latest" else 0
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", low)
    ver = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    stable = 0 if any(x in low for x in ("preview", "exp")) else 1
    return (alias, ver, stable, -len(low))


def rank_models(models: list[dict]) -> list[str]:
    """Suitable replacement models, best first, without duplicates."""
    scored: dict[str, tuple] = {}
    for m in models:
        if not isinstance(m, dict):
            continue
        if "generateContent" not in (m.get("supportedGenerationMethods") or []):
            continue
        name = _strip_models(str(m.get("name", "")))
        k = rank_model(name)
        if k is not None:
            scored[name] = k
    return sorted(scored, key=lambda n: scored[n], reverse=True)


def pick_model(models: list[dict]) -> str | None:
    ranked = rank_models(models)
    return ranked[0] if ranked else None


# --- error bodies ------------------------------------------------------------------------------


def _details(res: HttpResult) -> list[dict]:
    err = res.data.get("error") if isinstance(res.data, dict) else None
    if isinstance(res.data, list) and res.data and isinstance(res.data[0], dict):
        err = res.data[0].get("error")
    details = (err.get("details") or []) if isinstance(err, dict) else []
    return [d for d in details if isinstance(d, dict)] if isinstance(details, list) else []


def _violations(res: HttpResult) -> list[dict]:
    out: list[dict] = []
    for d in _details(res):
        for v in d.get("violations") or []:
            if isinstance(v, dict):
                out.append(v)
    return out


def _retry_delay(res: HttpResult) -> float | None:
    ra = parse_retry_after(res.headers)
    if ra is not None:
        return ra
    for d in _details(res):
        if d.get("retryDelay"):
            m = re.match(r"^\s*([0-9.]+)\s*s?\s*$", str(d["retryDelay"]))
            if m:
                return float(m.group(1))
    return None


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]", "", s.lower())


def _daily_quota(res: HttpResult) -> bool:
    """429 that waiting a minute will not cure: a per-day quota is exhausted.

    Looks at ``quotaId`` / ``quotaMetric`` of the violations first, then at the text
    («PerDay», «per day», «daily», «RequestsPerDay»…)."""
    ids = [_norm(str(v.get("quotaId", "")) + " " + str(v.get("quotaMetric", ""))) for v in _violations(res)]
    ids = [i for i in ids if i.strip()]
    if ids:
        return any("perday" in i or "daily" in i for i in ids)
    t = _norm(res.text)
    day = "perday" in t or "daily" in t or "requestsperday" in t
    minute = "perminute" in t or "perminuteper" in t
    return day and not minute


def _zero_quota(res: HttpResult) -> bool:
    """429 with ``quotaValue: 0`` / «limit: 0»: the model has no free-tier quota at all."""
    for v in _violations(res):
        if str(v.get("quotaValue", "")).strip() == "0":
            return True
    return bool(re.search(r"limit:\s*0\b", res.text))


def _is_overload(res: HttpResult, low: str) -> bool:
    return res.status == 503 or any(w in low for w in _OVERLOAD_WORDS)


def provider_detail(res: HttpResult, msg: str) -> str:
    """A redacted piece (≤300 chars) of the provider's message with the quota name, value and model."""
    extra: list[str] = []
    for v in _violations(res)[:2]:
        bits = []
        if v.get("quotaId"):
            bits.append("quotaId=%s" % v["quotaId"])
        if v.get("quotaValue") not in (None, ""):
            bits.append("quotaValue=%s" % v["quotaValue"])
        dims = v.get("quotaDimensions") if isinstance(v.get("quotaDimensions"), dict) else {}
        if dims.get("model"):
            bits.append("model=%s" % dims["model"])
        if bits:
            extra.append(", ".join(bits))
    # the quota name and value go first: the provider's message is long and would push them past the cut
    s = "HTTP %d%s: %s" % (res.status, (" [" + "; ".join(extra) + "]") if extra else "", " ".join(msg.split()))
    return redact(s)[:DETAIL_LEN]


class GeminiBackend(Backend):
    provider = "gemini"
    review_batch_size: int | None = REVIEW_BATCH_SIZE
    economy = True  # the free tier counts requests per day and is often overloaded

    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 300.0, concurrency: int = 1, max_tokens_override: int | None = None,
                 cache: Any = None, use_cache: bool = True, max_attempts: int = 4,
                 fallback_model: str = "", overload_wait_s: float = OVERLOAD_WAIT_S,
                 transport: Callable[..., HttpResult] | None = None) -> None:
        self._notes: list[str] = []
        super().__init__(model=_strip_models(model or DEFAULT_MODEL), effort="", concurrency=concurrency,
                         cache=cache, use_cache=use_cache, max_attempts=max_attempts)
        if not api_key:
            raise LLMUnavailable("Не задан ключ GEMINI_API_KEY. " + KEY_HELP)
        register_secret(api_key)
        self._key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_tokens_override = max_tokens_override
        self.transport: Callable[..., HttpResult] = transport or request_json
        self.configured_model = self.model  # name from the settings: the answer-cache key, stable across launches
        fb = _strip_models((fallback_model or "").strip())
        self.fallback_model = "" if fb == self.model else fb
        self.main_model = self.model  # main slot (after discovery: the discovered model)
        self.dead: dict[str, str] = {}  # model → why it is out for the rest of the run (day quota, 404)
        self.overload_wait_s = max(0.0, float(overload_wait_s))  # per-run ceiling of overload pauses
        self.overload_waited = 0.0
        self.round = 0  # pipeline round in economy mode (Backend.set_round)
        self.served: dict[str, int] = {}  # model → answers received (HTTP 200)
        self.requests_by_model: dict[str, int] = {}  # model → generateContent requests sent
        self.model_versions: dict[str, str] = {}  # model (alias) → modelVersion from the response
        self._out_cap: int | None = None
        self._candidates: list[str] | None = None
        self._tried: set[str] = set()
        self._discovered_n = 0
        self._model_lock = threading.Lock()

    # -- economy rounds -------------------------------------------------------------------------------
    def set_round(self, n: int) -> None:
        self.round = int(n)

    def can_answer(self) -> bool:
        return bool(self.live_models())

    def _call_wait_cap(self) -> float:
        """Pauses one call may spend: a share of what is left in the first economy round, more in the repeat."""
        if self.round == 1:
            return min(CALL_WAIT_CAP, max(0.0, self._overload_left()) * FIRST_ROUND_SHARE)
        if self.round == 2:
            return RETRY_CALL_WAIT_CAP
        return CALL_WAIT_CAP

    # -- what the report sees ------------------------------------------------------------------------
    @property
    def cache_model(self) -> str:
        return self.configured_model

    @property
    def notes(self) -> list[str]:
        out = list(self._notes)
        line = self.usage_note()
        if line and line not in out:
            out.append(line)
        return out

    @notes.setter
    def notes(self, value: list[str]) -> None:
        self._notes = list(value)

    def add_note(self, text: str) -> None:
        with self._lock:
            if text not in self._notes:
                self._notes.append(text)

    @property
    def switched(self) -> bool:
        """True when a model other than the main one answered or the main one dropped out."""
        served = getattr(self, "served", {})
        return bool(getattr(self, "dead", {})) or any(m != getattr(self, "main_model", "") for m in served)

    @property
    def model_version(self) -> str:
        """Real model behind the alias in use (``modelVersion`` of the last answer), or ''."""
        return self.model_versions.get(self.model, "")

    def model_label(self, model: str) -> str:
        v = self.model_versions.get(model, "")
        return "%s (%s)" % (model, v) if v and v != model else model

    def usage_note(self) -> str:
        """«Ответы Gemini по моделям: …» — only when more than one model was involved in the run."""
        if not (getattr(self, "_discovered_n", 0) or self.switched):
            return ""
        with self._lock:
            served = dict(self.served)
            sent = sum(self.requests_by_model.values())
        if not sent:
            return ""
        seen: list[str] = []
        for m in [self.main_model, self.fallback_model] + list(served):
            if m and m not in seen:
                seen.append(m)
        parts = ["%s — %d" % (self.model_label(m), served.get(m, 0)) for m in seen]
        return "Ответы Gemini по моделям: %s; всего запросов к модели: %d." % (", ".join(parts), sent)

    # -- request pieces -------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key}

    def out_budget(self, max_tokens: int, stage: str = "") -> int:
        if self.max_tokens_override:
            n = self.max_tokens_override
        elif max_tokens >= MAX_RETRY_TOKENS:  # a repeat after a cut, or a set-level stage: the full room
            n = min(max_tokens, MODEL_OUTPUT_CAP)
        else:
            n = max(max_tokens, 32768)
            bs = self.review_batch_size or DEFAULT_BATCH
            if stage.startswith("review") and bs > DEFAULT_BATCH:
                n = max(n, math.ceil(max_tokens * bs / DEFAULT_BATCH))
            n = min(n, FIRST_ATTEMPT_CAP)
        if self._out_cap:
            n = min(n, self._out_cap)
        return n

    def build_body(self, system: str, user: str, max_tokens: int) -> dict:
        n = self.out_budget(max_tokens, self.current_stage)
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": n},
        }

    # -- which model ----------------------------------------------------------------------------------
    def live_models(self) -> list[str]:
        """Models usable now, the preferred one (``self.model``: the last that answered) first."""
        with self._model_lock:
            pool = [m for m in (self.main_model, self.fallback_model) if m and m not in self.dead]
            if self.model in pool:
                pool.remove(self.model)
                pool.insert(0, self.model)
            return pool

    def _answered(self, model: str) -> None:
        with self._model_lock:
            first_spare = model == self.fallback_model and model not in self.served
            self.model = model
        with self._lock:
            self.served[model] = self.served.get(model, 0) + 1
        if first_spare:
            self.add_note("Gemini: основная модель «%s» не ответила (перегружена или без квоты) — запросы выполняет "
                          "резервная модель «%s» (GEMINI_FALLBACK_MODEL). Следующие запросы начинаются с той модели, "
                          "что ответила последней." % (self.main_model, model))

    def _kill(self, model: str, why: str) -> None:
        """Take a model out for the rest of the run (day quota, missing spare)."""
        with self._model_lock:
            if model in self.dead:
                return
            self.dead[model] = why
            if self.model == model:
                rest = [m for m in (self.main_model, self.fallback_model) if m and m not in self.dead]
                if rest:
                    self.model = rest[0]
        self.add_note("Gemini: %s — модель «%s» больше не используется в этом запуске." % (why, model))

    def _fetch_candidates(self) -> list[str]:
        models: list[dict] = []
        token = ""
        for _ in range(5):
            url = self.base_url + "/v1beta/models?pageSize=1000" + ("&pageToken=" + quote(token) if token else "")
            res = self.transport("GET", url, headers=self._headers(), timeout=min(self.timeout, 60.0))
            if res.status != 200 or not isinstance(res.data, dict):
                break
            models.extend(res.data.get("models") or [])
            token = res.data.get("nextPageToken") or ""
            if not token:
                break
        return rank_models(models)

    def _discover(self, failed_model: str, msg: str = "") -> bool:
        """After a 404 of the main model pick the next candidate from the model list (at most 3, never the same
        one twice). True when the main model was changed (by this thread or another one)."""
        with self._model_lock:
            if self.main_model != failed_model:  # another thread already switched
                return True
            self._tried.add(failed_model)
            if self._discovered_n >= MAX_DISCOVERY_CANDIDATES:
                return False
            if self._candidates is None:
                self._candidates = self._fetch_candidates()
            new = next((c for c in self._candidates if c not in self._tried and c != self.fallback_model), None)
            if not new:
                return False
            first = self._discovered_n == 0
            self._discovered_n += 1
            self._tried.add(new)
            self.main_model = new
            if self.model == failed_model:
                self.model = new
        if first:
            self.add_note("Модель Gemini «%s» недоступна (снята с обслуживания или нет доступа) — "
                          "автоматически выбрана «%s»." % (failed_model, new))
        else:
            self.add_note("Модель Gemini «%s» есть в списке, но не отвечает (%s) — выбрана следующая: «%s»."
                          % (failed_model, (redact(msg)[:100] or "404"), new))
        return True

    def _overload_left(self) -> float:
        with self._lock:
            return self.overload_wait_s - self.overload_waited

    def _overload_plan(self, first: str) -> list[tuple[str, float]]:
        """Attempts after ``first`` answered 503: (model, pause before the attempt).

        Round 1: the model that failed once more, then the other model twice. Later rounds: both models once,
        after a growing pause. With the per-run waiting spent: one attempt on each model that was not tried."""
        others = [m for m in self.live_models() if m != first]
        if self._overload_left() <= 0:
            return [(m, 0.0) for m in others]
        plan: list[tuple[str, float]] = [(first, SAME_MODEL_PAUSE)]
        for m in others:
            plan += [(m, 0.0), (m, SAME_MODEL_PAUSE)]
        order = [first] + others
        for pause in (RETRY_ROUND_PAUSES if self.round == 2 else ROUND_PAUSES):
            plan.append((order[-1] if others else first, pause))
            if others:
                plan.append((first, 0.0))
        return plan

    # -- the call -------------------------------------------------------------------------------------
    def _raw(self, system: str, user: str, max_tokens: int, *, effort: str | None = None) -> tuple[str, Usage]:
        live = self.live_models()
        if not live:
            raise LLMUnavailable("Gemini: в этом запуске не осталось доступных моделей (%s)."
                                 % "; ".join("%s — %s" % kv for kv in self.dead.items()))
        model = live[0]
        sent = 0
        plan: list[tuple[str, float]] | None = None  # overload sequence of this call, built at the first 503
        overloads: list[str] = []  # models that answered 503 in this call, in order
        waited = 0.0
        last_detail = ""
        exc_daily: LLMUnavailable | None = None
        call_cap = self._call_wait_cap()

        def next_overload_step() -> str | None:
            nonlocal waited
            while plan:
                m, pause = plan.pop(0)
                if m in self.dead:
                    continue
                if pause > 0:
                    if waited + pause > call_cap or self._overload_left() < pause:
                        if self._overload_left() < pause:
                            self.add_note("Gemini: время ожидания при перегрузке модели на этот запуск (%d с, "
                                          "GEMINI_OVERLOAD_WAIT_S) израсходовано — дальше каждый шаг делает по одной "
                                          "попытке на модель и при отказе пропускается." % int(self.overload_wait_s))
                        # without the pause only the models not yet tried in this call get their one attempt
                        rest = [x for x, _p in plan if x not in overloads and x not in self.dead]
                        plan[:] = []
                        return rest[0] if rest else None
                    with self._lock:
                        self.overload_waited += pause
                    waited += pause
                    # shown by the pipeline's heartbeat line (at most one line per ~15 s of silence)
                    self.wait_status = "Gemini перегружен (503): жду %d с, попытка %d" % (round(pause), sent + 1)
                    try:
                        self.sleep(pause)
                    finally:
                        self.wait_status = ""
                return m
            return None

        for _ in range(40):
            url = "%s/v1beta/models/%s:generateContent" % (self.base_url, quote(model, safe="-._"))
            body = self.build_body(system, user, max_tokens)
            if sent:
                with self._lock:
                    self.calls += 1  # a repeat inside this call is one more request to the provider
            sent += 1
            with self._lock:
                self.requests_by_model[model] = self.requests_by_model.get(model, 0) + 1
            t0 = time.monotonic()
            res = self.transport("POST", url, headers=self._headers(), body=body, timeout=self.timeout)
            if res.status == 200:
                self._answered(model)
                return self._parse(res, model, body["generationConfig"]["maxOutputTokens"], time.monotonic() - t0)
            msg = error_message(res)
            detail = provider_detail(res, msg)
            low = (msg + " " + res.text[:2000]).lower()

            if res.status == 404 or (res.status == 400 and "not found" in low and "model" in low):
                if model == self.fallback_model:
                    self._kill(model, "резервная модель «%s» не найдена (404), проверьте GEMINI_FALLBACK_MODEL" % model)
                elif self._discover(model, msg):
                    pass
                else:
                    tried = ", ".join(sorted(self._tried - {model}))
                    if not [m for m in self.live_models() if m != model]:
                        raise LLMUnavailable(
                            "Модель Gemini «%s» не найдена, и подходящую замену подобрать не удалось%s. "
                            "Укажите в .env GEMINI_MODEL=gemini-flash-latest."
                            % (model, (" (проверены также: %s)" % tried) if tried else ""), detail=detail)
                    self._kill(model, "модель «%s» не найдена (404), замена из списка не подошла" % model)
                nxt = next_overload_step() if plan is not None else None
                if nxt is None:
                    live = self.live_models()
                    if not live:
                        raise exc_daily or LLMUnavailable("Gemini: доступных моделей не осталось.", detail=detail)
                    nxt = live[0]
                model = nxt
                continue
            if res.status == 400 and ("api_key_invalid" in low or "api key not valid" in low or "api key expired" in low):
                raise LLMUnavailable("Ключ GEMINI_API_KEY не принят (неверный или просрочен). " + KEY_HELP, detail=detail)
            if res.status == 400 and "location is not supported" in low:
                raise LLMUnavailable("Gemini API недоступен из вашего региона. Используйте другой провайдер "
                                     "(ANTHROPIC_API_KEY, OPENAI_API_KEY) или --provider offline.", detail=detail)
            if res.status == 400 and ("input token count" in low or "exceeds the maximum number of tokens" in low
                                      or "too many tokens" in low or "request payload size exceeds" in low):
                # A per-request limit, not a per-key one: the other (smaller) steps of the run can still go
                # through, so this step is skipped like a bad answer and the run goes on.
                raise LLMBadOutput("Текст этого шага не поместился во входное окно модели %s: документ слишком "
                                   "длинный для неё. Шаг пропущен, проверка продолжается. Сократите документ "
                                   "(разбейте его на части) или запустите проверку с --depth fast." % model,
                                   detail=detail, kind="too_long")
            if res.status == 400 and ("maxoutputtokens" in low or "max_output_tokens" in low) and not self._out_cap:
                self._out_cap = 8192
                self.add_note("Модель %s не принимает большой лимит ответа — лимит снижен до 8192 токенов." % model)
                continue
            if res.status in (401, 403):
                raise LLMUnavailable("Ключ GEMINI_API_KEY не принят или у него нет доступа к модели %s (%d). %s"
                                     % (model, res.status, KEY_HELP), detail=detail)
            if res.status == 429:
                daily, zero = _daily_quota(res), _zero_quota(res)
                if not (daily or zero):
                    raise LLMTransient("Gemini: превышен лимит запросов в минуту (429), модель %s." % model,
                                       retry_after=_retry_delay(res), kind="rate", detail=detail)
                why = ("дневная квота бесплатного уровня для модели «%s» исчерпана (429)" % model if daily
                       else "у модели «%s» нет квоты на бесплатном уровне (429, лимит 0)" % model)
                self._kill(model, why)
                killed = [m for m in self.dead]
                exc_daily = LLMUnavailable(
                    ("Дневная квота бесплатного тарифа Gemini исчерпана (%s). Повторите завтра или используйте "
                     "другой ключ." if daily else
                     "У модели Gemini нет квоты на бесплатном тарифе (%s). Укажите другую модель в GEMINI_MODEL "
                     "или используйте другой ключ.") % ", ".join(killed), detail=detail)
                nxt = next_overload_step() if plan is not None else None
                if nxt is None:
                    live = self.live_models()
                    if not live:
                        raise exc_daily
                    nxt = live[0]
                model = nxt
                continue
            if res.status in (408,) or res.status >= 500:
                if not _is_overload(res, low):
                    raise LLMTransient("Gemini: временная ошибка сервера (%d), модель %s." % (res.status, model),
                                       retry_after=parse_retry_after(res.headers), kind="server", detail=detail)
                overloads.append(model)
                last_detail = detail
                if plan is None:
                    plan = self._overload_plan(model)
                nxt = next_overload_step()
                if nxt is None:
                    names = []
                    for m in overloads:
                        if m not in names:
                            names.append(m)
                    where = ("на двух моделях" if len(names) > 1 else "на одной модели")
                    e = LLMTransient("Gemini: %s перегружен%s (%d), попыток %d %s; шаг пропущен. Это перегрузка "
                                     "на стороне Google, не ошибка ключа."
                                     % (", ".join("«%s»" % n for n in names), "ы" if len(names) > 1 else "а",
                                        res.status, len(overloads), where),
                                     kind="server", detail=last_detail)
                    e.final = True
                    raise e
                model = nxt
                continue
            raise LLMUnavailable("Gemini отклонил запрос (%d): %s" % (res.status, msg[:200]), detail=detail)
        raise LLMUnavailable("Gemini: не удалось подобрать рабочие параметры запроса.")

    def _parse(self, res: HttpResult, model: str, limit: int, seconds: float) -> tuple[str, Usage]:
        d = res.data if isinstance(res.data, dict) else {}
        ver = d.get("modelVersion")
        if isinstance(ver, str) and ver.strip():
            with self._lock:
                self.model_versions[model] = _strip_models(ver.strip())[:80]
        um = d.get("usageMetadata") or {}
        prompt = int(um.get("promptTokenCount") or 0)
        cached = int(um.get("cachedContentTokenCount") or 0)
        usage = Usage(input_tokens=max(0, prompt - cached),
                      output_tokens=int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0),
                      cache_read=cached, seconds=seconds)
        pf = d.get("promptFeedback") or {}
        if pf.get("blockReason"):
            e = LLMRefused("Gemini отказался обрабатывать запрос (причина: %s)." % pf.get("blockReason"))
            e.usage = usage
            raise e
        cands = d.get("candidates") or []
        if not cands:
            e2 = LLMBadOutput("Gemini вернул пустой ответ без вариантов.")
            e2.usage = usage
            raise e2
        c = cands[0] if isinstance(cands[0], dict) else {}
        reason = str(c.get("finishReason") or "")
        parts = ((c.get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if reason in _REFUSAL_REASONS:
            e3 = LLMRefused("Gemini прервал ответ по правилам безопасности (%s)." % reason)
            e3.usage = usage
            raise e3
        if reason == "MAX_TOKENS":
            e4 = LLMBadOutput("Ответ Gemini обрезан: не хватило лимита токенов ответа. Уменьшите пакет "
                              "(LLM_BATCH_SIZE) или задайте больший LLM_MAX_TOKENS.")
            e4.kind = "truncated"
            e4.limit = limit
            e4.usage = usage
            raise e4
        if not text.strip():
            e5 = LLMBadOutput("Gemini вернул пустой текст (finishReason=%s)." % (reason or "?"))
            e5.usage = usage
            raise e5
        return text, usage
