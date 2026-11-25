"""Machine-readable twin of the report: the full Result plus a summary with the same counts as the Markdown."""
from __future__ import annotations

import dataclasses
import json
import types
import typing
from pathlib import Path
from typing import Any

from . import answers as ans_mod, baseline as baseline_mod, score
from .merge import STANDARDS
from . import model as model_mod
from .model import REQ_CRITERIA, SET_CRITERIA, Result
from .report_md import advisory, counted, sev_counts, stage_rows, test_totals, verdict_after_blockers

REPORT_FORMAT = "revizor-report/1"


def summary(result: Result) -> dict[str, Any]:
    c = counted(result)
    with_f = {rid for f in c for rid in f.req_ids}
    clar = {rv.rid: rv.clarity for rv in result.reviews if rv.clarity is not None}
    after = verdict_after_blockers(result) if result.diagnostic is None else None
    sm = ans_mod.summary(result.audit)
    bl = baseline_mod.entry(result.audit)
    t_ready, t_rest = test_totals(result)
    return {
        "tests": {"ready": t_ready, "blocked": t_rest},
        "answers": ({"answered": sm.get("answered", 0), "total": sm.get("total", 0), "closed_findings": len(sm.get("closed") or []),
                     "follow_ups": sm.get("follow_ups", 0)} if sm else None),
        "verdict_after_answers": result.verdict_after_answers,
        "baseline": ({"fixed": len(bl["fixed"]), "still_open": len(bl["still_open"]), "new": len(bl["new"])} if bl else None),
        "requirements": len(result.requirements),
        "requirements_with_findings": sum(1 for r in result.requirements if r.rid in with_f),
        "findings": len(c),
        "by_severity": sev_counts(c),
        "by_criterion": {k: sum(1 for f in c if f.criterion == k) for k in REQ_CRITERIA + SET_CRITERIA},
        "observations": len(advisory(result)),
        "questions": len(result.questions),
        "questions_by_priority": {p: sum(1 for q in result.questions if q.priority == p)
                                  for p in ("blocker", "important", "nice")},
        "readiness": result.run.score,
        "readiness_band": score.readiness_band(result.run.score),
        "clarity_bands": dict(score.band_distribution(clar)) if clar else {},
        "verdict": result.verdict,
        "verdict_after_blocker_answers": after[0] if after else "",
        "preliminary": result.run.mode == "offline",
    }


def to_data(result: Result) -> dict[str, Any]:
    data = result.to_dict()
    rows = stage_rows(result)
    if rows and isinstance(data.get("run"), dict):
        data["run"]["stage_usage"] = rows
    for f in data.get("findings", []) + data.get("rejected", []):
        f["standard"] = STANDARDS.get(f.get("criterion", ""), "")
    return {"format": REPORT_FORMAT, "summary": summary(result), **data}


def render(result: Result) -> str:
    return json.dumps(to_data(result), ensure_ascii=False, indent=2) + "\n"


# --- reading a saved report back ---------------------------------------------------------------------------


def _build(tp: Any, value: Any) -> Any:
    """``value`` (parsed JSON) as an instance of the type ``tp``; unknown keys are ignored, missing ones default."""
    if value is None:
        return None
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _build(args[0], value) if len(args) == 1 else value
    if origin is list:
        (inner,) = typing.get_args(tp) or (Any,)
        return [_build(inner, v) for v in value] if isinstance(value, list) else []
    if origin is dict:
        args = typing.get_args(tp)
        inner = args[1] if len(args) == 2 else Any
        return {k: _build(inner, v) for k, v in value.items()} if isinstance(value, dict) else {}
    if dataclasses.is_dataclass(tp) and isinstance(value, dict):
        hints = typing.get_type_hints(tp, vars(model_mod))
        kwargs = {}
        for f in dataclasses.fields(tp):
            if f.name in value:
                kwargs[f.name] = _build(hints.get(f.name, Any), value[f.name])
            elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                kwargs[f.name] = "" if hints.get(f.name) is str else None
        return tp(**kwargs)
    if tp is float and isinstance(value, int):
        return float(value)
    return value


def from_data(data: dict[str, Any]) -> Result:
    """A ``Result`` from the dictionary ``to_data`` wrote. Tolerant to older files: fields they lack get defaults,
    fields this version does not know (``summary``, ``standard``, ``stage_usage``) are ignored."""
    if not isinstance(data, dict) or not isinstance(data.get("run"), dict):
        raise ValueError("это не JSON-отчёт Ревизора ТЗ: нет поля run")
    return _build(Result, data)


def load_result(path: str | Path) -> Result:
    """Read a JSON report written by the agent back into the dataclasses (no model, no network)."""
    return from_data(json.loads(Path(path).read_text(encoding="utf-8")))
