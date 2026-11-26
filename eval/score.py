#!/usr/bin/env python3
"""Оценка одного результата «Ревизора ТЗ» по ключу дефектов.

    python eval/score.py RESULT.json --key KEY.json [--json] [--min-strict 0.5] [--min-organisers 0.8] [--no-absorbed]

Правила сопоставления (всё считается кодом, без участия модели):

* Текст и цитаты замечаний, слитых в карточку (поле absorbed, «Сюда же»), считаются частью этой карточки: читатель
  видит их на ней. Ключ --no-absorbed возвращает прежний счёт (только собственный текст и цитаты замечания).
* Берутся только открытые замечания (status == "open"), не «наблюдения» (advisory == false).
  Отклонённые, слитые и «наблюдения» не учитываются ни как находки, ни как ложные тревоги.
* «Место» совпало, если у замечания есть цитата, которая
  (а) пересекается с якорем дефекта по тексту после normalize() из revizor.quotes:
      одна строка содержится в другой (короче не менее 12 знаков) ИЛИ
      не менее 60% слов якоря встречаются в цитате (порядок не важен), и
  (б) делит с якорем хотя бы одну строку документа (номера строк цитаты сверяются
      со строками, где якорь реально стоит в файле). Условие (б) требуется всегда:
      это строже, чем «подстрока», и не даёт короткому якорю совпасть со словом из другого места.
  Цитаты длиннее 600 знаков после нормализации считаются «слишком широкими» и не засчитываются.
* НАЙДЕН (строго): место совпало И каждая группа ключевых слов дала попадание
  (регулярное выражение, поиск в title + problem + recommendation, нижний регистр; текст проверяется как есть и с ё→е).
* НАЙДЕН (мягко): совпало только место.
* Ложная тревога: открытое замечание CRITICAL/MAJOR, чья цитата пересекается с якорем
  списка «good» (так же, как выше) и которое при этом НЕ подтверждает строго ни один дефект ключа.
  Такие замечания, подтверждающие настоящий дефект на той же строке, выводятся отдельно
  как «спорные, не считаются» (ключ строили так, что хорошее и плохое иногда на одной строке).
* Без совпадений: открытые замечания, не пересёкшиеся ни с одним якорем дефекта и не ставшие
  ложной тревогой, — для ручного разбора.

Код выхода 0 всегда; 2 — если не достигнут порог --min-strict / --min-organisers;
1 — неверные входные данные (нет файла, не тот документ, битый ключ).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from revizor.quotes import normalize  # noqa: E402

SEV_RANK = {"MINOR": 1, "MAJOR": 2, "CRITICAL": 3}
SEVERITIES = ("CRITICAL", "MAJOR", "MINOR")
MIN_SUBSTRING = 12
WORD_SHARE = 0.60
MAX_QUOTE_CHARS = 600
_WORD = re.compile(r"\w+", re.UNICODE)


class ScoreError(Exception):
    """Входные данные непригодны для оценки (сообщение — по-русски)."""


# --- чтение входов ----------------------------------------------------------


def read_document_text(path: Path) -> str:
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_key(path: Path) -> tuple[dict, Path]:
    path = Path(path)
    try:
        key = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScoreError(f"Ключ {path} не читается: {exc}") from exc
    if not isinstance(key, dict) or "defects" not in key or "document" not in key:
        raise ScoreError(f"Ключ {path}: нет полей document и defects")
    doc_path = (path.parent / key["document"]).resolve()
    return key, doc_path


# --- сопоставление ----------------------------------------------------------


def _words(norm: str) -> set[str]:
    return set(_WORD.findall(norm))


class Anchor:
    """Якорь ключа: нормализованный текст, слова и строки, где он стоит в документе."""

    def __init__(self, text: str, doc_text: str):
        self.text = text
        self.norm = normalize(text)
        self.words = _words(self.norm)
        self.ranges: list[tuple[int, int]] = []
        start = 0
        while True:
            pos = doc_text.find(text, start)
            if pos < 0:
                break
            first = doc_text.count("\n", 0, pos) + 1
            self.ranges.append((first, first + text.count("\n")))
            start = pos + 1
        if not self.ranges:
            raise ScoreError(f"Якорь не найден в документе дословно: «{text[:60]}». Запустите check_keys.py.")


def quote_range(q: dict) -> tuple[int, int]:
    s = int(q.get("line_start") or 0)
    e = int(q.get("line_end") or s)
    return s, max(s, e)


def quote_matches(quote: dict, anchor: Anchor) -> str:
    """'' если не пересекается; иначе 'подстрока' или 'слова'."""
    s, e = quote_range(quote)
    if s <= 0:
        return ""
    if not any(s <= ae and as_ <= e for as_, ae in anchor.ranges):
        return ""
    qn = normalize(quote.get("text", ""))
    if not qn or len(qn) > MAX_QUOTE_CHARS or not anchor.norm:
        return ""
    short, long_ = (qn, anchor.norm) if len(qn) <= len(anchor.norm) else (anchor.norm, qn)
    if len(short) >= MIN_SUBSTRING and short in long_:
        return "подстрока"
    if anchor.words:
        share = len(anchor.words & _words(qn)) / len(anchor.words)
        if share >= WORD_SHARE:
            return "слова"
    return ""


def finding_text(f: dict) -> str:
    raw = "\n".join(str(f.get(k) or "") for k in ("title", "problem", "recommendation"))
    return raw.lower()


def keywords_hit(f: dict, groups: list[list[str]]) -> bool:
    """Каждая группа должна дать попадание; ищем и в тексте как есть, и в тексте с ё→е
    (в слепых ключах выражения написаны то с «ё», то с «е»)."""
    text = finding_text(f)
    folded = text.replace("ё", "е")
    for group in groups:
        if not any(re.search(rx, text) or re.search(rx, folded) for rx in group):
            return False
    return True


def with_absorbed(f: dict) -> dict:
    """The finding as the reader sees its card: the title, the problem and the quotes of every finding merged into it
    («Сюда же») count as part of it. A report of the older format has no ``absorbed`` and stays as it is."""
    items = [a for a in (f.get("absorbed") or []) if isinstance(a, dict)]
    if not items:
        return f
    view = dict(f)
    view["problem"] = "\n".join([str(f.get("problem") or "")] + [
        f"{a.get('title') or ''}\n{a.get('problem') or ''}" for a in items])
    quotes = list(f.get("quotes") or [])
    for a in items:
        for q in a.get("quotes") or []:
            if not isinstance(q, dict):
                continue
            line = int(q.get("line") or q.get("line_start") or 0)
            quotes.append({"line_start": line, "line_end": int(q.get("line_end") or line), "text": q.get("text", "")})
    view["quotes"] = quotes
    return view


def is_counted(f: dict) -> bool:
    return f.get("status", "open") == "open" and not f.get("advisory", False)


def finding_matches_anchors(f: dict, anchors: list[Anchor]) -> bool:
    return any(quote_matches(q, a) for q in f.get("quotes") or [] for a in anchors)


def _share(found: int, total: int) -> float | None:
    return None if total == 0 else found / total


def _first_line(f: dict) -> int:
    qs = f.get("quotes") or []
    return quote_range(qs[0])[0] if qs else 0


def _brief(f: dict) -> dict:
    return {
        "fid": f.get("fid", ""),
        "severity": f.get("severity", ""),
        "title": f.get("title", ""),
        "line": _first_line(f),
    }


def score_result(result: dict, key: dict, doc_text: str, use_absorbed: bool = True) -> dict:
    """Чистая функция: результат + ключ + текст документа → словарь с оценками.

    ``use_absorbed``: при сопоставлении дефекта с замечанием текст и цитаты слитых в него замечаний считаются частью
    замечания (читатель видит их на карточке). Ложные тревоги считаются по собственным цитатам замечания."""
    own = [f for f in (result.get("findings") or []) if is_counted(f)]
    findings = [with_absorbed(f) for f in own] if use_absorbed else own
    advisory = sum(1 for f in (result.get("findings") or []) if f.get("advisory"))
    defects = key["defects"]

    d_anchors = {d["id"]: [Anchor(a, doc_text) for a in d["anchors"]] for d in defects}
    g_anchors = [(g, Anchor(g["anchor"], doc_text)) for g in key.get("good") or []]

    rows = []
    matched_any: set[int] = set()
    strict_of_finding: dict[int, list[str]] = {}
    for d in defects:
        loose_idx = [i for i, f in enumerate(findings) if finding_matches_anchors(f, d_anchors[d["id"]])]
        strict_idx = [i for i in loose_idx if keywords_hit(findings[i], d["keywords"])]
        matched_any.update(loose_idx)
        for i in strict_idx:
            strict_of_finding.setdefault(i, []).append(d["id"])
        tool_sev = ""
        agreement = ""
        if strict_idx:
            tool_sev = max((findings[i].get("severity", "") for i in strict_idx), key=lambda s: SEV_RANK.get(s, 0))
            a, b = SEV_RANK.get(tool_sev, 0), SEV_RANK[d["severity"]]
            agreement = "same" if a == b else ("tool_higher" if a > b else "tool_lower")
        rows.append({
            "id": d["id"],
            "severity": d["severity"],
            "origin": d.get("origin", ""),
            "title": d.get("title", ""),
            "strict": bool(strict_idx),
            "loose": bool(loose_idx),
            "status": "strict" if strict_idx else ("loose" if loose_idx else "missed"),
            "strict_fids": [findings[i].get("fid", "") for i in strict_idx],
            "loose_fids": [findings[i].get("fid", "") for i in loose_idx],
            "tool_severity": tool_sev,
            "severity_agreement": agreement,
        })

    def tally(sel: list[dict]) -> dict:
        n = len(sel)
        s = sum(1 for r in sel if r["strict"])
        lo = sum(1 for r in sel if r["loose"])
        return {"total": n, "strict": s, "loose": lo, "strict_share": _share(s, n), "loose_share": _share(lo, n)}

    recall = {
        "overall": tally(rows),
        "by_severity": {sev: tally([r for r in rows if r["severity"] == sev]) for sev in SEVERITIES},
        "by_origin": {},
    }
    for origin in sorted({r["origin"] for r in rows if r["origin"]}):
        recall["by_origin"][origin] = tally([r for r in rows if r["origin"] == origin])

    false_alarms, exempt = [], []
    in_false: set[int] = set()
    for i, f in enumerate(own):
        if f.get("severity") not in ("CRITICAL", "MAJOR"):
            continue
        hit = [g for g, ga in g_anchors if any(quote_matches(q, ga) for q in f.get("quotes") or [])]
        if not hit:
            continue
        item = _brief(f)
        item["good_anchor"] = hit[0]["anchor"]
        if i in strict_of_finding:
            item["confirms"] = strict_of_finding[i]
            exempt.append(item)
        else:
            false_alarms.append(item)
            in_false.add(i)

    unmatched = [_brief(f) for i, f in enumerate(own) if i not in matched_any and i not in in_false]

    agreement = {"same": 0, "tool_higher": 0, "tool_lower": 0}
    for r in rows:
        if r["severity_agreement"]:
            agreement[r["severity_agreement"]] += 1

    run = result.get("run") or {}
    return {
        "document": key.get("document", ""),
        "run": {
            "provider": run.get("provider", ""),
            "model": run.get("model", ""),
            "mode": run.get("mode", ""),
            "cost_usd": run.get("cost_usd"),
            "cost_note": run.get("cost_note", ""),
            "duration_s": run.get("duration_s"),
            "doc_name": run.get("doc_name", ""),
        },
        "options": {"absorbed": use_absorbed},
        "counts": {
            "requirements": len(result.get("requirements") or []),
            "findings_open": len(findings),
            "advisory": advisory,
        },
        "recall": recall,
        "severity_agreement": agreement,
        "defects": rows,
        "false_alarms": false_alarms,
        "false_alarms_exempt": exempt,
        "unmatched": unmatched,
        "warnings": [],
    }


def score_files(result_path: Path, key_path: Path, force: bool = False, use_absorbed: bool = True) -> dict:
    key, doc_path = load_key(key_path)
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScoreError(f"Результат {result_path} не читается: {exc}") from exc
    if not isinstance(result, dict) or "findings" not in result:
        raise ScoreError(f"Результат {result_path}: нет списка findings (это не JSON-двойник отчёта)")
    try:
        doc_text = read_document_text(doc_path)
    except OSError as exc:
        raise ScoreError(f"Документ {doc_path} не читается: {exc}") from exc
    warnings: list[str] = []
    run_doc = (result.get("run") or {}).get("doc_name", "")
    if run_doc and run_doc != doc_path.name:
        msg = f"Результат получен на документе «{run_doc}», а ключ относится к «{doc_path.name}»"
        if not force:
            raise ScoreError(msg + ". Проверьте пару файлов (или --force).")
        warnings.append(msg)
    if result.get("diagnostic"):
        warnings.append("В результате есть диагностика: документ не был проверен, оценка бессмысленна")
    out = score_result(result, key, doc_text, use_absorbed=use_absorbed)
    out["warnings"] = warnings
    out["result_file"] = str(result_path)
    out["key_file"] = str(key_path)
    return out


# --- вывод ------------------------------------------------------------------


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _tally_line(label: str, t: dict) -> str:
    return (f"  {label:<18} строго {t['strict']}/{t['total']} ({pct(t['strict_share'])}),"
            f" мягко {t['loose']}/{t['total']} ({pct(t['loose_share'])})")


def render_text(rep: dict) -> str:
    L = []
    run = rep["run"]
    L.append(f"Оценка: {rep['document']}")
    L.append(f"Запуск: провайдер {run['provider'] or '—'}, модель {run['model'] or '—'}, режим {run['mode'] or '—'}")
    cost = run["cost_usd"]
    dur = run["duration_s"]
    L.append(f"Стоимость: {'—' if cost is None else '$%.4f' % cost}"
             f"{' (' + run['cost_note'] + ')' if run.get('cost_note') else ''}; время: {'—' if dur is None else '%.1f с' % dur}")
    c = rep["counts"]
    L.append("Сопоставление: " + ("слитые в карточку замечания считаются частью замечания (как их видит читатель)"
                                 if rep.get("options", {}).get("absorbed", True) else
                                 "только собственный текст и цитаты замечания (--no-absorbed)"))
    L.append(f"Требований: {c['requirements']}; открытых замечаний (без наблюдений): {c['findings_open']}; наблюдений: {c['advisory']}")
    for w in rep["warnings"]:
        L.append(f"ВНИМАНИЕ: {w}")
    L.append("")
    L.append("Полнота (сколько дефектов ключа найдено)")
    L.append(_tally_line("всего", rep["recall"]["overall"]))
    for sev in SEVERITIES:
        L.append(_tally_line(sev, rep["recall"]["by_severity"][sev]))
    for origin, t in rep["recall"]["by_origin"].items():
        label = "организаторов" if origin == "organisers" else f"происхождение {origin}"
        L.append(_tally_line(label, t))
    ag = rep["severity_agreement"]
    L.append("")
    L.append(f"Уровень у строго найденных: совпал {ag['same']}, инструмент выше {ag['tool_higher']}, инструмент ниже {ag['tool_lower']}")
    L.append("")
    L.append(f"Ложные тревоги (CRITICAL/MAJOR на «хорошем» месте): {len(rep['false_alarms'])}")
    for f in rep["false_alarms"]:
        L.append(f"  - {f['fid']} {f['severity']} строка {f['line']}: {f['title']}")
    if rep["false_alarms_exempt"]:
        L.append(f"Спорные, не считаются (на «хорошей» строке, но подтверждают дефект ключа): {len(rep['false_alarms_exempt'])}")
        for f in rep["false_alarms_exempt"]:
            L.append(f"  - {f['fid']} {f['severity']} строка {f['line']}: {f['title']} -> {', '.join(f['confirms'])}")
    L.append("")
    L.append(f"Замечания без совпадений (разобрать вручную): {len(rep['unmatched'])}")
    for f in rep["unmatched"]:
        L.append(f"  - {f['fid']} {f['severity']} строка {f['line']}: {f['title']}")
    L.append("")
    L.append("Дефекты ключа")
    mark = {"strict": "НАЙДЕН ", "loose": "место  ", "missed": "НЕТ    "}
    for r in rep["defects"]:
        fids = r["strict_fids"] or r["loose_fids"]
        extra = f" [{', '.join(fids)}]" if fids else ""
        L.append(f"  {r['id']} {r['severity']:<8} {mark[r['status']]} {r['title']}{extra}")
    L.append("")
    L.append("«НАЙДЕН» — место и ключевые слова; «место» — совпало только место (мягкая находка); «НЕТ» — не найдено.")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Оценка результата Ревизора ТЗ по ключу дефектов")
    ap.add_argument("result", help="JSON-двойник отчёта (Result.to_dict)")
    ap.add_argument("--key", required=True, help="файл ключа <документ>.key.json")
    ap.add_argument("--json", action="store_true", help="вывести машинный JSON вместо текста")
    ap.add_argument("--min-strict", type=float, help="порог строгой полноты, доля 0..1; ниже — код выхода 2")
    ap.add_argument("--min-organisers", type=float, help="порог строгой полноты по пунктам организаторов, доля 0..1")
    ap.add_argument("--no-absorbed", action="store_true",
                    help="старый способ: слитые в карточку замечания не считаются частью замечания (для сравнения двух чисел)")
    ap.add_argument("--force", action="store_true", help="не останавливаться, если имя документа в результате и в ключе разное")
    args = ap.parse_args(argv)
    for name in ("min_strict", "min_organisers"):
        v = getattr(args, name)
        if v is not None and not 0 <= v <= 1:
            print(f"--{name.replace('_', '-')}: нужна доля от 0 до 1 (например 0.5), получено {v}", file=sys.stderr)
            return 1
    try:
        rep = score_files(Path(args.result), Path(args.key), force=args.force, use_absorbed=not args.no_absorbed)
    except ScoreError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render_text(rep))
    code = 0
    if args.min_strict is not None:
        share = rep["recall"]["overall"]["strict_share"]
        if share is None or share < args.min_strict:
            print(f"Порог --min-strict {args.min_strict} не достигнут: {pct(share)}", file=sys.stderr)
            code = 2
    if args.min_organisers is not None:
        org = rep["recall"]["by_origin"].get("organisers")
        if org is None:
            print("--min-organisers: в ключе нет пунктов с origin=organisers", file=sys.stderr)
            return 1
        if org["strict_share"] < args.min_organisers:
            print(f"Порог --min-organisers {args.min_organisers} не достигнут: {pct(org['strict_share'])}", file=sys.stderr)
            code = 2
    return code


if __name__ == "__main__":
    from revizor.console import prepare_console

    prepare_console()
    sys.exit(main())
