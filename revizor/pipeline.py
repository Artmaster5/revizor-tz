"""The review pipeline: load → structure → slice → rules → review → set checks → quotes + merge →
skeptic → questions → indices and verdict. Every model stage has an offline fallback; nothing raises.

    result = run("docs/requirements.md", backend=None, depth="full", progress=print)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from . import answers as ans_mod, baseline as baseline_mod, clarify, consistency, checklist as checklist_mod, gapcheck, impact, loader, rules, score, slicer, testgen, verdict
from .config import agent_commit, agent_dirty, agent_tag, agent_version
from .llm import Backend, LLMError, LLMTransient, LLMUnavailable
from .llm.pricing import cost_note, cost_usd
from .gate import apply_dedup, apply_gate
from .merge import (QuoteStats, SEV_RANK, dedupe_model, fold_formal, fold_rules, merge_duplicates, model_finding,
                    number, sort_key)
from .model import (GAP_TYPES, REQ_CRITERIA, CoverageRow, CriterionVerdict, Diagnostic, DroppedQuote, Finding,
                    FixAction, Question, QuestionDraft, Requirement, RequirementReview, Result, RunInfo, Skipped,
                    SourceDoc, Strength)
from .prompts import (BATCH_SIZE, COMBINED_NAME, COMBINED_STAGE, GAPS_STAGE, LENS_BY_STAGE, MAX_TOKENS, REVIEW_LENSES, SCHEMAS,
                      SET_FIRST_ATTEMPT_TOKENS, SET_NAME, SET_STAGES, DEDUP_CHUNK, GATE_CHUNK, GATE_CHUNK_ECONOMY, ContextDoc, Lens, build_system, injection_note,
                      answers_tag, answers_user, checklist_user, dedup_line, dedup_user, gaps_user, gate_user, questions_user, review_user,
                      set_user, slice_rest_user,
                      slice_user, tests_user)
from .quotes import QuoteIndex
from .textfmt import count, lines_text, shorten, W_BLOCK, W_DEFECT, W_FINDING, W_LINE, W_REQ

STEPS = 9

# Default gap type of a rule question, by criterion (rules do not name it themselves).
GAP_BY_CRITERION = {
    "unambiguity": "term", "comprehensibility": "term", "completeness": "value", "verifiability": "value",
    "set_completeness": "scenario", "consistency": "conflict", "set_consistency": "conflict",
    "feasibility": "scenario", "set_feasibility": "scenario", "traceability": "owner",
}
PRIORITY_BY_SEV = {"CRITICAL": "blocker", "MAJOR": "important", "MINOR": "nice"}
PRIO_RANK = {"blocker": 0, "important": 1, "nice": 2}
SET_HINT_CRITERIA = {
    "set_consistency": ("set_consistency", "comprehensibility", "consistency"),
    "set_completeness": ("set_completeness", "completeness"),
    "set_standards": ("set_feasibility", "traceability", "feasibility"),
}


class StageSkipped(Exception):
    """The model is not asked: it became unavailable or the cost cap was reached."""


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


STAGE_GROUP = {"slice": "нарезка", "slice_rest": "нарезка", "review": "проверка требований",
               "review_clarity": "проверка требований", "review_completeness": "проверка требований",
               "review_correctness": "проверка требований", "set_consistency": "проверка набора",
               "set_completeness": "проверка набора", "set_standards": "проверка набора", "set_gaps": "проверка набора",
               "skeptic": "перепроверка", "dedup": "перепроверка", "questions": "вопросы", "answers": "ответы автора", "tests": "тесты",
               "checklist": "чек-лист документа"}
HEARTBEAT_S = 15.0  # a «still waiting» line after this many silent seconds

_STAGE_PREFIX = re.compile(r"^\[(\d+)/" + str(STEPS) + r"\] ")


class StageClock:
    """Wall-clock seconds of each of the nine stages, measured at the ``[n/9]`` progress lines.

    ``wrap(say)`` returns a ``say`` that appends « · N с» to every ``[n/9]`` line (not to a skipped stage) and records
    N. A stage lasts from the previous stage line to its own. Stages 4 and 5 share one pool of requests, so the
    pipeline gives them their own spans through ``set_span`` (pool start → last request of that stage)."""

    def __init__(self, t0: float | None = None) -> None:
        self.last = time.monotonic() if t0 is None else t0
        self.sec: dict[int, float] = {}
        self.span: dict[int, float] = {}
        self.lock = threading.Lock()

    def set_span(self, n: int, seconds: float) -> None:
        with self.lock:
            self.span[n] = max(0.0, seconds)

    def wrap(self, say: Callable[[str], None]) -> Callable[[str], None]:
        def timed(line: str) -> None:
            m = _STAGE_PREFIX.match(str(line))
            if not m:
                say(line)
                return
            n, now = int(m.group(1)), time.monotonic()
            with self.lock:
                sec = self.span.pop(n, None)
                if sec is None:
                    sec = max(0.0, now - self.last)
                if n != 4:  # stage 5 starts together with stage 4 (one pool)
                    self.last = now
                self.sec[n] = round(sec, 1)
            skipped = "… пропущено" in str(line)
            say(line if skipped else f"{line} · {int(round(sec))} с")
        return timed

    def seconds(self) -> list[float]:
        with self.lock:
            return [self.sec.get(n, 0.0) for n in range(1, STEPS + 1)]


class Gate:
    """Single door to the model: cost cap, dead-provider switch, audit of every call, live progress."""

    def __init__(self, backend: Backend, system: str, max_cost_usd: float | None, audit: list[dict],
                 say: Callable[[str], None] | None = None, heartbeat_s: float = HEARTBEAT_S):
        self.backend = backend
        self.system = system
        self.max_cost = max_cost_usd
        self.audit = audit
        self.lock = threading.Lock()
        self.dead = ""  # reason the provider stopped answering
        self.budget_hit = False
        self.unknown_price_warned = False
        self.answered = 0  # calls that returned a usable answer
        self.say = say or (lambda _s: None)
        self.planned: dict[str, int] = {}
        self.done: dict[str, int] = {}
        self.in_flight: dict[int, tuple[str, float]] = {}  # thread id → (stage, start)
        self.last_print = time.monotonic()
        self.heartbeat_s = heartbeat_s
        self._stop = threading.Event()
        self._hb: threading.Thread | None = None
        self._tls = threading.local()  # last failed model_call entry of this thread (economy second round)

    def plan(self, stage_or_group: str, n: int) -> None:
        g = STAGE_GROUP.get(stage_or_group, stage_or_group)
        with self.lock:
            self.planned[g] = self.planned.get(g, 0) + n

    def cost(self) -> float | None:
        b = self.backend
        return cost_usd(b.provider, b.model, b.totals)

    # -- liveness ---------------------------------------------------------------------------
    def _emit(self, line: str) -> None:
        with self.lock:
            self.last_print = time.monotonic()
        self.say(line)

    def _heartbeat(self) -> None:
        while not self._stop.wait(min(1.0, self.heartbeat_s / 3)):
            line = self.idle_line(time.monotonic())
            if line:
                self._emit(line)

    def idle_line(self, now: float) -> str:
        """The «still waiting» line after ``heartbeat_s`` silent seconds with a call in flight, else ""."""
        with self.lock:
            silent = now - self.last_print
            flight = list(self.in_flight.values())
        if not flight or silent < self.heartbeat_s:
            return ""
        stage, start = min(flight, key=lambda x: x[1])
        g = STAGE_GROUP.get(stage, stage)
        status = getattr(self.backend, "wait_status", "") or ""
        if status:  # the backend is pausing on its own (provider overloaded): say so instead
            return f"  … {status} — {g}, шаг идёт {int(now - start)} с"
        return (f"  … жду ответа модели: {g}, в работе запросов: {len(flight)}, "
                f"самый долгий — {int(now - start)} с")

    def _start_heartbeat(self) -> None:
        with self.lock:
            if self._hb is not None or self.heartbeat_s <= 0:
                return
            self._hb = threading.Thread(target=self._heartbeat, name="revizor-heartbeat", daemon=True)
        self._hb.start()

    def close(self) -> None:
        self._stop.set()
        if self._hb is not None:
            self._hb.join(timeout=2)

    def _progress(self, stage: str, seconds: float, ok: bool, note: str = "") -> None:
        g = STAGE_GROUP.get(stage, stage)
        with self.lock:
            self.done[g] = self.done.get(g, 0) + 1
            k, n = self.done[g], max(self.planned.get(g, 0), self.done[g])
        c = self.cost()
        money = f" · ${c:.2f}" if c is not None else ""
        mark = "✓" if ok else "✗"
        self._emit(f"  {mark} {g} {k}/{n} · {int(round(seconds))} с{money}" + (f" · {note}" if note else ""))

    def max_tokens_for(self, stage: str) -> int:
        """Thinking is billed inside max_tokens: the set-level stages ask for SET_FIRST_ATTEMPT_TOKENS at once
        (Claude and Gemini accept it; other servers have lower caps and keep MAX_TOKENS)."""
        n = MAX_TOKENS.get(stage, 16000)
        if (stage in SET_STAGES or stage == GAPS_STAGE) and self.backend.provider in ("anthropic", "gemini"):
            n = max(n, SET_FIRST_ATTEMPT_TOKENS)
        return n

    def can_call(self) -> bool:
        """True while a new call may still be sent: the provider answers and the cost cap is not reached."""
        if self.dead or self.budget_hit:
            return False
        if self.max_cost is not None:
            c = self.cost()
            if c is not None and c >= self.max_cost:
                return False
        return bool(self.backend.can_answer())

    # -- the call --------------------------------------------------------------------------
    def call(self, stage: str, user: str) -> dict:
        if self.dead:
            raise StageSkipped(self.dead)
        if self.max_cost is not None:
            c = self.cost()
            if c is not None and c >= self.max_cost:
                with self.lock:
                    self.budget_hit = True
                raise StageSkipped(f"достигнут лимит стоимости ${self.max_cost:g} (потрачено ${c:.4f})")
        t0 = time.monotonic()
        tid = threading.get_ident()
        with self.lock:
            self.in_flight[tid] = (stage, t0)
        self._start_heartbeat()
        entry: dict[str, Any] = {"type": "model_call", "stage": stage, "provider": self.backend.provider,
                                 "model": self.backend.model, "system_sha256": _sha(self.system),
                                 "user_sha256": _sha(user)}
        try:
            data, usage = self.backend.complete_json(self.system, user, schema=SCHEMAS.get(stage),
                                                     max_tokens=self.max_tokens_for(stage), stage=stage)
        except LLMUnavailable as e:
            with self.lock:
                self.dead = e.user_message
            entry.update(ok=False, error=e.user_message[:300], detail=str(getattr(e, "detail", "") or "")[:300],
                         seconds=round(time.monotonic() - t0, 2))
            self._log(entry)
            self._done_call(tid, stage, t0, False, "модель недоступна")
            raise
        except LLMError as e:
            entry.update(ok=False, error=e.user_message[:300], detail=str(getattr(e, "detail", "") or "")[:300],
                         seconds=round(time.monotonic() - t0, 2))
            self._log(entry)
            self._tls.last_failed = entry
            self._done_call(tid, stage, t0, False, "ответ не получен")
            raise
        except BaseException:
            self._done_call(tid, stage, t0, False, "ошибка")
            raise
        entry.update(ok=True, model=self.backend.model, input_tokens=usage.input_tokens,
                     output_tokens=usage.output_tokens, cache_read=usage.cache_read, cache_write=usage.cache_write,
                     seconds=round(usage.seconds, 2), from_cache=usage.from_cache,
                     answer_sha256=_sha(json.dumps(data, ensure_ascii=False, sort_keys=True)))
        self._log(entry)
        take = getattr(self.backend, "take_dropped", None)
        for d in (take() if take else []):  # items the validator could not repair: dropped, never document text
            self._log({"type": "item_dropped", "stage": stage, "path": d.get("path", ""),
                       "reason": str(d.get("reason", ""))[:160]})
        with self.lock:
            self.answered += 1
        self._done_call(tid, stage, t0, True, "из кэша агента" if usage.from_cache else "")
        return data

    def _done_call(self, tid: int, stage: str, t0: float, ok: bool, note: str) -> None:
        with self.lock:
            self.in_flight.pop(tid, None)
        self._progress(stage, time.monotonic() - t0, ok, note)

    def _log(self, entry: dict) -> None:
        with self.lock:
            # a stage that runs beside another one collects its entries apart (``sink``) and the pipeline adds them
            # at the place the sequential run would have written them
            sink = getattr(self._tls, "sink", None)
            (self.audit if sink is None else sink).append(entry)


# --- prompt inputs shared with the estimate -------------------------------------------------


def batches(requirements: list[Requirement], size: int | None = None) -> list[list[Requirement]]:
    if not requirements:
        return []
    n = math.ceil(len(requirements) / (size or BATCH_SIZE))
    size = math.ceil(len(requirements) / n)
    return [requirements[i:i + size] for i in range(0, len(requirements), size)]


def batch_payload(batch: list[Requirement]) -> list[dict]:
    from .textfmt import last_section

    return [{"rid": r.rid, "doc_id": r.doc_id, "section": last_section(r.section), "kind": r.kind,
             "lines": lines_text(r.line_start, r.line_end), "text": r.quote} for r in batch]


def req_list_payload(requirements: list[Requirement]) -> list[dict]:
    from .textfmt import last_section

    return [{"rid": r.rid, "doc_id": r.doc_id, "section": last_section(r.section),
             "summary": r.summary or shorten(r.quote, 110)} for r in requirements]


def _hint_line(f: Finding) -> str:
    q = f.quotes[0]
    where = lines_text(q.line_start, f.quotes[-1].line_end if len(f.quotes) > 1 else q.line_end)
    rids = ", ".join(f.req_ids[:6])
    kind = " (наблюдение низкой точности)" if f.advisory else ""
    return f"- {where}{' · ' + rids if rids else ''}: [{f.criterion}, {f.severity}] {shorten(f.title, 120)}{kind}"


def review_hints(rule_findings: list[Finding], batch: list[Requirement], lens: Lens | None) -> list[str]:
    rids = {r.rid for r in batch}
    crit = set(lens.criteria) if lens else set(REQ_CRITERIA)
    return [_hint_line(f) for f in rule_findings
            if f.scope == "requirement" and f.criterion in crit and rids & set(f.req_ids)]


def set_hints(rule_findings: list[Finding], stage: str) -> list[str]:
    crit = SET_HINT_CRITERIA.get(stage, ())
    return [_hint_line(f) for f in rule_findings if f.scope == "set" and f.criterion in crit]


def review_stages(depth: str) -> list[Lens | None]:
    return list(REVIEW_LENSES) if depth == "full" else [None]


def injection_lines(findings: list[Finding]) -> list[int]:
    out = []
    for f in findings:
        if "prompt-injection" in f.rule_ids or ("hidden-text" in f.rule_ids and f.severity == "CRITICAL"):
            out += [q.line_start for q in f.quotes]
    return sorted(set(out))


def load_contexts(paths: list[str], warnings: list[str]) -> list[ContextDoc]:
    out = []
    for p in paths or []:
        d = loader.load(p)
        if isinstance(d, Diagnostic):
            warnings.append(f"Файл контекста «{d.name or p}» не использован: {d.title}")
            continue
        out.append(ContextDoc(name=d.name, text=d.text))
    return out


# --- the run ------------------------------------------------------------------------------------


def run(input_path: str, *, backend: Backend | None = None, depth: str = "full",
        progress: Callable[[str], None] | None = None, contexts: list[str] | None = None,
        max_cost_usd: float | None = None, unavailable_reason: str = "",
        answers: Callable[[list[Question]], "ans_mod.Parsed"] | None = None, baseline_path: str = "") -> Result:
    """Never raises (except KeyboardInterrupt): any failure ends up in the result."""
    say = progress or (lambda _s: None)
    t0 = time.monotonic()
    info = RunInfo(agent_version=agent_version(), started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                   rules_version=rules.RULESET_VERSION, agent_commit=agent_commit())
    if info.agent_commit:
        info.agent_tag, info.agent_dirty = agent_tag(), agent_dirty()
    try:
        return _run(input_path, backend, depth if depth in ("full", "fast") else "full", say, contexts or [],
                    max_cost_usd, unavailable_reason, info, t0, answers, baseline_path)
    except KeyboardInterrupt:
        raise
    except Exception as e:  # last line of defence: a report must still be written
        info.duration_s = round(time.monotonic() - t0, 2)
        info.warnings.append(f"Внутренняя ошибка агента ({type(e).__name__}); отчёт неполный.")
        diag = Diagnostic(code="READ_ERROR", title="Проверка прервана внутренней ошибкой агента",
                          what_happened="Агент не смог довести проверку до конца из-за ошибки в собственном коде.",
                          what_to_do=["Запустите проверку ещё раз.",
                                      "Если ошибка повторится, запустите с --provider offline и сообщите разработчикам "
                                      "текст из строки «Технические подробности»."],
                          details=f"{type(e).__name__}: {str(e)[:300]}", path=str(input_path),
                          name=str(input_path).rsplit("/", 1)[-1])
        return Result(run=info, diagnostic=diag, verdict="NOT_CHECKED", verdict_text=verdict.TEXT["NOT_CHECKED"])


def _run(input_path, backend, depth, say, context_paths, max_cost_usd, unavailable_reason, info: RunInfo, t0,
         answers=None, baseline_path=""):
    audit: list[dict] = []
    warnings = info.warnings
    clock = StageClock(t0)  # seconds per stage: appended to every «[n/9]» line, kept in ``info.stage_seconds``
    say = clock.wrap(say)

    # 1. load ------------------------------------------------------------------------------
    doc = loader.load(input_path)
    if isinstance(doc, Diagnostic):
        say(f"[1/{STEPS}] Читаю документ… не удалось: {doc.title}")
        info.doc_name = doc.name
        info.doc_size_bytes = doc.size_bytes
        info.mode = "offline" if backend is None else depth
        info.duration_s = round(time.monotonic() - t0, 2)
        return Result(run=info, diagnostic=doc, verdict="NOT_CHECKED",
                      verdict_text=verdict.TEXT["NOT_CHECKED"] + ": " + doc.title)
    _doc_info(info, doc)
    warnings.extend(doc.warnings)
    if doc.masked:
        audit.append({"type": "decision", "what": "values_masked", "count": len(doc.masked)})
    pages = f", {doc.pages} стр." if doc.pages else ""
    say(f"[1/{STEPS}] Читаю документ… {count(len(doc.lines), W_LINE)}{pages}, {count(len(doc.blocks), W_BLOCK)}")

    ctx_docs = load_contexts(context_paths, warnings)
    info.context_files = [c.name for c in ctx_docs]
    system = build_system(doc, ctx_docs)

    if backend is None and unavailable_reason:
        warnings.append(f"Модель недоступна: {unavailable_reason}. Отчёт построен без модели, только правилами.")
        audit.append({"type": "decision", "what": "provider_fallback", "reason": unavailable_reason[:300]})
    gate = Gate(backend, system, max_cost_usd, audit, say=say) if backend is not None else None
    if gate is not None and max_cost_usd is not None and gate.cost() is None:
        warnings.append(f"Цена модели {backend.model} неизвестна, поэтому ограничение стоимости ${max_cost_usd:g} "
                        "не может быть применено.")

    # Injection lines are known before any model call (block-level rules do not depend on slicing).
    h_reqs, h_skipped = slicer.heuristic_slice(doc)
    pre_rules = rules.run_rules(doc, h_reqs, h_skipped)
    inj_lines = injection_lines(pre_rules)
    inj = injection_note(inj_lines)
    if inj_lines:
        info.injection_suspected = True
        warnings.append("В документе есть текст, обращённый к проверяющему ИИ (" + lines_text(inj_lines[0], inj_lines[0])
                        + (", " + ", ".join(str(x) for x in inj_lines[1:]) if len(inj_lines) > 1 else "")
                        + "). Агент его не выполнял.")

    # 2. slice ---------------------------------------------------------------------------------
    economy = bool(getattr(backend, "economy", False)) if gate is not None else False
    requirements, skipped, slice_how = _slice(doc, gate, inj, h_reqs, h_skipped, warnings, audit, economy=economy)
    if gate is not None and gate.dead and gate.answered == 0:
        warnings.append(f"Модель недоступна: {gate.dead}. Отчёт построен без модели, только правилами.")
        audit.append({"type": "decision", "what": "provider_fallback", "reason": gate.dead[:300]})
        gate.close()
        gate = None
    model_on = gate is not None
    info.mode = depth if model_on else "offline"
    say(f"[2/{STEPS}] Нарезаю документ на требования ({slice_how})… найдено {len(requirements)}, "
        f"не требования: {len(skipped)}")

    # 3. rules ------------------------------------------------------------------------------------
    rule_findings = rules.run_rules(doc, requirements, skipped)
    for err in rules.last_errors:
        warnings.append("Автоматическое правило не выполнено: " + err)
    counted_rules = [f for f in rule_findings if not f.advisory]
    say(f"[3/{STEPS}] Проверяю правилами… {count(len(counted_rules), W_FINDING)}"
        + (f", наблюдений {len(rule_findings) - len(counted_rules)}" if len(rule_findings) != len(counted_rules) else ""))

    qi = QuoteIndex(doc, requirements)
    stats = QuoteStats()
    dropped: list[DroppedQuote] = []
    known = {r.rid for r in requirements}
    reviews = _empty_reviews(requirements, model_on)
    coverage: list[CoverageRow] = []
    model_raw = 0
    unchecked: list[str] = []

    # 4–5. review and set checks ---------------------------------------------------------------
    model_findings: list[Finding] = []
    try:
        gap_cells, gap_send = gapcheck.initial_cells(doc)
    except Exception as e:  # the completeness map is optional: never breaks the run
        gap_cells, gap_send = [], []
        warnings.append(f"Карта полноты не составлена: внутренняя ошибка ({type(e).__name__}).")
    gap_found = 0
    if model_on:
        jobs = []
        for b in batches(requirements, getattr(backend, "review_batch_size", None)):
            for lens in review_stages(depth):
                jobs.append(("review", b, lens))
        for st in SET_STAGES:
            jobs.append(("set", None, st))
        if gap_send:
            jobs.append(("gaps", None, gap_send))
        for kind, _b, what in jobs:
            gate.plan("review" if kind == "review" else GAPS_STAGE if kind == "gaps" else what, 1)
        say(f"      Отправляю модели {len(jobs)} запросов (одновременно до {gate.backend.concurrency})…")
        pool_t0, done_at = time.monotonic(), {}
        results = _run_jobs(gate, jobs, rule_findings, requirements, inj, economy=economy, done_at=done_at)
        for n_stage, is_review in ((4, True), (5, False)):  # each stage's span: pool start → its last request
            ends = [done_at[id(j)] for j in jobs if (j[0] == "review") == is_review and id(j) in done_at]
            if ends:
                clock.set_span(n_stage, max(ends) - pool_t0)
        if economy and gate.dead and gate.answered == 0:
            # Slicing made no request, so a dead provider (bad key, no quota) shows up only here: fall back to the
            # rules-only report exactly as a failed slicing call does.
            warnings.append(f"Модель недоступна: {gate.dead}. Отчёт построен без модели, только правилами.")
            audit.append({"type": "decision", "what": "provider_fallback", "reason": gate.dead[:300]})
            gate.close()
            gate = None
            model_on = False
            info.mode = "offline"
            reviews = _empty_reviews(requirements, model_on)
    if model_on:
        rev_found = set_found = 0
        rev_failed = set_failed = 0
        for (kind, b, what), (ans, err) in zip(jobs, results):
            if kind == "review":
                lens: Lens | None = what
                stage = lens.stage if lens else COMBINED_STAGE
                crits = lens.criteria if lens else REQ_CRITERIA
                lname = lens.name if lens else COMBINED_NAME
                if err:
                    rev_failed += 1
                    rng = f"{b[0].rid}…{b[-1].rid}" if len(b) > 1 else b[0].rid
                    unchecked.append(f"{rng} не проверены моделью (линза «{lname}»): {err}")
                    for r in b:
                        for c in crits:
                            reviews[r.rid].criteria[c] = CriterionVerdict("unchecked", "не проверено моделью: " + shorten(err, 120))
                    audit.append({"type": "decision", "what": "batch_unchecked", "stage": stage,
                                  "rids": [r.rid for r in b], "reason": err[:300]})
                    continue
                n_before = len(dropped)
                n_raw, found = _parse_review(ans, b, lens, stage, crits, reviews, qi, stats, known, dropped, audit)
                for dq in dropped[n_before:]:
                    audit.append({"type": "decision", "what": "quote_dropped", "stage": stage, "title": dq.title})
                model_raw += n_raw
                rev_found += len(found)
                model_findings += found
            elif kind == "gaps":
                if err:
                    set_failed += 1
                    unchecked.append(f"Проход-критик «чего не хватает» не выполнен: {err}; контрольные вопросы не оценены.")
                    audit.append({"type": "decision", "what": "set_stage_unchecked", "stage": GAPS_STAGE, "reason": err[:300]})
                    continue
                try:
                    found = gapcheck.apply_model(ans, gap_cells, what, qi=qi, stats=stats, known=known, audit=audit,
                                                 dropped=dropped)
                    model_raw += sum(len(x.get("findings") or []) for x in ans.get("items") or [] if isinstance(x, dict))
                    model_findings += found
                    gap_found = len(found)
                    set_found += len(found)
                except Exception as e:  # a bug in parsing must not kill the run
                    unchecked.append(f"Ответ прохода-критика не разобран: внутренняя ошибка ({type(e).__name__}).")
                    gap_cells, _send = gapcheck.initial_cells(doc)
            else:
                stage = what
                if err:
                    set_failed += 1
                    unchecked.append(f"Проверка набора «{SET_NAME[stage]}» не выполнена: {err}")
                    audit.append({"type": "decision", "what": "set_stage_unchecked", "stage": stage, "reason": err[:300]})
                    continue
                raw = [x for x in (ans.get("findings") or []) if isinstance(x, dict)]
                model_raw += len(raw)
                for x in raw:
                    f, dq = model_finding(x, scope="set", stage=stage, qi=qi, stats=stats, known_rids=known, audit=audit)
                    if f is not None:
                        model_findings.append(f)
                        set_found += 1
                    elif dq is not None:
                        dropped.append(dq)
                        audit.append({"type": "decision", "what": "quote_dropped", "stage": stage, "title": dq.title})
                if stage == "set_completeness":
                    coverage = _coverage(ans.get("coverage"), known)
        lens_txt = f"{len(batches(requirements, getattr(backend, 'review_batch_size', None)))} пакетов × {len(review_stages(depth))} линзы" if depth == "full" \
            else f"{len(batches(requirements, getattr(backend, 'review_batch_size', None)))} пакетов"
        say(f"[4/{STEPS}] Проверяю каждое требование моделью ({lens_txt})… подтверждено {rev_found}"
            + (f", не выполнено запросов: {rev_failed}" if rev_failed else ""))
        n_steps = len(SET_STAGES) + (1 if gap_send else 0)
        say(f"[5/{STEPS}] Проверяю набор требований ({n_steps} шага)… подтверждено {set_found}"
            + (f" (из них пробелов по контрольным вопросам: {gap_found})" if gap_send else "")
            + (f", не выполнено шагов: {set_failed}" if set_failed else ""))
    else:
        say(f"[4/{STEPS}] Проверка каждого требования моделью… пропущено: модель не используется")
        say(f"[5/{STEPS}] Проверка набора требований моделью… пропущено: модель не используется")

    # 6. merge -----------------------------------------------------------------------------------
    model_findings = dedupe_model(model_findings, audit)
    findings = number(fold_rules(rule_findings, model_findings, audit))
    info.quotes_confirmed = stats.confirmed + sum(len(f.quotes) for f in rule_findings)
    info.quotes_restored = stats.restored
    info.quotes_dropped = stats.dropped
    say(f"[6/{STEPS}] Сверяю цитаты и объединяю повторы… подтверждено цитат {info.quotes_confirmed}, "
        f"отброшено {info.quotes_dropped}")

    # 7. skeptic ---------------------------------------------------------------------------------
    if model_on and depth == "full" and not gate.dead:
        _skeptic(gate, findings, info, inj, audit, unchecked, say, qi=qi, economy=economy)
    else:
        why = "быстрый режим" if model_on else "модель не используется"
        say(f"[7/{STEPS}] Перепроверка замечаний… пропущено: {why}")

    info.critical_demoted = sum(1 for a in audit if a.get("what") == "critical_without_reason")

    open_f = [f for f in findings if f.status == "open"]
    rejected = [f for f in findings if f.status in ("rejected", "merged")]

    if info.injection_suspected and model_on and model_raw == 0 and counted_rules:
        warnings.append("Модель не нашла замечаний в документе с подсадным текстом — её результат мог быть искажён, "
                        "опирайтесь на замечания правил.")

    # 8. questions ----------------------------------------------------------------------------------
    counted_open = sorted([f for f in open_f if not f.advisory], key=sort_key)
    questions, fix_first, reason, strengths = [], [], "", []
    q_by_model = False
    if model_on and counted_open and not gate.dead and not gate.budget_hit:
        gate.plan("questions", 1)
        try:
            ans = gate.call("questions", questions_user(_question_lines(counted_open), inj))
            questions, fix_first, reason, strengths = _parse_questions(ans, counted_open, known)
            q_by_model = True
        except (LLMError, StageSkipped) as e:
            unchecked.append(f"Вопросы автору составлены по шаблонам правил: модель не ответила ({_err(e)})")
    elif model_on and counted_open:
        unchecked.append("Вопросы автору составлены по шаблонам: этап вопросов не выполнялся "
                         f"({gate.dead or 'достигнут лимит стоимости'})")
    if not q_by_model:
        questions = _template_questions(counted_open)
        fix_first = _template_fix_first(counted_open)
    _link_questions(questions, counted_open)
    # 8a. near-duplicates by code: needs the question ids, so it runs after the questions and before everything that
    # reads the findings (answers, tests, indices, verdict). The skeptic has already looked at both twins.
    info.dup_merged = merge_duplicates(counted_open, questions, fix_first, audit)
    folded = fold_formal(counted_open, questions, fix_first, audit)
    if info.dup_merged or folded is not None:
        open_f = [f for f in findings if f.status == "open"]
        rejected = [f for f in findings if f.status in ("rejected", "merged")]
        counted_open = [f for f in counted_open if f.status == "open"]
    # 8a'. the completeness map follows every gap to the finding that survived; what each answer changes
    try:
        gapcheck.resolve(gap_cells, findings)
    except Exception as e:
        warnings.append(f"Карта полноты не связана с замечаниями: внутренняя ошибка ({type(e).__name__}).")
    # a question is «блокирующий» exactly while it is linked to an open CRITICAL (after the re-check and the merges)
    consistency.settle_questions(questions, counted_open)
    try:
        impact.apply(questions, counted_open, requirements, offline=not model_on, reorder=True, findings=findings)
    except Exception as e:
        warnings.append(f"Влияние ответов на итог не посчитано: внутренняя ошибка ({type(e).__name__}).")
    try:  # a ready assumption for every question, the addressee of the letter, the fingerprint of the question
        clarify.annotate(questions, counted_open, by_model=q_by_model)
    except Exception as e:
        warnings.append(f"Допущения и отпечатки вопросов не добавлены: внутренняя ошибка ({type(e).__name__}).")
    say(f"[8/{STEPS}] Составляю вопросы автору ({'модель' if q_by_model else 'шаблоны правил'})… "
        f"{len(questions)}")

    # 8d is started here, beside 8b and 8c: its request reads only the open findings (id, level, title), which neither
    # the answers nor the tests change. Its audit entries, warnings and line are kept apart and added after 8c, at the
    # place a sequential run writes them, so the report does not depend on who finishes first.
    cl_audit: list[dict] = []
    cl_unchecked: list[str] = []
    cl_lines: list[str] = []
    cl_pool = cl_fut = None
    if model_on and gate.backend.concurrency > 1:  # with one request at a time (LLM_CONCURRENCY=1) it stays in line
        def _checklist_beside() -> list:
            t_cl = time.monotonic()
            gate._tls.sink = cl_audit
            try:
                return _checklist_stage(doc, counted_open, gate, inj, cl_audit, cl_unchecked,
                                        lambda s: cl_lines.append(f"{s} · {int(round(time.monotonic() - t_cl))} с"))
            finally:
                gate._tls.sink = None
        cl_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="revizor-checklist")
        cl_fut = cl_pool.submit(_checklist_beside)

    # 8b. author's answers (optional) ----------------------------------------------------------------
    applied = None
    if answers is not None:
        try:
            applied = _answers_stage(answers, questions, counted_open, requirements, gate if model_on else None, doc, inj,
                                     audit, warnings, unchecked, say, info)
            try:  # the rule that found a finding is run again on its new wording; a closed finding may stay open
                clarify.reopen_in_audit(audit, questions, clarify.fix_checks(counted_open, requirements, doc))
            except Exception as e:
                warnings.append(f"Проверка исправлений не выполнена: внутренняя ошибка ({type(e).__name__}).")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # optional stage: never breaks the run
            warnings.append(f"Ответы автора не применены: внутренняя ошибка ({type(e).__name__}).")
    # 8c. acceptance-test drafts (optional) -----------------------------------------------------------
    tests: list = []
    t_tests = time.monotonic()
    try:
        tests = _tests_stage(requirements, counted_open, questions, coverage, gate if model_on else None, depth, inj,
                             audit, unchecked,
                             lambda s: say(f"{s} · {int(round(time.monotonic() - t_tests))} с"))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        warnings.append(f"Черновики тестов не составлены: внутренняя ошибка ({type(e).__name__}).")
    # 8d. the document's own unfilled review checklist, answered (optional; never changes findings or the verdict) --
    checklist_items: list = []
    try:
        if cl_fut is not None:  # started beside 8b/8c (see above): wait for it, then add what it kept apart
            try:
                checklist_items = cl_fut.result()
            finally:
                audit.extend(cl_audit)
                unchecked.extend(cl_unchecked)
                for s in cl_lines:
                    say(s)
        else:
            t_cl = time.monotonic()
            checklist_items = _checklist_stage(doc, counted_open, gate if model_on else None, inj, audit, unchecked,
                                               lambda s: say(f"{s} · {int(round(time.monotonic() - t_cl))} с"))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        warnings.append(f"Ответы на чек-лист из документа не составлены: внутренняя ошибка ({type(e).__name__}).")
    finally:
        if cl_pool is not None:
            cl_pool.shutdown(wait=False)

    # 9. reviews, indices, verdict ---------------------------------------------------------------------
    _finish_reviews(reviews, requirements, findings, model_on)
    if model_on and requirements:
        pairs = sum(1 for rv in reviews.values() for c in REQ_CRITERIA if rv.criteria.get(c) is not None
                    and rv.criteria[c].status == "unchecked")
        if pairs:
            n_req = sum(1 for rv in reviews.values() if any(v.status == "unchecked" for v in rv.criteria.values()))
            unchecked.append(f"Без оценки модели остались {pairs} из {len(requirements) * len(REQ_CRITERIA)} пар "
                             f"«требование × критерий» (затронуто {count(n_req, W_REQ)}); для них в разделе 3 "
                             "стоит ❔, итог по ним опирается только на правила.")
            audit.append({"type": "decision", "what": "unchecked_pairs", "pairs": pairs, "requirements": n_req})
    clar = score.clarities(requirements, open_f)
    for rid, v in clar.items():
        reviews[rid].clarity = v
    info.score, info.score_formula = score.readiness(clar, open_f)
    code, vtext = verdict.decide(open_f, offline=not model_on)
    if reason:  # written by the model: its counts are replaced by the code's
        reason = verdict.guard_reason(reason, counted_open)
    else:
        reason = _template_reason(counted_open, model_on)
    after_code = after_text = ""
    if applied is not None and applied.answered:
        after_code, after_text = verdict.decide([f for f in open_f if not f.closed_by_answer], offline=not model_on)
    try:  # questions added from the author's answers get their impact too
        impact.apply(questions, counted_open, requirements, offline=not model_on, reorder=False)
    except Exception as e:
        warnings.append(f"Влияние ответов на итог не посчитано: внутренняя ошибка ({type(e).__name__}).")
    say(f"[9/{STEPS}] Считаю индексы и вердикт… {verdict.short_text(code)}")

    if gate is not None:
        if gate.budget_hit:
            warnings.append(f"Достигнут лимит стоимости ${max_cost_usd:g}: дальше модель не вызывалась, оставшиеся "
                            "этапы выполнены без неё.")
        if gate.dead and gate.answered:
            warnings.append(f"Модель перестала отвечать во время прогона: {gate.dead}")
    warnings.extend(unchecked)
    if gate is not None:
        gate.close()
    _usage(info, backend, warnings, audit)
    info.duration_s = round(time.monotonic() - t0, 2)
    info.stage_seconds = clock.seconds()
    if baseline_path:
        try:
            entry, warn = baseline_mod.compare(baseline_path, [f for f in open_f if not f.advisory])
            if warn:
                warnings.append(warn)
            if entry:
                audit.append(entry)
        except Exception as e:
            warnings.append(f"Сравнение с прошлой проверкой не выполнено: внутренняя ошибка ({type(e).__name__}).")

    result = Result(run=info, requirements=requirements, skipped=skipped,
                    reviews=[reviews[r.rid] for r in requirements], findings=sorted(open_f, key=sort_key),
                    rejected=rejected, dropped=dropped, questions=questions, fix_first=fix_first, strengths=strengths,
                    coverage=coverage, verdict=code, verdict_text=vtext, verdict_reason=reason, tests=tests,
                    verdict_after_answers=after_code, verdict_after_answers_text=after_text, audit=audit,
                    checklist=checklist_items, gap_map=gap_cells)
    # the report never contradicts itself: marks, clarity, question priorities and strengths follow the findings;
    # anything the checker still finds goes into the warnings instead of a silently self-contradicting report
    try:  # corrected wordings that lose meaning are withheld on every path that renders cards
        from . import rewrite_gate
        rewrite_gate.apply(result)
    except Exception as e:  # stage boundary: the report is still written, but no rewrite may pass unchecked
        for f in result.findings:
            f.fixed_wording = ""
        warnings.append(f"Проверка предложенных формулировок не выполнена ({type(e).__name__}): они не показаны.")
    try:  # a wording that the gate withheld fixes nothing; a model finding's wording is only checked for meaning
        clarify.after_gate(result.findings)
    except Exception as e:
        warnings.append(f"Проверка исправлений не выполнена: внутренняя ошибка ({type(e).__name__}).")
    consistency.settle(result)
    try:
        problems = consistency.check_consistency(result)
    except Exception as e:  # stage boundary: a bug in the checker must not cost the report
        problems = [f"проверка согласованности не выполнена ({type(e).__name__})"]
    for p in problems:
        warnings.append("Отчёт может противоречить себе: " + p)
    if problems:
        audit.append({"type": "decision", "what": "consistency_violations", "count": len(problems)})
    return result


# --- optional stages: author's answers and test drafts ---------------------------------------------------------


ANSWER_CHUNK = 15


def _answers_stage(provider, questions: list[Question], counted_open: list[Finding], requirements, gate, doc, inj,
                   audit, warnings, unchecked, say, info: RunInfo | None = None):
    parsed = provider(questions)
    warnings.extend(parsed.warnings)
    if info is not None:
        info.answers_sha256 = getattr(parsed, "sha256", "") or ""
    fp = getattr(parsed, "doc_fingerprint", "")
    if fp and doc.sha256 and not doc.sha256.lower().startswith(fp):
        warnings.append("Файл ответов выдан для другой версии документа: документ изменился с тех пор. Ответы с "
                        "отпечатком вопроса применены только к тем вопросам, которые не изменились; остальные "
                        "ответы по номеру могли попасть не на тот вопрос — проверьте их в разделе «Диалог с автором».")
    for qid in getattr(parsed, "stale", {}):
        q = next((x for x in questions if x.qid == qid), None)
        if q is not None:
            q.answer_effect = ("Ответ не применён: вопрос или документ изменились с тех пор, как выдан файл ответов "
                               "(отпечаток вопроса не совпал).")
            audit.append({"type": "decision", "what": "answer_stale", "qid": qid})
    applied = ans_mod.Applied(total=len(questions))
    if not questions:
        if parsed.answers or parsed.unknown:
            warnings.append("В отчёте нет вопросов к автору: ответы не применялись.")
        return None
    if not parsed.answers:
        say("      Ответы автора: в файле нет ни одного ответа — учтено 0")
        audit.append({"type": "answers_summary", "answered": 0, "total": len(questions), "closed": [], "follow_ups": 0,
                      "injection": [], "by_model": False})
        return None
    for qid in list(parsed.answers):
        a = parsed.answers[qid]
        if ans_mod.is_injection(a.raw):
            q = next(x for x in questions if x.qid == qid)
            q.answer = shorten(a.text, 400)
            q.answer_effect = ("Ответ содержит текст, обращённый к проверяющему ИИ; он не выполнен и не учтён, "
                               "замечания остаются открытыми.")
            applied.rejected_injection.append(qid)
            audit.append({"type": "decision", "what": "answer_injection_ignored", "qid": qid})
            del parsed.answers[qid]
    if applied.rejected_injection:
        warnings.append("В ответах автора на " + ", ".join(applied.rejected_injection)
                        + " есть текст, обращённый к проверяющему ИИ. Агент его не выполнял и ответ не учёл.")
    by_fid = {f.fid: f for f in counted_open}
    req_text = {r.rid: r.quote for r in requirements}
    usable = [q for q in questions if q.qid in parsed.answers]
    follow: list[Question] = []
    use_model = gate is not None and not gate.dead and not gate.budget_hit and bool(usable)
    if use_model:
        for q in usable:
            q.answer = ans_mod.shown(parsed.answers[q.qid])
        chunks = [usable[i:i + ANSWER_CHUNK] for i in range(0, len(usable), ANSWER_CHUNK)]
        gate.plan("answers", len(chunks))
        for ch in chunks:
            try:
                blocks = [ans_mod.question_block(q, by_fid, req_text) for q in ch]
                ans = gate.call("answers", answers_user(blocks, answers_tag(doc), inj))
                follow += ans_mod.parse_model(ans, questions, counted_open, requirements, {q.qid for q in ch}, applied, audit)
                applied.by_model = True
            except Exception as e:  # LLMError, StageSkipped or a backend bug: the offline rules take over
                unchecked.append(f"Ответы {ch[0].qid}…{ch[-1].qid} разобраны без модели: она не ответила ({_err(e)}).")
                ans_mod.apply_offline(questions, counted_open, parsed, applied, only={q.qid for q in ch})
    else:
        if usable and gate is not None:
            unchecked.append("Ответы автора разобраны без модели: " + (gate.dead or "достигнут лимит стоимости") + ".")
        ans_mod.apply_offline(questions, counted_open, parsed, applied)
    for fu in follow:
        fu.qid = ans_mod.norm_qid(len(questions) + 1)
        questions.append(fu)
        applied.follow_ups += 1
    if follow:
        try:
            clarify.annotate(follow, counted_open, by_model=True)
        except Exception as e:
            warnings.append(f"Допущения новых вопросов не добавлены: внутренняя ошибка ({type(e).__name__}).")
    for fid in applied.closed:
        audit.append({"type": "decision", "what": "answer_closes", "fid": fid, "qids": applied.closes.get(fid, [])})
    audit.append({"type": "answers_summary", "answered": applied.answered, "total": applied.total,
                  "closed": sorted(set(applied.closed)), "follow_ups": applied.follow_ups,
                  "injection": applied.rejected_injection, "by_model": applied.by_model, "fixed": applied.fixed,
                  "closes": applied.closes, "stale": sorted(getattr(parsed, "stale", {}))})
    say(f"      Ответы автора: учтено {applied.answered} из {applied.total}, закрыто замечаний {len(set(applied.closed))}"
        + (f", новых вопросов {applied.follow_ups}" if applied.follow_ups else ""))
    return applied


def _tests_stage(requirements, counted_open, questions, coverage, gate, depth, inj, audit, unchecked, say):
    if not requirements:
        return []
    det = testgen.deterministic(requirements, counted_open)
    extra: list = []
    if gate is not None and depth == "full" and not gate.dead and not gate.budget_hit:
        gate.plan("tests", 1)
        reqs = [f"{r.rid} | {r.doc_id} | {shorten(r.quote, 400)}" for r in requirements[:150]]
        fl = [f"{f.fid} | {f.severity} | {f.title}" for f in counted_open[:80]]
        ql = [f"{q.qid} | {shorten(q.text, 200)}" for q in questions[:40]]
        cov = [f"{c.acceptance_rid} → " + ", ".join(c.covers) for c in coverage]
        try:
            ans = gate.call("tests", tests_user(reqs, fl, ql, cov, inj))
            extra = testgen.parse_model(ans, requirements, counted_open, questions, audit)
        except Exception as e:
            unchecked.append(f"Черновики тестов: часть, которую составляет модель, не выполнена ({_err(e)}); "
                             "показаны только черновики по правилам.")
    drafts = testgen.finalize(testgen.merge(det, extra), requirements, counted_open, questions, audit)
    ready = sum(1 for t in drafts if t.then and not t.blocked_by)
    say(f"      Черновики тестов: {len(drafts)} (готовы {ready}, ждут ответа автора или описания результата {len(drafts) - ready})")
    return drafts


def _checklist_stage(doc, counted_open, gate, inj, audit, unchecked, say):
    """Detect the document's unfilled review checklist (code) and answer its items (model, else «unknown»)."""
    items = checklist_mod.detect(doc)
    if not items:
        return []
    fids = {f.fid for f in counted_open}
    lines = [f"{f.fid} | {f.severity} | {shorten(f.title, 140)}" for f in counted_open[:150]]
    if gate is None or gate.dead or gate.budget_hit:
        checklist_mod.fill_offline(items)
        if gate is not None:
            unchecked.append("Ответы на чек-лист из документа не составлены: " + (gate.dead or "достигнут лимит стоимости")
                             + "; пункты показаны без оценки.")
    else:
        parts = checklist_mod.chunks(items)
        gate.plan("checklist", len(parts))
        for ch in parts:
            try:
                ans = gate.call("checklist", checklist_user(checklist_mod.payload(ch), lines, inj))
                checklist_mod.apply_model(ans, ch, fids, audit)
            except Exception as e:  # LLMError, StageSkipped or a parsing bug: the items stay, unanswered
                checklist_mod.fill_offline(ch)
                unchecked.append(f"Ответы на пункты чек-листа {ch[0].number}–{ch[-1].number} из документа не получены "
                                 f"({_err(e)}); пункты показаны без оценки.")
    c = checklist_mod.counts(items)
    audit.append({"type": "decision", "what": "checklist_answered", "items": len(items), **c})
    say(f"      Чек-лист из документа: пунктов {len(items)} — да {c['yes']}, нет {c['no']}, "
        f"нельзя определить {c['unknown']}")
    return items


# --- stage helpers ----------------------------------------------------------------------------


def _err(e: BaseException) -> str:
    return getattr(e, "user_message", "") or str(e) or type(e).__name__


def _doc_info(info: RunInfo, doc: SourceDoc) -> None:
    info.doc_name = doc.name
    info.doc_format = doc.fmt
    info.doc_size_bytes = doc.size_bytes
    info.doc_sha256 = doc.sha256
    info.doc_pages = doc.pages
    info.doc_lines = len(doc.lines)
    info.doc_chars = len(doc.text)
    info.doc_words = len(re.findall(r"\w+", doc.text))
    info.masked_values = len(doc.masked)
    info.doc_title = doc_title(doc)


DOC_TITLE_MAX = 120


def doc_title(doc: SourceDoc) -> str:
    """The document's first heading; without headings its first non-empty line; cut to 120 characters."""
    head = next((b.text for b in doc.blocks if b.kind == "heading" and b.text.strip()), "")
    if not head:
        head = next((ln for ln in doc.lines if ln.strip()), "")
        head = re.sub(r"^\s{0,3}#{1,6}\s+", "", head)
    return shorten(head, DOC_TITLE_MAX)


ECONOMY_SLICE_NOTE = {
    "gemini": "Нарезка на требования выполнена без модели: бесплатный уровень Gemini считает запросы, а они нужнее "
              "для проверки.",
    "": "Нарезка на требования выполнена без модели: включён режим экономии запросов (LLM_ECONOMY).",
}


def _slice(doc, gate, inj, h_reqs, h_skipped, warnings, audit, economy: bool = False):
    if gate is None:
        return h_reqs, h_skipped, "эвристика, без модели"
    if economy:  # requests are scarce: the heuristic slicer's result is used as is, no model request
        note = ECONOMY_SLICE_NOTE.get(gate.backend.provider, ECONOMY_SLICE_NOTE[""])
        warnings.append(note)
        audit.append({"type": "decision", "what": "slice_heuristic", "reason": "economy"})
        return h_reqs, h_skipped, "эвристика, без запроса к модели"
    gate.plan("slice", 1)
    try:
        ans = gate.call("slice", slice_user(doc, inj))
    except (LLMError, StageSkipped) as e:
        if not gate.dead:
            warnings.append(f"Нарезка моделью не удалась ({_err(e)}): требования выделены эвристикой.")
            audit.append({"type": "decision", "what": "slice_heuristic", "reason": _err(e)[:300]})
        return h_reqs, h_skipped, "эвристика"
    req_items = [x for x in ans.get("requirements") or [] if isinstance(x, dict)]
    skip_items = [x for x in ans.get("skipped") or [] if isinstance(x, dict)]
    reqs, skipped, missing = slicer.assemble(doc, req_items, skip_items, origin="model")
    if missing:
        gate.plan("slice_rest", 1)
        try:
            ans2 = gate.call("slice_rest", slice_rest_user(doc, missing, inj))
            req_items += [x for x in ans2.get("requirements") or [] if isinstance(x, dict)]
            skip_items += [x for x in ans2.get("skipped") or [] if isinstance(x, dict)]
            reqs, skipped, missing = slicer.assemble(doc, req_items, skip_items, origin="model")
        except (LLMError, StageSkipped) as e:
            audit.append({"type": "decision", "what": "slice_rest_failed", "reason": _err(e)[:300]})
    if missing:
        reqs, skipped = slicer.fill_missing(doc, reqs, skipped, missing)
        warnings.append(f"Модель не распределила {count(len(missing), ('блок', 'блока', 'блоков'))}: "
                        "они распределены эвристикой.")
        audit.append({"type": "decision", "what": "slice_filled_heuristically", "blocks": len(missing)})
    return reqs, skipped, "модель"


# Economy mode: the order in which review and set jobs are SENT, most valuable first (results are still parsed in
# the original job order, so ids and the report do not depend on it).
ECONOMY_ORDER = ("set_consistency", GAPS_STAGE, "review_correctness", "review_completeness", "review_clarity",
                 COMBINED_STAGE, "set_completeness", "set_standards")


def job_stage(job) -> str:
    kind, _b, what = job
    if kind == "gaps":
        return GAPS_STAGE
    if kind == "review":
        return what.stage if what else COMBINED_STAGE
    return what


def job_order(jobs, economy: bool) -> list[int]:
    """Indices of ``jobs`` in sending order: unchanged without economy; by value with it (all batches of a lens
    together, the original order inside a group)."""
    if not economy:
        return list(range(len(jobs)))
    rank = {st: i for i, st in enumerate(ECONOMY_ORDER)}
    return sorted(range(len(jobs)), key=lambda i: (rank.get(job_stage(jobs[i]), len(rank)), i))


def order_jobs(jobs, economy: bool) -> list:
    return [jobs[i] for i in job_order(jobs, economy)]


def _run_jobs(gate: Gate, jobs, rule_findings, requirements, inj, economy: bool = False, done_at: dict | None = None):
    """Run the review and set jobs; results in the ORIGINAL job order. ``done_at`` (optional) receives, per job
    (key ``id(job)``), the monotonic time its last attempt ended: the pipeline reads the span of stages 4 and 5 from it."""
    def one3(job):
        try:
            return one3_inner(job)
        finally:
            if done_at is not None:
                done_at[id(job)] = time.monotonic()

    def one3_inner(job):
        kind, b, what = job
        gate._tls.last_failed = None
        try:
            if kind == "gaps":
                return _gaps_call(gate, what, requirements, inj), "", None
            if kind == "review":
                user = review_user(batch_payload(b), review_hints(rule_findings, b, what), what, inj)
                return gate.call(what.stage if what else COMBINED_STAGE, user), "", None
            user = set_user(what, req_list_payload(requirements), set_hints(rule_findings, what), inj)
            return gate.call(what, user), "", None
        except (LLMError, StageSkipped) as e:
            # a transient failure (overload, timeout, per-minute limit) may be repeated in economy mode
            if isinstance(e, LLMTransient):
                return None, _err(e), (getattr(gate._tls, "last_failed", None) or True)
            return None, _err(e), None
        except Exception as e:  # a bug in parsing must not kill the run
            return None, f"внутренняя ошибка ({type(e).__name__})", None

    def one(job):
        ans, err, _again = one3(job)
        return ans, err

    if economy:
        return _run_jobs_economy(gate, jobs, one3)
    workers = max(1, min(gate.backend.concurrency, len(jobs)))
    if workers == 1:
        return [one(j) for j in jobs]
    # One pool for review lenses and set checks (the set prompts take only the requirement list and rule hints, not
    # review results). Submission order: the long set checks first, so that they do not start in the last wave;
    # results come back in the ORIGINAL job order, so ids, report and JSON do not depend on completion order.
    # (longest first: the critic pass, then the set checks, then the review batches)
    submit = sorted(range(len(jobs)), key=lambda i: ({"gaps": 0, "set": 1}.get(jobs[i][0], 2), i))
    results: list = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {i: ex.submit(one, jobs[i]) for i in submit}
        for i, fu in futs.items():
            results[i] = fu.result()
    return results


def _run_in(gate: Gate, jobs, idxs: list[int], one3) -> dict[int, tuple]:
    """Run ``jobs[i]`` for ``i`` in ``idxs``, sent in that order; results by index."""
    workers = max(1, min(gate.backend.concurrency, len(idxs)))
    if workers == 1:
        return {i: one3(jobs[i]) for i in idxs}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {i: ex.submit(one3, jobs[i]) for i in idxs}
        return {i: fu.result() for i, fu in futs.items()}


def _run_jobs_economy(gate: Gate, jobs, one3) -> list[tuple]:
    """Economy mode: the most valuable steps first; then ONE more round for the steps that got no answer because of
    a transient failure (overload, timeout, per-minute limit), in the same order, while the provider can answer."""
    backend = gate.backend
    order = job_order(jobs, True)
    backend.set_round(1)
    try:
        got = _run_in(gate, jobs, order, one3)
        again = [i for i in order if got[i][2] is not None]
        if again and gate.can_call():
            with gate.lock:
                for i in again:
                    entry = got[i][2]
                    if isinstance(entry, dict):
                        entry["retried"] = True  # superseded by the repeat: not counted as an unanswered step
            for i in again:
                kind, _b, what = jobs[i]
                gate.plan("review" if kind == "review" else GAPS_STAGE if kind == "gaps" else what, 1)
            gate._log({"type": "decision", "what": "second_round", "stages": [job_stage(jobs[i]) for i in again]})
            gate._emit(f"      Повторяю шаги, на которые модель не ответила: {len(again)}")
            backend.set_round(2)
            got.update(_run_in(gate, jobs, again, one3))
    finally:
        backend.set_round(0)
    return [(got[i][0], got[i][1]) for i in range(len(jobs))]


def _gaps_call(gate: Gate, items: list, requirements: list[Requirement], inj: str) -> dict:
    """The critic pass: one call with every item; for the ids that did not come back, ONE follow-up call with only
    those items (like ``slice_rest``). What is still missing stays «unchecked» (gapcheck.apply_model)."""
    reqs = req_list_payload(requirements)
    ans = gate.call(GAPS_STAGE, gaps_user(gapcheck.item_lines(items), reqs, inj))
    got = [x for x in (ans.get("items") or []) if isinstance(x, dict)]
    missing = [it for it in items if it.id not in gapcheck.answered_ids({"items": got})]
    if missing:
        gate.plan(GAPS_STAGE, 1)
        try:
            ans2 = gate.call(GAPS_STAGE, gaps_user(gapcheck.item_lines(missing), reqs, inj))
            got += [x for x in (ans2.get("items") or []) if isinstance(x, dict)]
            gate._log({"type": "decision", "what": "gaps_follow_up", "ids": [it.id for it in missing]})
        except (LLMError, StageSkipped) as e:
            gate._log({"type": "decision", "what": "gaps_follow_up_failed", "reason": _err(e)[:300]})
    return {"items": got}


def _empty_reviews(requirements: list[Requirement], model_on: bool) -> dict[str, RequirementReview]:
    note = "" if model_on else "не проверялось без модели"
    return {r.rid: RequirementReview(rid=r.rid, criteria={c: CriterionVerdict("unchecked", note) for c in REQ_CRITERIA})
            for r in requirements}


def _parse_review(ans, batch, lens, stage, crits, reviews, qi, stats, known, dropped, audit=None):
    rids = {r.rid for r in batch}
    found: list[Finding] = []
    n_raw = 0
    seen: set[str] = set()
    for item in ans.get("reviews") or []:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("rid") or "").strip()
        if rid not in rids or rid in seen:
            continue
        seen.add(rid)
        rv = reviews[rid]
        rv.by_model = True
        crit_ans = item.get("criteria") if isinstance(item.get("criteria"), dict) else {}
        for c in crits:
            v = crit_ans.get(c)
            if isinstance(v, dict):
                st = v.get("status") if v.get("status") in ("ok", "issue", "na") else "ok"
                rv.criteria[c] = CriterionVerdict(st, str(v.get("note") or "")[:300])
            elif isinstance(v, str) and v in ("ok", "issue", "na"):
                rv.criteria[c] = CriterionVerdict(v, "")
            else:
                rv.criteria[c] = CriterionVerdict("unchecked", "модель не вернула оценку")
        for x in item.get("findings") or []:
            if not isinstance(x, dict):
                continue
            n_raw += 1
            f, dq = model_finding(x, scope="requirement", stage=stage, qi=qi, stats=stats, known_rids=known,
                                  rid=rid, allowed=crits, audit=audit)
            if f is not None:
                found.append(f)
            elif dq is not None:
                dropped.append(dq)
    for rid in rids - seen:
        for c in crits:
            reviews[rid].criteria[c] = CriterionVerdict("unchecked", "модель не вернула оценку этого требования")
    return n_raw, found


def _coverage(raw, known: set[str]) -> list[CoverageRow]:
    out = []
    for x in raw or []:
        if not isinstance(x, dict):
            continue
        a = str(x.get("acceptance_rid") or "").strip()
        if a not in known:
            continue
        covers = [c for c in (str(c).strip() for c in x.get("covers") or []) if c in known and c != a]
        out.append(CoverageRow(acceptance_rid=a, covers=covers))
    return out


def _skeptic_line(f: Finding) -> str:
    qs = "; ".join(f"{lines_text(q.line_start, q.line_end)}: «{shorten(q.text, 300)}»" for q in f.quotes[:4])
    sev = f.severity + (f" (основание: {f.blocker_reason})" if f.severity == "CRITICAL" and f.blocker_reason else "")
    return " | ".join([f.fid, f.scope, f.criterion, sev, f.title, shorten(f.problem, 600), qs])


def gate_order(findings: list[Finding]) -> list[Finding]:
    """The findings stage 7 looks at, in document order: open counted ones by their first quote's line, then id."""
    targets = [f for f in findings if f.status == "open" and not f.advisory]
    return sorted(targets, key=lambda f: (f.quotes[0].line_start if f.quotes else 10**9, f.fid))


def gate_chunks(findings: list[Finding], economy: bool = False) -> list[list[Finding]]:
    size = GATE_CHUNK_ECONOMY if economy else GATE_CHUNK
    targets = gate_order(findings)
    return [targets[i:i + size] for i in range(0, len(targets), size)]


def _skeptic(gate, findings, info, inj, audit, unchecked, say, *, qi=None, economy: bool = False):
    """Stage 7: the re-check in small chunks in document order (``apply_gate``), then one dedup call over what is
    left (``apply_dedup``; not in economy mode). A failed call leaves its findings as they were."""
    chunks = gate_chunks(findings, economy)
    targets = [f for ch in chunks for f in ch]
    decisions: list[dict] = []
    failed = 0
    gate.plan("skeptic", len(chunks))

    def one(ch):
        try:
            return gate.call("skeptic", gate_user([_skeptic_line(f) for f in ch], inj)), ""
        except (LLMError, StageSkipped) as e:
            return None, _err(e)

    workers = max(1, min(len(chunks), int(getattr(gate.backend, "concurrency", 1) or 1)))
    if workers > 1:  # the chunks are independent: they run side by side, the answers are read in document order
        with ThreadPoolExecutor(max_workers=workers) as pool:
            answers = list(pool.map(one, chunks))
    else:
        answers = [one(ch) for ch in chunks]
    for ch, (ans, err) in zip(chunks, answers):
        if err:
            failed += 1
            unchecked.append(f"Перепроверка не выполнена для {count(len(ch), W_FINDING)} "
                             f"({ch[0].fid}…{ch[-1].fid}): {err}")
            continue
        decisions += [d for d in ans.get("decisions") or [] if isinstance(d, dict)]
    out = apply_gate(findings, decisions, qi=qi, injection=info.injection_suspected, audit=audit)
    deduped = 0
    rest = gate_order(findings)
    if not economy and len(rest) >= 2 and not gate.dead and not gate.budget_hit:
        dchunks = [rest[i:i + DEDUP_CHUNK] for i in range(0, len(rest), DEDUP_CHUNK)]
        gate.plan("dedup", len(dchunks))
        groups: list[dict] = []

        def one_dedup(ch):
            try:
                return gate.call("dedup", dedup_user([dedup_line(f) for f in ch], inj)), ""
            except (LLMError, StageSkipped) as e:
                return None, _err(e)

        # the chunks are independent (each sees only its own lines): side by side, answers read in chunk order
        dworkers = max(1, min(len(dchunks), int(getattr(gate.backend, "concurrency", 1) or 1)))
        if dworkers > 1:
            with ThreadPoolExecutor(max_workers=dworkers) as pool:
                dans = list(pool.map(one_dedup, dchunks))
        else:
            dans = [one_dedup(ch) for ch in dchunks]
        for ch, (ans, err) in zip(dchunks, dans):
            if err:
                failed += 1
                unchecked.append(f"Поиск повторов при перепроверке не выполнен для {count(len(ch), W_FINDING)} "
                                 f"({ch[0].fid}…{ch[-1].fid}): {err}")
                continue
            groups += [g for g in ans.get("groups") or [] if isinstance(g, dict)]
        deduped = apply_dedup(findings, groups, injection=info.injection_suspected, audit=audit)
    kept = sum(1 for f in targets if f.status == "open" and not f.advisory)
    info.skeptic_kept, info.skeptic_rejected = kept, out.rejected
    info.skeptic_merged, info.skeptic_advisory, info.skeptic_dedup = out.merged + deduped, out.advisory, deduped
    say(f"[7/{STEPS}] Перепроверяю замечания… оставлено {kept}"
        + (f", в наблюдения {out.advisory}" if out.advisory else "")
        + (f", отклонено {out.rejected}" if out.rejected else "")
        + f", объединено {out.merged + deduped}"
        + (f" (из них повторов: {deduped})" if deduped else "")
        + (f", защищено замечаний правил: {out.protected}" if out.protected else "")
        + (f", понижено блокеров без основания: {out.critical_demoted}" if out.critical_demoted else "")
        + (f", не выполнено частей: {failed}" if failed else ""))


def _question_lines(findings: list[Finding]) -> list[str]:
    out = []
    for f in findings:
        draft = ""
        if f.question:
            draft = " | черновик вопроса: " + f.question.text
            if f.question.options:
                draft += " (варианты: " + "; ".join(f.question.options) + ")"
        out.append(" | ".join([f.fid, f.severity, f.criterion, f.title, shorten(f.problem, 500)]) + draft)
    return out


def _parse_questions(ans, findings: list[Finding], known: set[str]):
    fids = {f.fid for f in findings}
    questions = []
    for x in ans.get("questions") or []:
        if not isinstance(x, dict) or not str(x.get("text") or "").strip():
            continue
        opts = [str(o).strip() for o in x.get("options") or [] if str(o).strip()][:4]
        eff = [str(o).strip() for o in x.get("option_effects") or []][:len(opts)]
        questions.append(Question(
            qid="", text=str(x["text"]).strip(), why=str(x.get("why") or "").strip(),
            addressee=x.get("addressee") or "analyst", options=opts, priority=x.get("priority") or "important",
            finding_ids=[i for i in (str(v).strip() for v in x.get("finding_ids") or []) if i in fids],
            option_effects=eff, gap=x.get("gap") if x.get("gap") in GAP_TYPES else "",
            assumption=" ".join(str(x.get("assumption") or "").split())))
    fix = []
    for x in ans.get("fix_first") or []:
        if isinstance(x, dict) and str(x.get("action") or "").strip():
            fix.append(FixAction(action=str(x["action"]).strip(),
                                 finding_ids=[i for i in (str(v).strip() for v in x.get("finding_ids") or []) if i in fids]))
    strengths = []
    for x in ans.get("strengths") or []:
        if isinstance(x, dict) and str(x.get("text") or "").strip():
            strengths.append(Strength(text=str(x["text"]).strip(),
                                      req_ids=[i for i in (str(v).strip() for v in x.get("req_ids") or []) if i in known]))
    return questions, fix, str(ans.get("verdict_reason") or "").strip(), strengths


def _first_sentence(s: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", s or "")
    return (m.group(1) if m else s or "").strip()


def _template_questions(findings: list[Finding]) -> list[Question]:
    """Offline (or when the questions stage failed): one question per distinct rule/reviewer draft."""
    by_key: dict[tuple[str, str], Question] = {}
    order: list[tuple[str, str]] = []
    for f in findings:
        d: QuestionDraft | None = f.question
        if d is None or not d.text.strip():
            continue
        key = (re.sub(r"\s+", " ", d.text.strip().lower()), d.addressee)
        q = by_key.get(key)
        prio = PRIORITY_BY_SEV.get(f.severity, "nice")
        if q is None:
            q = Question(qid="", text=d.text.strip(), why=_first_sentence(f.problem), addressee=d.addressee,
                         options=list(d.options), priority=prio, finding_ids=[f.fid],
                         gap=d.gap or GAP_BY_CRITERION.get(f.criterion, ""), assumption=d.assumption.strip())
            by_key[key] = q
            order.append(key)
        else:
            q.finding_ids.append(f.fid)
            if PRIO_RANK[prio] < PRIO_RANK[q.priority]:
                q.priority = prio
    return [by_key[k] for k in order]


def _template_fix_first(findings: list[Finding]) -> list[FixAction]:
    ranked = sorted(findings, key=lambda f: (SEV_RANK.get(f.severity, 9),) + sort_key(f))
    top = [f for f in ranked if f.severity in ("CRITICAL", "MAJOR")][:7]
    if len(top) < 3:
        top = ranked[:3]
    out: list[FixAction] = []
    by_text: dict[str, FixAction] = {}
    for f in top:  # the same fix for several places is one action with all of them
        text = f"{f.title}. {shorten(f.recommendation, 260)}"
        if text in by_text:
            by_text[text].finding_ids.append(f.fid)
            continue
        by_text[text] = FixAction(action=text, finding_ids=[f.fid])
        out.append(by_text[text])
    return out


def _template_reason(findings: list[Finding], model_on: bool) -> str:
    crit = [f for f in findings if f.severity == "CRITICAL"]
    major = [f for f in findings if f.severity == "MAJOR"]
    if crit:
        titles = "; ".join(shorten(f.title, 90) for f in crit[:3])
        text = (f"Найдено блокеров: {len(crit)} ({titles}). Пока они не устранены, разработчик и тестировщик будут "
                "работать по описанию, которое противоречит себе или общепринятым стандартам, и часть работы придётся "
                "переделывать.")
    elif major:
        text = (f"Блокеров нет, но есть {count(len(major), ('существенное замечание', 'существенных замечания', 'существенных замечаний'))}: "
                "без ответов автора разработчик и тестировщик могут понять эти места по-разному.")
    else:
        text = "Ни блокеров, ни существенных замечаний не найдено."
    if not model_on:
        text += (" Проверка выполнена только автоматическими правилами: смысловые противоречия, пропущенные сценарии "
                 "и пограничные случаи без модели не ищутся.")
    return text


def _link_questions(questions: list[Question], findings: list[Finding]) -> None:
    by_fid = {f.fid: f for f in findings}
    pos = {f.fid: k for k, f in enumerate(findings)}
    questions.sort(key=lambda q: (PRIO_RANK.get(q.priority, 1), min((pos[i] for i in q.finding_ids if i in pos), default=10**6)))
    for k, q in enumerate(questions, 1):
        q.qid = f"В-{k:02d}"
        if q.priority not in PRIO_RANK:
            q.priority = "important"
        rids: list[str] = []
        for fid in q.finding_ids:
            f = by_fid.get(fid)
            if f is None:
                continue
            if not f.question_id:
                f.question_id = q.qid
            if q.quote is None and f.quotes:
                q.quote = f.quotes[0]
            for r in f.req_ids:
                if r not in rids:
                    rids.append(r)
        q.req_ids = rids


def _finish_reviews(reviews: dict[str, RequirementReview], requirements, findings: list[Finding], model_on: bool):
    open_req = [f for f in findings if f.status == "open" and not f.advisory and f.scope == "requirement"]
    for r in requirements:
        rv = reviews[r.rid]
        for c in REQ_CRITERIA:
            hits = [f for f in open_req if f.criterion == c and r.rid in f.req_ids]
            cur = rv.criteria.get(c) or CriterionVerdict("unchecked")
            if hits:
                note = cur.note if cur.status == "issue" and cur.note else hits[0].title
                rv.criteria[c] = CriterionVerdict("issue", note)
            elif cur.status == "issue":  # not confirmed by the quote check or the re-check (counted in the passport)
                rv.criteria[c] = CriterionVerdict("ok", "")


def _usage(info: RunInfo, backend: Backend | None, warnings: list[str], audit: list[dict] | None = None) -> None:
    if backend is None:
        info.provider = "offline"
        info.model = ""
        info.cost_usd = 0.0
        info.cost_note = "модель не вызывалась"
        return
    t = backend.totals
    info.provider = backend.provider
    info.model = backend.model
    info.model_version = str(getattr(backend, "model_version", "") or "")
    info.effort = getattr(backend, "effort_label", None) or backend.effort
    info.llm_calls = backend.calls
    info.llm_cache_hits = backend.cache_hits
    info.input_tokens = t.input_tokens
    info.output_tokens = t.output_tokens
    info.cache_read_tokens = t.cache_read
    info.cache_write_tokens = t.cache_write
    info.cost_usd = cost_usd(backend.provider, backend.model, t)
    info.cost_note = cost_note(backend.provider, backend.model)
    rows = _stage_rows(backend)
    if rows and audit is not None:
        audit.append({"type": "stage_usage", "rows": rows})
    for n in backend.notes:
        if n not in warnings:
            warnings.append(n)
    if any("запасная модель" in n for n in backend.notes):
        warnings.append("Часть ответов дала запасная модель провайдера: фактическая стоимость могла отличаться "
                        "от показанной (вероятно, занижена).")


def _stage_rows(backend: Backend) -> list[dict]:
    """Per-stage usage from the model layer; empty when the layer does not provide it."""
    try:
        from .llm import stage_usage_rows
    except ImportError:
        return []
    try:
        return list(stage_usage_rows(backend))
    except Exception:  # an older backend without stage_usage
        return []
