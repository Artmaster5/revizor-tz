"""Re-render a saved JSON report with the current layout code. No model, no network.

    python tools/rerender.py reports/report.json -o /tmp/out/report.md [--as-is] [--no-side-files]

Loads the JSON back into the dataclasses (``report_json.load_result``), brings an older result in line with the
current rules (``remerge``: duplicate merges and the fold of formal remarks; ``consistency.settle``: marks,
clarity, indices, verdict, question priority, strengths, level trail), runs the rewrite gate, and writes the Markdown report with the usual side files
(questionnaire, rewrites, tests, CSV for a spreadsheet and for Jira, details of the review, summary, run details,
HTML). The passport of the re-rendered report says that the model was not called.
``--as-is`` skips ``settle`` and shows the saved numbers unchanged. The source JSON is never modified.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from revizor import consistency, exports, merge, report_json, report_md, rewrite_gate, sidefiles  # noqa: E402
from revizor.model import Result  # noqa: E402


def remerge(result: Result) -> int:
    """Apply the current merge rules (``merge_duplicates``, ``fold_formal``) to a result written by older code.
    Absorbed findings move to ``result.rejected``. Returns how many findings were absorbed."""
    if result.diagnostic is not None:
        return 0
    merge.merge_duplicates(result.findings, result.questions, result.fix_first, result.audit)
    merge.fold_formal(result.findings, result.questions, result.fix_first, result.audit)
    gone = [f for f in result.findings if f.status != "open"]
    if gone:
        result.findings = [f for f in result.findings if f.status == "open"]
        result.rejected += gone
        left = {f.fid for f in result.findings}
        for t in result.tests:
            t.finding_ids = [i for i in t.finding_ids if i in left]
    return len(gone)


def prepare(result: Result, *, as_is: bool = False) -> Result:
    if not as_is:
        remerge(result)
        consistency.settle(result)
    rewrite_gate.apply(result)  # safe to call twice: an already withheld rewrite has no wording left
    if not as_is:
        problems = consistency.check_consistency(result)
        if problems:
            result.run.warnings.append("Отчёт может противоречить себе: " + "; ".join(problems[:5]))
    return result


def files_for(result: Result, out: Path, *, side_files: bool = True) -> list[tuple[Path, str]]:
    md_text = report_md.render(result, out.name, rerendered=True)
    files = [(out, md_text)]
    if not side_files or result.diagnostic is not None:
        return files
    if result.questions:
        qpath = out.with_name(out.stem + ".questions.md")
        answered = out.with_name(out.stem + "_answered" + out.suffix)  # the rerun must not overwrite this report
        files.append((qpath, sidefiles.questions_md(result, input_path=result.run.doc_name, output_path=str(answered),
                                                    questions_path=str(qpath))))
    for suffix, build in ((".fixed.md", sidefiles.fixed_md), (".tests.md", sidefiles.tests_md),
                          (".findings.csv", sidefiles.findings_csv)):
        text = build(result)
        if text:
            files.append((out.with_name(out.stem + suffix), text))
    files += exports.extra_files(result, out, md_text)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Пересобрать отчёт из сохранённого JSON без модели и сети.")
    ap.add_argument("report", help="JSON-отчёт агента")
    ap.add_argument("-o", "--output", required=True, help="куда записать Markdown (side-файлы — рядом)")
    ap.add_argument("--as-is", action="store_true", help="не пересчитывать отметки, индексы и вердикт")
    ap.add_argument("--no-side-files", action="store_true", help="записать только Markdown")
    args = ap.parse_args(argv)
    src, out = Path(args.report).resolve(), Path(args.output).resolve()
    if out == src or out.with_suffix(".json") == src:
        print("Выходной файл совпадает с исходным JSON: укажите другой --output.", file=sys.stderr)
        return 2
    try:
        result = report_json.load_result(src)
    except (OSError, ValueError) as e:
        print(f"Не удалось прочитать «{src}»: {e}", file=sys.stderr)
        return 1
    prepare(result, as_is=args.as_is)
    out.parent.mkdir(parents=True, exist_ok=True)
    for path, text in files_for(result, out, side_files=not args.no_side_files):
        path.write_text(text, encoding="utf-8", newline="" if path.suffix == ".csv" else None)
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
