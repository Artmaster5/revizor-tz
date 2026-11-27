"""Add «примеры-свидетели» to the contradictions of a saved report and write the report again.

    python tools/witness.py reports/report.json --input docs/requirements.md [--also reports/report_answered.json]
        [--provider anthropic] [--model claude-opus-5-5]

One model call builds a concrete case for each open contradiction (``revizor/witness.py``); the program keeps a case
only when both quotes are found in the document word for word. The findings, their levels, the indices and the verdict
are not touched: the report is written again from the saved result with the witnesses added (the JSON in place, the
Markdown and the side files next to it). ``--also`` applies the same checked witnesses to another report of the same
document (for example the one after the author's answers) without a second call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from revizor import loader, report_json, witness  # noqa: E402
from revizor.config import build_env  # noqa: E402
from revizor.llm import LLMError, make_backend  # noqa: E402
from revizor.llm.pricing import cost_usd  # noqa: E402
from revizor.model import Diagnostic, Result  # noqa: E402
from revizor.pipeline import injection_lines  # noqa: E402
from revizor.prompts import build_system, injection_note  # noqa: E402
from revizor.quotes import QuoteIndex  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rerender  # noqa: E402


def write(result: Result, src: Path) -> list[Path]:
    """The JSON in place, then the Markdown report and its side files next to it (layout line unchanged)."""
    out = src.with_suffix(".md")
    paths = [src]
    src.write_text(report_json.render(result), encoding="utf-8", newline="")
    for path, text in rerender.files_for(result, out, rerendered=False):
        if path.name.endswith(".questions.md"):
            continue  # a witness does not change the questions; the file keeps the run's own relative paths
        path.write_text(text, encoding="utf-8", newline="")
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Добавить примеры-свидетели к противоречиям сохранённого отчёта.")
    ap.add_argument("report", help="JSON-отчёт агента")
    ap.add_argument("--input", required=True, help="проверенный документ (SHA-256 должен совпасть с отчётом)")
    ap.add_argument("--also", action="append", default=[], help="ещё один JSON-отчёт того же документа")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)

    src = Path(args.report).resolve()
    result = report_json.load_result(src)
    doc = loader.load(args.input)
    if isinstance(doc, Diagnostic):
        print(f"Документ не прочитан: {doc.title}", file=sys.stderr)
        return 1
    if result.run.doc_sha256 and doc.sha256 != result.run.doc_sha256:
        print("SHA-256 документа не совпадает с отчётом: это другой документ.", file=sys.stderr)
        return 1
    items = witness.targets(result.findings)
    if not items:
        print("Противоречий в отчёте нет.")
        return 0

    env = build_env()
    backend = make_backend(args.provider, env, model=args.model)
    inj = injection_note(injection_lines(result.findings))
    try:
        answer, usage = backend.complete_json(build_system(doc), witness.user_prompt(items, inj),
                                              schema=witness.SCHEMA, max_tokens=16000, stage=witness.STAGE)
    except LLMError as e:
        print(f"Модель не ответила: {e.user_message}", file=sys.stderr)
        return 1
    qi = QuoteIndex(doc)
    kept, dropped = witness.check(answer, items, qi, model=backend.model)
    print(f"Противоречий: {len(items)}; примеров принято: {len(kept)}; отброшено: {len(dropped)}")
    for d in dropped:
        print(f"  отброшен {d['fid'] or '?'}: {d['reason']}")
    for fid, w in kept.items():
        print(f"  {fid}: {w['case'][:150]}")
    cost = cost_usd(backend.provider, backend.model, usage)
    print(f"Токены: вход {usage.input_tokens}, выход {usage.output_tokens}" + (f"; ${cost:.4f}" if cost is not None else ""))

    reports = [(src, result)] + [(Path(p).resolve(), report_json.load_result(p)) for p in args.also]
    for path, res in reports:
        if res.run.doc_sha256 and res.run.doc_sha256 != doc.sha256:
            print(f"{path.name}: другой документ, пропущен.", file=sys.stderr)
            continue
        mine = {f.fid: f for f in witness.targets(res.findings)}
        same = {fid: w for fid, w in kept.items() if fid in mine and mine[fid].title == next(
            (f.title for f in items if f.fid == fid), None)}
        n = witness.apply(res.findings, same)
        res.audit.append({"type": "decision", "what": "witness", "kept": sorted(same), "dropped": dropped,
                          "model": backend.model, "cost_usd": round(cost, 4) if cost is not None else None})
        for p in write(res, path):
            print(p)
        print(f"{path.name}: примеров добавлено {n}")
    return 0


if __name__ == "__main__":
    from revizor.console import prepare_console

    prepare_console()
    raise SystemExit(main())
