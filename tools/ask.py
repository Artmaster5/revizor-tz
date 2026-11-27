"""Чат по отчёту: задать агенту вопрос о проверенном документе и сохранённом отчёте.

    python tools/ask.py reports/report.json --input docs/requirements.md [--provider anthropic] [--model M] ["вопрос"]

С вопросом в командной строке агент отвечает один раз. Без вопроса открывается простой диалог в терминале
(пустая строка или «выход» заканчивает). Модель отвечает только по документу и отчёту. Проверяет ответ программа:
каждая цитата ищется в документе (QuoteIndex, точное совпадение) и печатается со строкой; не найденная в документе
цитата убирается, идентификатор замечания, которого нет в отчёте, убирается так же.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from revizor import loader, report_json  # noqa: E402
from revizor.config import build_env  # noqa: E402
from revizor.llm import LLMError, make_backend  # noqa: E402
from revizor.llm.pricing import cost_usd  # noqa: E402
from revizor.model import Diagnostic, Result  # noqa: E402
from revizor.prompts import build_system  # noqa: E402
from revizor.quotes import QuoteIndex  # noqa: E402

STAGE = "ask"
SCHEMA = {"type": "object", "required": ["answer", "quotes", "findings"], "properties": {
    "answer": {"type": "string", "maxLength": 1500},
    "quotes": {"type": "array"},  # [{"text": ...}]; a bare string is accepted too, verify() handles both
    "findings": {"type": "array", "items": {"type": "string"}}}}

RULES = (
    "Ты отвечаешь на вопрос читателя по проверенному документу и по отчёту о его проверке. "
    "Отвечай только по тексту документа и по отчёту. Если ответа в них нет, прямо скажи: в документе и отчёте этого нет. "
    "Числа (сколько замечаний и блокеров, индекс, вердикт) бери только из строк сводки отчёта, сам не пересчитывай. "
    "Ничего не выдумывай. В поле quotes приведи дословные фрагменты документа, на которых стоит ответ "
    "(не пересказ, не цитаты из отчёта), каждый короткий, одна фраза. В поле findings перечисли номера замечаний отчёта (например D-022), "
    "которые относятся к ответу. Ответ не длиннее 1500 символов, по-русски. "
    'Формат: {"answer": "...", "quotes": [{"text": "..."}], "findings": ["D-001"]}.'
)


def _short(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def report_view(result: Result) -> str:
    """Компактное представление отчёта для модели."""
    out = [f"Вердикт: {result.verdict} — {result.verdict_text}"]
    score = getattr(getattr(result, "readiness", None), "score", None)
    if score is not None:
        out.append(f"Индекс готовности документа: {score}/100")
    open_ = [f for f in result.findings if f.status == "open" and not getattr(f, "advisory", False)]
    crit = [f.fid for f in open_ if f.severity == "CRITICAL"]
    out.append(f"Открытых замечаний: {len(open_)}; из них блокеров (CRITICAL): {len(crit)} — {', '.join(crit)}")
    out.append("")
    out.append("Замечания:")
    for f in result.findings:
        out.append(f"{f.fid} | {f.severity} | {f.status} | {_short(f.title, 150)}")
        out.append(f"  проблема: {_short(f.problem, 350)}")
        for q in f.quotes[:3]:
            out.append(f"  строка {q.line_start}: «{_short(q.text, 160)}»")
    if result.questions:
        out.append("")
        out.append("Вопросы к автору:")
        for q in result.questions:
            out.append(f"{q.qid} [{q.priority}] {_short(q.text, 250)} (замечания: {', '.join(q.finding_ids)})")
    return "\n".join(out)


def user_prompt(result: Result, question: str) -> str:
    return f"{RULES}\n\nОТЧЁТ О ПРОВЕРКЕ\n{report_view(result)}\n\nВОПРОС\n{question}"


def verify(answer: dict, result: Result, qi: QuoteIndex) -> dict:
    """Проверка ответа программой: цитаты по документу, номера замечаний по отчёту."""
    quotes, bad_quotes = [], []
    seen = set()
    for item in answer.get("quotes") or []:
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        q = qi.find(text) if text.strip() else None
        if q is None or not q.exact:
            bad_quotes.append(text)
            continue
        key = (q.line_start, q.line_end)
        if key in seen:
            continue
        seen.add(key)
        quotes.append(q)
    by_id = {f.fid: f for f in result.findings}
    ids, bad_ids = [], []
    for fid in answer.get("findings") or []:
        fid = str(fid).strip()
        if fid in by_id:
            if fid not in ids:
                ids.append(fid)
        else:
            bad_ids.append(fid)
    return {"answer": str(answer.get("answer", "")).strip(), "quotes": quotes, "bad_quotes": bad_quotes,
            "findings": [by_id[i] for i in ids], "bad_ids": bad_ids}


def render(v: dict) -> str:
    lines = ["Ответ:", v["answer"] or "(пусто)", ""]
    lines.append("Цитаты (проверены программой):")
    if not v["quotes"]:
        lines.append("  нет")
    for q in v["quotes"]:
        span = f"{q.line_start}" if q.line_start == q.line_end else f"{q.line_start}–{q.line_end}"
        lines.append(f"  строка {span}: «{_short(q.text, 300)}»")
    for t in v["bad_quotes"]:
        lines.append(f"  «{_short(t, 120)}» — цитата не найдена в документе — убрана")
    lines.append("")
    lines.append("Замечания:")
    if not v["findings"]:
        lines.append("  нет")
    for f in v["findings"]:
        lines.append(f"  {f.fid}: {f.title}")
    for fid in v["bad_ids"]:
        lines.append(f"  {fid} — замечания с таким номером нет в отчёте — убрано")
    return "\n".join(lines)


def ask_once(backend, system: str, result: Result, qi: QuoteIndex, question: str) -> tuple[str, float | None, object]:
    answer, usage = backend.complete_json(system, user_prompt(result, question), schema=SCHEMA,
                                          max_tokens=4000, stage=STAGE)
    cost = cost_usd(backend.provider, backend.model, usage)
    return render(verify(answer, result, qi)), cost, usage


def usage_line(usage, cost) -> str:
    return f"Токены: вход {usage.input_tokens}, выход {usage.output_tokens}" + (f"; ${cost:.4f}" if cost is not None else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Задать вопрос агенту по сохранённому отчёту.")
    ap.add_argument("report", help="JSON-отчёт агента")
    ap.add_argument("--input", required=True, help="проверенный документ (SHA-256 должен совпасть с отчётом)")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "gemini", "openai", "ollama"])
    ap.add_argument("--model", default=None)
    ap.add_argument("question", nargs="?", default=None, help="вопрос; без него откроется диалог")
    args = ap.parse_args(argv)

    result = report_json.load_result(Path(args.report).resolve())
    doc = loader.load(args.input)
    if isinstance(doc, Diagnostic):
        print(f"Документ не прочитан: {doc.title}", file=sys.stderr)
        return 1
    if result.run.doc_sha256 and doc.sha256 != result.run.doc_sha256:
        print("SHA-256 документа не совпадает с отчётом: это другой документ.", file=sys.stderr)
        return 1

    try:
        backend = make_backend(args.provider, build_env(), model=args.model)
    except LLMError as e:
        print(f"Модель недоступна: {e.user_message}", file=sys.stderr)
        return 1
    system = build_system(doc)
    qi = QuoteIndex(doc)
    total_in = total_out = 0
    total_cost = 0.0

    def one(question: str) -> bool:
        nonlocal total_in, total_out, total_cost
        try:
            text, cost, usage = ask_once(backend, system, result, qi, question)
        except LLMError as e:
            print(f"Модель не ответила: {e.user_message}", file=sys.stderr)
            return False
        print(text)
        print("Цитаты и номера замечаний проверены программой по документу и отчёту; непроверенное убрано.")
        print(usage_line(usage, cost))
        total_in += usage.input_tokens
        total_out += usage.output_tokens
        total_cost += cost or 0.0
        return True

    if args.question:
        return 0 if one(args.question) else 1

    print("Вопросы по отчёту. Пустая строка или «выход» — закончить.")
    while True:
        try:
            line = input("\nВопрос> ").strip()
        except EOFError:
            break
        if not line or line.lower() in ("выход", "exit", "quit"):
            break
        one(line)
    return 0


if __name__ == "__main__":
    from revizor.console import prepare_console

    prepare_console()
    raise SystemExit(main())
