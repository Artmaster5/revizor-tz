"""Machine-readable twin of the report: the full Result plus a summary with the same counts as the Markdown."""
from __future__ import annotations

import json
from typing import Any

from . import answers as ans_mod, baseline as baseline_mod, score
from .merge import STANDARDS
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
