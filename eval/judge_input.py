#!/usr/bin/env python3
"""Короткий вход для независимого судьи: только открытые замечания и вопросы из JSON-отчёта.

    python eval/judge_input.py reports/report.json -o reports/report.slim.json

Полный JSON-отчёт весит сотни килобайт (нарезка, карта полноты, журнал решений). Судье нужны только замечания:
номер, уровень, заголовок, суть, рекомендация, цитаты со строками, и вопросы автору. Ничего не добавляется и не
пересказывается: поля копируются как есть.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def slim(result: dict) -> dict:
    findings = []
    for f in result.get("findings", []):
        if f.get("status") != "open" or f.get("advisory"):
            continue
        findings.append({
            "fid": f.get("fid"), "severity": f.get("severity"), "criterion": f.get("criterion"), "scope": f.get("scope"),
            "title": f.get("title"), "problem": f.get("problem"), "recommendation": f.get("recommendation"),
            "blocker_reason": f.get("blocker_reason") or None,
            "quotes": [{"line": q.get("line_start"), "text": q.get("text")} for q in f.get("quotes", [])],
        })
    questions = [{"qid": q.get("qid"), "priority": q.get("priority"), "text": q.get("text"),
                  "finding_ids": q.get("finding_ids")} for q in result.get("questions", [])]
    run = result.get("run", {})
    return {"document": run.get("doc_name"), "model": run.get("model"), "findings": findings, "questions": questions,
            "strengths": result.get("strengths", [])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Короткий вход для независимого судьи")
    ap.add_argument("result", help="JSON-отчёт агента")
    ap.add_argument("-o", "--output", required=True, help="куда записать короткий JSON")
    args = ap.parse_args(argv)
    data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    out = slim(data)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8",
                                 newline="\n")
    print(f"Замечаний: {len(out['findings'])}, вопросов: {len(out['questions'])} → {args.output}")
    return 0


if __name__ == "__main__":
    if str(Path(__file__).resolve().parent.parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from revizor.console import prepare_console  # noqa: E402

    prepare_console()
    sys.exit(main())
