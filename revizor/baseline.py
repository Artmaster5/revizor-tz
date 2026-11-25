"""Comparison with a previous JSON report of the same document (``--baseline OLD.json``).

Matching: two findings are the same when they share a rule id or a criterion AND their quotes overlap (normalised
text, containment or at least half of the words). Never raises: a broken or foreign file gives a warning.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .model import Finding
from .textfmt import shorten

MAX_BASELINE_BYTES = 30_000_000
FORMAT_PREFIX = "revizor-report/"


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", " ", (s or "").casefold().replace("ё", "е")).strip()


def _words(s: str) -> set[str]:
    return {w for w in s.split() if len(w) > 2}


def _overlap(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    wa, wb = _words(na), _words(nb)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.5


def _old_quotes(f: dict[str, Any]) -> list[str]:
    return [str(q.get("text") or "") for q in f.get("quotes") or [] if isinstance(q, dict)]


def _same(old: dict[str, Any], new: Finding) -> bool:
    shared_rule = bool(set(old.get("rule_ids") or []) & set(new.rule_ids))
    if not (shared_rule or (old.get("criterion") and old.get("criterion") == new.criterion)):
        return False
    return any(_overlap(a, q.text) for a in _old_quotes(old) for q in new.quotes)


def _brief(f: dict[str, Any]) -> dict[str, str]:
    q = _old_quotes(f)
    return {"fid": str(f.get("fid") or ""), "severity": str(f.get("severity") or ""), "title": str(f.get("title") or "")[:160],
            "quote": shorten(q[0], 160) if q else ""}


def compare(path: str, current: list[Finding]) -> tuple[dict[str, Any] | None, str]:
    """(audit entry, warning). ``current`` — open counted findings of this run."""
    p = Path(path)
    try:
        if p.stat().st_size > MAX_BASELINE_BYTES:
            return None, f"Файл для сравнения «{p.name}» слишком большой: сравнение не выполнено."
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"Файл для сравнения «{p.name}» не прочитан ({type(e).__name__}): сравнение не выполнено."
    if not isinstance(data, dict) or not str(data.get("format") or "").startswith(FORMAT_PREFIX) \
            or not isinstance(data.get("findings"), list):
        return None, (f"Файл «{p.name}» не похож на JSON-отчёт Ревизора ТЗ (нет поля format и списка findings): "
                      "сравнение не выполнено.")
    old = [f for f in data["findings"] if isinstance(f, dict) and f.get("status", "open") == "open" and not f.get("advisory")]
    used: set[str] = set()
    fixed, still = [], []
    for o in old:
        hit = next((n for n in current if n.fid not in used and _same(o, n)), None)
        if hit is None:
            fixed.append(_brief(o))
        else:
            used.add(hit.fid)
            still.append({**_brief(o), "new_fid": hit.fid, "severity": hit.severity, "title": hit.title[:160]})
    new = [{"fid": n.fid, "severity": n.severity, "title": n.title[:160],
            "quote": shorten(n.quotes[0].text, 160) if n.quotes else ""} for n in current if n.fid not in used]
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    return ({"type": "baseline", "file": p.name, "old_doc": str(run.get("doc_name") or ""),
             "old_when": str(run.get("started_at") or ""), "old_findings": len(old),
             "fixed": fixed, "still_open": still, "new": new}, "")


def entry(audit: list[dict]) -> dict[str, Any] | None:
    for a in audit:
        if a.get("type") == "baseline":
            return a
    return None
