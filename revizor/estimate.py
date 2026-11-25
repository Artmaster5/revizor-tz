"""--estimate: build the prompts a real run would send, count calls and tokens, price them. No model calls."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from . import checklist as checklist_mod, loader, rules, slicer
from .llm import Usage
from .llm.base import STAGE_EFFORT, parse_stage_overrides
from .llm.pricing import cost_note, cost_usd
from .model import Diagnostic
from .pipeline import (batch_payload, batches, injection_lines, load_contexts, req_list_payload, review_hints,
                       review_stages, set_hints, _question_lines, _skeptic_line)
from .prompts import (COMBINED_STAGE, SET_STAGES, SKEPTIC_CHUNK, approx_tokens, build_system, checklist_user,
                      injection_note, questions_user, review_user, set_user, skeptic_user, slice_user)
from .textfmt import dec, num, usd

# Output tokens (thinking included) per call = expected JSON size of the answer × a multiplier.
# CALIBRATION 25.11.2026, model claude-haiku-5-5 ONLY (other models think more or less; treat the result as an order
# of magnitude there), with the default efforts of STAGE_EFFORT (requirement review at «low»). Source: a measured run
# on docs/requirements.md (51 requirements by the model's slicing, 5 batches per lens; 59 requirements and 5 batches by
# the heuristic slicing used here), ≈ 268 000 output tokens in all. Measured output of the stage, divided by the JSON
# this module predicts for the same stage:
#   slice 7 764 / 5 900 = 1,3 (low) · review_clarity 43 653 / 15 812 = 2,8 · review_completeness 41 982 / 13 157 = 3,2 ·
#   review_correctness 32 766 / 13 157 = 2,5 (all low) · set_consistency ≈ 53 000 / 2 600 = 20,4 (high, one successful
#   call) · set_completeness 25 215 / 2 900 = 8,7 (medium) · set_standards 23 095 / 2 600 = 8,9 (medium) ·
#   skeptic 22 747 / 4 410 = 5,2 (medium) · questions 7 400 / 4 500 = 1,6 (low) · tests 10 521 / 1 800 = 5,8 (low).
# The combined pass of --depth fast («review», low) was not measured: it takes the mean of the three lenses.
# «answers» and «checklist» (the document's own unfilled review checklist, low) were not measured either. Each factor is valid for the stage's DEFAULT effort (STAGE_EFFORT); for another
# effort it is scaled by the ratio of THINKING_MULT (the ratios are provisional: only the default efforts were measured).
STAGE_MULT = {"slice": 1.3, "slice_rest": 1.3, "review_clarity": 2.8, "review_completeness": 3.2,
              "review_correctness": 2.5, "review": 2.8, "set_consistency": 20.4, "set_completeness": 8.7,
              "set_standards": 8.9, "skeptic": 5.2, "questions": 1.6, "answers": 3.0, "tests": 5.8,
              "checklist": 1.6}
THINKING_MULT = {"low": 1.5, "medium": 3.0, "high": 6.0, "xhigh": 8.0, "max": 10.0}
THINKING_SPREAD = (0.7, 1.4)  # low / high edge of the range around the expected value
# Time. Seconds per output token of ONE request stream: the measured run spent 1 493 s in calls for 351 568 tokens.
SECONDS_PER_OUT_TOKEN = 1493 / 351568
# Wall clock = slice + max(pool, longest call) + skeptic + questions + tests, where all review and set calls share one
# pool of LLM_CONCURRENCY workers: pool = (sum of their seconds) / workers, but never less than the longest single call.
# Skeptic chunks also run in parallel. In the measured run the set checks started in the last wave and set_completeness
# (two attempts, 357 s) held the run after the pool had emptied; the pipeline now submits them first.
DEFAULT_CONCURRENCY = 4
OUT_STAGE_EXTRA = {"answers": 600, "tests": 1800}

# Expected size of the JSON answers, in tokens (rough, from the answer shapes in prompts.py).
OUT_SLICE_REQ = 65
OUT_SLICE_SKIP = 35
OUT_CRITERION = 45
OUT_FINDING = 380
FINDINGS_PER_REQ_LENS = 0.35
FINDINGS_PER_REQ_COMBINED = 0.9
OUT_SET = 2600
FINDINGS_PER_SET_STAGE = 6
OUT_SKEPTIC_DECISION = 45
OUT_QUESTION = 190
OUT_CHECKLIST_ITEM = 70
LINE_CHARS_MODEL_FINDING = 650
LOW_OUT_FACTOR = 0.8
HIGH_OUT_DEFAULT = 1.5  # providers without effort levels: some reasoning on top of the JSON
HIGH_IN_FACTOR = 1.25


def stage_efforts(env: Mapping[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """(locked effort or "", overrides). ``ANTHROPIC_EFFORT`` locks one level; ``ANTHROPIC_EFFORT_STAGES`` overrides."""
    env = env or {}
    locked = (env.get("ANTHROPIC_EFFORT") or "").strip().lower()
    if locked not in THINKING_MULT:
        locked = ""
    over, _bad = parse_stage_overrides(env.get("ANTHROPIC_EFFORT_STAGES") or "")
    return locked, over


def effort_of(stage: str, locked: str, over: Mapping[str, str]) -> str:
    if locked:
        return locked
    for table in (over, STAGE_EFFORT):
        if stage in table:
            return table[stage]
        head = stage.split("_", 1)[0]
        if head in table:
            return table[head]
    return "medium"


@dataclass
class Call:
    stage: str
    user_tokens: int
    out_tokens: int


@dataclass
class Estimate:
    doc_name: str = ""
    lines: int = 0
    requirements: int = 0
    batches: int = 0
    lenses: int = 0
    system_tokens: int = 0
    calls: list[Call] = field(default_factory=list)
    error: str = ""


def build(input_path: str, *, depth: str, contexts: list[str] | None = None) -> Estimate:
    doc = loader.load(input_path)
    if isinstance(doc, Diagnostic):
        return Estimate(error=doc.title)
    warnings: list[str] = []
    system = build_system(doc, load_contexts(contexts or [], warnings))
    reqs, skipped = slicer.heuristic_slice(doc)
    rf = rules.run_rules(doc, reqs, skipped)
    inj = injection_note(injection_lines(rf))
    est = Estimate(doc_name=doc.name, lines=len(doc.lines), requirements=len(reqs),
                   system_tokens=approx_tokens(system))
    calls = est.calls
    calls.append(Call("slice", approx_tokens(slice_user(doc, inj)), OUT_SLICE_REQ * len(reqs) + OUT_SLICE_SKIP * len(skipped)))
    bs = batches(reqs)
    lenses = review_stages(depth)
    est.batches, est.lenses = len(bs), len(lenses)
    model_findings = 0.0
    for b in bs:
        for lens in lenses:
            user = review_user(batch_payload(b), review_hints(rf, b, lens), lens, inj)
            n_crit = len(lens.criteria) if lens else 7
            per = FINDINGS_PER_REQ_LENS if lens else FINDINGS_PER_REQ_COMBINED
            model_findings += per * len(b)
            calls.append(Call(lens.stage if lens else COMBINED_STAGE, approx_tokens(user),
                              int(len(b) * (n_crit * OUT_CRITERION + per * OUT_FINDING))))
    for st in SET_STAGES:
        user = set_user(st, req_list_payload(reqs), set_hints(rf, st), inj)
        calls.append(Call(st, approx_tokens(user), OUT_SET + (300 if st == "set_completeness" else 0)))
        model_findings += FINDINGS_PER_SET_STAGE
    counted_rules = [f for f in rf if not f.advisory]
    for k, f in enumerate(counted_rules, 1):
        f.fid = f"D-{k:03d}"
    total_findings = len(counted_rules) + int(model_findings)
    extra_chars = int(model_findings) * LINE_CHARS_MODEL_FINDING
    if depth == "full" and total_findings:
        rule_lines = [_skeptic_line(f) for f in counted_rules]
        n_chunks = max(1, -(-total_findings // SKEPTIC_CHUNK))
        base = approx_tokens(skeptic_user(rule_lines, inj)) + approx_tokens("x" * extra_chars)
        for _ in range(n_chunks):
            calls.append(Call("skeptic", base // n_chunks + 300, total_findings * OUT_SKEPTIC_DECISION // n_chunks))
    if total_findings:
        user = questions_user(_question_lines(counted_rules), inj)
        calls.append(Call("questions", approx_tokens(user) + approx_tokens("x" * extra_chars),
                          min(20, total_findings) * OUT_QUESTION + 700))
    if depth == "full" and reqs:
        user = "x" * min(40000, 140 * len(reqs))
        calls.append(Call("tests", approx_tokens(user), OUT_STAGE_EXTRA["tests"]))
    items = checklist_mod.detect(doc)
    if items:
        fl = [f"{f.fid} | {f.severity} | {f.title}" for f in counted_rules[:150]]
        extra = approx_tokens("x" * (int(model_findings) * 60))
        for ch in checklist_mod.chunks(items):
            calls.append(Call("checklist", approx_tokens(checklist_user(checklist_mod.payload(ch), fl, inj)) + extra,
                              OUT_CHECKLIST_ITEM * len(ch) + 50))
    return est


def _mult(stage: str, locked: str, over: Mapping[str, str]) -> float:
    default = effort_of(stage, "", {})
    eff = effort_of(stage, locked, over)
    base = STAGE_MULT.get(stage, STAGE_MULT.get(stage.split("_", 1)[0], 3.0))
    return base * THINKING_MULT[eff] / THINKING_MULT[default]


def expected_out(c: Call, locked: str, over: Mapping[str, str]) -> float:
    return c.out_tokens * _mult(c.stage, locked, over)


def output_range(est: Estimate, provider: str, env: Mapping[str, str] | None = None) -> tuple[int, int]:
    """(low, high) output tokens, thinking included. Only providers with effort levels use the multiplier table."""
    out_json = sum(c.out_tokens for c in est.calls)
    if provider != "anthropic":
        return int(out_json * LOW_OUT_FACTOR), int(out_json * HIGH_OUT_DEFAULT)
    locked, over = stage_efforts(env)
    expected = sum(expected_out(c, locked, over) for c in est.calls)
    return int(expected * THINKING_SPREAD[0]), int(expected * THINKING_SPREAD[1])


def expected_seconds(est: Estimate, provider: str, env: Mapping[str, str] | None = None) -> float:
    """Wall-clock seconds by the schedule described above (the expected value, not an edge of the range)."""
    env = env or {}
    try:
        workers = max(1, min(32, int(env.get("LLM_CONCURRENCY") or DEFAULT_CONCURRENCY)))
    except ValueError:
        workers = DEFAULT_CONCURRENCY
    locked, over = stage_efforts(env)
    k = SECONDS_PER_OUT_TOKEN
    if provider == "anthropic":
        sec = lambda c: expected_out(c, locked, over) * k
    else:
        sec = lambda c: c.out_tokens * (LOW_OUT_FACTOR + HIGH_OUT_DEFAULT) / 2 * k
    pool = [sec(c) for c in est.calls if c.stage.startswith(("review", "set_"))]
    skeptic = [sec(c) for c in est.calls if c.stage == "skeptic"]
    serial = sum(sec(c) for c in est.calls if c.stage in ("slice", "slice_rest", "questions", "tests", "checklist"))
    t = serial
    if pool:
        t += max(sum(pool) / min(workers, len(pool)), max(pool))
    if skeptic:
        t += sum(skeptic) / min(workers, len(skeptic))
    return t


def expected_output(est: Estimate, provider: str, env: Mapping[str, str] | None = None) -> int:
    lo, hi = output_range(est, provider, env)
    return int((lo / THINKING_SPREAD[0] + hi / THINKING_SPREAD[1]) / 2)


def price(est: Estimate, provider: str, model: str,
          env: Mapping[str, str] | None = None) -> tuple[float | None, float | None, int, int, int]:
    """(low USD, high USD, input tokens, low output, high output)."""
    n = len(est.calls)
    user_in = sum(c.user_tokens for c in est.calls)
    total_in = user_in + n * est.system_tokens
    out_low, out_high = output_range(est, provider, env)
    # Low: the shared system part is written to the provider's cache once and read by every later call.
    low = Usage(input_tokens=user_in, output_tokens=out_low, cache_write=est.system_tokens,
                cache_read=est.system_tokens * max(0, n - 1))
    # High: caching did not work, inputs 25 % above the estimate, long reasoning.
    high = Usage(input_tokens=int(total_in * HIGH_IN_FACTOR), output_tokens=out_high)
    return cost_usd(provider, model, low), cost_usd(provider, model, high), total_in, out_low, out_high


def _minutes(seconds: float) -> str:
    return f"{max(1, int(round(seconds / 60)))}"


def report(est: Estimate, *, provider: str, model: str, depth: str, provider_note: str = "",
           env: Mapping[str, str] | None = None) -> list[str]:
    if est.error:
        return [f"Оценка невозможна: документ не прочитан ({est.error}). Модель для него не понадобится."]
    low, high, total_in, out_low, out_high = price(est, provider, model, env)
    by_stage: dict[str, list[Call]] = {}
    for c in est.calls:
        by_stage.setdefault(c.stage, []).append(c)
    n_review = sum(len(v) for k, v in by_stage.items() if k.startswith("review"))
    parts = [f"нарезка {len(by_stage.get('slice', []))}",
             f"требования {n_review} ({est.batches} пак. × {est.lenses} {'линзы' if est.lenses > 1 else 'проход'})",
             f"набор {sum(len(by_stage.get(s, [])) for s in SET_STAGES)}"]
    if "skeptic" in by_stage:
        parts.append(f"перепроверка {len(by_stage['skeptic'])}")
    if "questions" in by_stage:
        parts.append(f"вопросы {len(by_stage['questions'])}")
    if "tests" in by_stage:
        parts.append(f"тесты {len(by_stage['tests'])}")
    if "checklist" in by_stage:
        parts.append(f"чек-лист документа {len(by_stage['checklist'])}")
    lines = [
        "Оценка прогона (модель не вызывалась)",
        f"  Документ: {est.doc_name} — {num(est.lines)} строк, ≈ {est.requirements} требований "
        "(эвристическая нарезка как приближение к нарезке моделью)",
        f"  Модель: {provider} / {model or '—'} · глубина: {depth}" + (f" · {provider_note}" if provider_note else ""),
        f"  Вызовов: {len(est.calls)} — " + ", ".join(parts) + "; повторы и исправления формы ответа не учтены",
        f"  Общая системная часть (правила + документ): ≈ {num(est.system_tokens)} токенов в каждом вызове",
        f"  Вход всего: ≈ {num(total_in)} токенов (системная часть {len(est.calls)} × {num(est.system_tokens)} "
        f"+ задания этапов {num(total_in - len(est.calls) * est.system_tokens)})",
        f"  Выход: ≈ {num(out_low)} – {num(out_high)} токенов, считая рассуждения модели; "
        "рассуждения оплачиваются как выход",
        f"  Время: ≈ {_minutes(expected_seconds(est, provider, env) * 0.75)} – "
        f"{_minutes(expected_seconds(est, provider, env) * 1.35)} мин "
        "(по замеру 25.11 на claude-haiku-5-5: ≈ 0,0042 с на токен выхода в одном запросе; все запросы проверки "
        "требований и набора идут в одном пуле, LLM_CONCURRENCY одновременно; у другой модели и в другой день иначе)",
    ]
    if low is None:
        lines.append(f"  Стоимость: неизвестна — {cost_note(provider, model)}")
    else:
        lines.append(f"  Стоимость: ≈ {usd(low)} – {usd(high)} ({cost_note(provider, model)}; нижняя граница — "
                     "системная часть читается из кэша провайдера, верхняя — кэш не сработал)")
    lines.append("  По этапам (вход без системной части / ожидаемый JSON-выход, токены):")
    for st, cs in by_stage.items():
        lines.append(f"    {st}: {len(cs)} × ≈ {num(sum(c.user_tokens for c in cs) // len(cs))} / "
                     f"≈ {num(sum(c.out_tokens for c in cs) // len(cs))}")
    lines.append("  Правило подсчёта: токены ≈ буквы кириллицы ÷ 2,6 + остальные знаки ÷ 3,6. Это приближение "
                 "(±25 %): точные числа сообщает провайдер после прогона. Размер ответов взят из форм ответа "
                 "с типичным числом замечаний; на «грязном» документе замечаний и выхода больше.")
    if high:
        lines.append(f"  Чтобы ограничить расход: --max-cost {max(0.01, round(high * 1.2, 2)):g}")
    return lines


__all__ = ["build", "price", "report", "Estimate", "Call", "dec"]
