#!/usr/bin/env python3
"""Прогон «Ревизора ТЗ» по всем документам оценки и сводка eval/RESULTS.md.

    python eval/run_all.py [--provider offline] [--model M] [--depth full|fast] [--only ИМЯ] [--i-accept-cost]

Для каждого ключа eval/docs/*.key.json запускается
    <тот же python> main.py --input <документ> --output eval/results/<имя документа без расширения>.<провайдер>.md
(рядом появляется JSON-двойник <имя>.<провайдер>.json), затем результат оценивается score.py.
Сводка RESULTS.md строится по всем сохранённым результатам в eval/results (по таблице на пару провайдер/модель),
поэтому живой прогон добавляется к офлайновому, а не затирает его.

Платные провайдеры (всё, кроме offline) запускаются только с --i-accept-cost. Команды печатаются до запуска.
Офлайн-запуск идёт с REVIZOR_NO_DOTENV=1, чтобы .env не читался.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

EVAL_DIR = Path(__file__).resolve().parent
AGENT_ROOT = EVAL_DIR.parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import score  # noqa: E402

DOCS_DIR = EVAL_DIR / "docs"
RESULTS_DIR = EVAL_DIR / "results"
RESULTS_MD = EVAL_DIR / "RESULTS.md"
MAIN_PY = AGENT_ROOT / "main.py"
RUN_TIMEOUT_S = 3600

Runner = Callable[[list, dict], int]


def default_runner(cmd: list, env: dict) -> int:
    if not MAIN_PY.exists():
        print(f"Ошибка: {MAIN_PY} ещё не существует", file=sys.stderr)
        return 127
    try:
        return subprocess.run(cmd, cwd=str(AGENT_ROOT), env=env, timeout=RUN_TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        print("Ошибка: прогон превысил лимит времени", file=sys.stderr)
        return 124


def discover_keys(only: str | None = None) -> list[dict]:
    """[{key_path, doc_path, doc_name, stem}] в порядке: документ организаторов, затем настроечные по имени."""
    items = []
    for kp in sorted(DOCS_DIR.glob("*.key.json")):
        key, doc_path = score.load_key(kp)
        items.append({"key_path": kp, "doc_path": doc_path, "doc_name": doc_path.name, "stem": doc_path.stem,
                      "organisers": any(d.get("origin") == "organisers" for d in key["defects"])})
    items.sort(key=lambda x: (not x["organisers"], x["doc_name"]))
    if only:
        items = [x for x in items if only in (x["doc_name"], x["stem"])]
        if not items:
            raise SystemExit(f"--only {only}: такого документа нет среди ключей в {DOCS_DIR}")
    return items


def plan(items: list[dict], provider: str, model: str | None, depth: str | None) -> list[dict]:
    out = []
    for it in items:
        report = RESULTS_DIR / f"{it['stem']}.{provider}.md"
        cmd = [sys.executable, str(MAIN_PY), "--input", str(it["doc_path"]), "--output", str(report), "--provider", provider]
        if model:
            cmd += ["--model", model]
        if depth:
            cmd += ["--depth", depth]
        out.append({**it, "report": report, "twin": report.with_suffix(".json"), "cmd": cmd})
    return out


def build_env(provider: str) -> dict:
    env = dict(os.environ)
    if provider == "offline":
        env["REVIZOR_NO_DOTENV"] = "1"
    return env


# --- сводка -----------------------------------------------------------------


def _fmt_share(t: dict) -> str:
    return f"{t['strict']}/{t['total']}" if t["total"] else "—"


def collect_scored(items: list[dict]) -> tuple[dict, list[str]]:
    """Оценивает все сохранённые результаты. Возвращает {(provider, model): {stem: отчёт}} и список проблем."""
    groups: dict[tuple[str, str], dict[str, dict]] = {}
    problems: list[str] = []
    for it in items:
        for twin in sorted(RESULTS_DIR.glob(f"{it['stem']}.*.json")):
            if twin.name.endswith(".score.json"):
                continue
            try:
                rep = score.score_files(twin, it["key_path"])
            except score.ScoreError as exc:
                problems.append(f"{twin.name}: {exc}")
                continue
            if rep["warnings"]:
                problems.append(f"{twin.name}: " + "; ".join(rep["warnings"]))
            groups.setdefault((rep["run"]["provider"] or twin.stem.split(".")[-1], rep["run"]["model"] or "—"), {})[it["stem"]] = rep
    return groups, problems


def _money(x) -> str:
    return "—" if x is None else f"${x:.2f}"


def render_results(items: list[dict], groups: dict, problems: list[str], now: str) -> str:
    L = ["# Результаты измерения «Ревизора ТЗ»", "",
         f"Сформировано: {now}. Источник — файлы в `eval/results/`, оценка — `eval/score.py` (правила в его шапке).", ""]
    if not groups:
        L += ["Сохранённых результатов пока нет: запустите `python eval/run_all.py`.", ""]
    order = [it["stem"] for it in items]
    for (provider, model), docs in sorted(groups.items()):
        L += [f"## Провайдер `{provider}`, модель `{model}`", "",
              "| Документ | Требований | Замечаний | Найдено строго | Найдено мягко | CRITICAL строго | Замечания на месте без дефекта | Стоимость | Время |",
              "|---|---|---|---|---|---|---|---|---|"]
        blind = {"strict": 0, "loose": 0, "total": 0, "crit_s": 0, "crit_t": 0, "fa": 0, "cost": 0.0, "dur": 0.0, "n": 0, "cost_known": True}
        org_line = ""
        for stem in order:
            rep = docs.get(stem)
            if rep is None:
                L.append(f"| {stem} | не прогонялся | | | | | | | |")
                continue
            ov = rep["recall"]["overall"]
            cr = rep["recall"]["by_severity"]["CRITICAL"]
            dur = rep["run"]["duration_s"]
            L.append(f"| {stem} | {rep['counts']['requirements']} | {rep['counts']['findings_open']} | {_fmt_share(ov)} ({score.pct(ov['strict_share'])})"
                     f" | {ov['loose']}/{ov['total']} ({score.pct(ov['loose_share'])}) | {_fmt_share(cr)} | {len(rep['false_alarms'])}"
                     f" | {_money(rep['run']['cost_usd'])} | {'—' if dur is None else '%.0f с' % dur} |")
            org = rep["recall"]["by_origin"].get("organisers")
            if org:
                org_line = (f"Пункты, названные организаторами в удалённом ответнике: строго {org['strict']}/{org['total']}"
                            f" ({score.pct(org['strict_share'])}), мягко {org['loose']}/{org['total']} ({score.pct(org['loose_share'])}).")
            if not any(x["stem"] == stem and x["organisers"] for x in items):
                blind["strict"] += ov["strict"]; blind["loose"] += ov["loose"]; blind["total"] += ov["total"]
                blind["crit_s"] += cr["strict"]; blind["crit_t"] += cr["total"]; blind["fa"] += len(rep["false_alarms"]); blind["n"] += 1
                if rep["run"]["cost_usd"] is None:
                    blind["cost_known"] = False
                else:
                    blind["cost"] += rep["run"]["cost_usd"]
                blind["dur"] += dur or 0.0
        if blind["n"]:
            sh = blind["strict"] / blind["total"] if blind["total"] else None
            lo = blind["loose"] / blind["total"] if blind["total"] else None
            L.append(f"| **Настроечные вместе ({blind['n']})** | | | **{blind['strict']}/{blind['total']} ({score.pct(sh)})**"
                     f" | {blind['loose']}/{blind['total']} ({score.pct(lo)}) | {blind['crit_s']}/{blind['crit_t']} | {blind['fa']}"
                     f" | {_money(blind['cost']) if blind['cost_known'] else '—'} | {blind['dur']:.0f} с |")
        L.append("")
        if org_line:
            L += [org_line, ""]
        ag = {"same": 0, "tool_higher": 0, "tool_lower": 0}
        for rep in docs.values():
            for k2 in ag:
                ag[k2] += rep["severity_agreement"][k2]
        L += [f"Уровень у строго найденных дефектов: совпал {ag['same']}, инструмент поставил выше {ag['tool_higher']}, ниже {ag['tool_lower']}.", ""]
        unm = sum(len(r["unmatched"]) for r in docs.values())
        exm = sum(len(r["false_alarms_exempt"]) for r in docs.values())
        L += [f"Замечаний без совпадения с ключом (нужен ручной разбор, это не обязательно ошибки): {unm}. Спорных замечаний на «хороших» строках, не засчитанных как ложные тревоги: {exm}.", ""]
    if problems:
        L += ["## Предупреждения при оценке", ""] + [f"- {p}" for p in problems] + [""]
    L.append(EXPLANATION)
    return "\n".join(L).rstrip() + "\n"


EXPLANATION = """## Как читать таблицу

- **Найдено строго** — сколько дефектов из ключа инструмент нашёл при двух совпадениях сразу: цитата открытого замечания пересекается с якорем дефекта
  (то же место документа) **и** в тексте замечания (заголовок, суть, рекомендация) есть слова обеих групп ключа. Это число можно цитировать.
- **Найдено мягко** — совпало только место. Это верхняя граница: замечание могло быть о другом на той же строке. Мягкое число отдельно не заявляется.
- **CRITICAL строго** — то же для дефектов, которые по ключу критические.
- **Замечания на месте без дефекта** — открытые замечания уровня CRITICAL или MAJOR, чья цитата пересекается с местом, которое в ключе названо хорошим
  (в настроечных документах такие места отмечены автором ключа, в документе организаторов — командой). Замечание, которое при этом строго подтверждает
  настоящий дефект ключа на той же строке, в эту колонку не входит и отдельно перечисляется как спорное.
- **Замечаний без совпадения** — открытые замечания вне якорей дефектов; ключ не может быть исчерпывающим, поэтому часть из них — верные находки сверх ключа.
  Их разбирает человек, в долю точности они автоматически не входят.
- Наблюдения (`advisory`), отклонённые и слитые замечания не учитываются нигде.

## Как работает сопоставление

1. Текст цитаты и якоря приводится к одному виду (`revizor.quotes.normalize`: без разметки, единые кавычки и тире, `ё`→`е`, нижний регистр).
2. Место совпало, если одна строка содержится в другой (не короче 12 знаков) или не менее 60% слов якоря есть в цитате, **и** номера строк цитаты
   пересекаются со строками, где якорь стоит в файле. Цитаты длиннее 600 знаков считаются слишком широкими.
3. Ключевые слова — регулярные выражения, по группе на «о чём» и «в чём проблема»; нужна хотя бы одна подходящая в каждой группе.
4. Один дефект засчитывается один раз; одно замечание может подтвердить несколько дефектов, если цитирует несколько мест и содержит слова обоих.

Повторить любой результат: `python eval/score.py eval/results/<документ>.<провайдер>.json --key eval/docs/<документ>.key.json`.
Проверить сами ключи: `python eval/check_keys.py`.

## Как читать цифры

- Четыре настроечных документа и их ключи написал автор, который не видел инструмент; ключи нельзя было подогнать под его ответы. Но ключ — это мнение одного
  человека: часть дефектов он мог не заметить, поэтому «замечание без совпадения» не равно ошибке.
- Ключ документа организаторов написала команда (руководитель проекта до знакомства с ответами инструмента), а не независимый автор.
  Пункты с `origin: organisers` — те, что сами организаторы называли в удалённом шаблоне ответа; остальные — наши. Это не слепая проверка.
- Правила офлайн-слоя после пробных прогонов на этих четырёх документах правились, чтобы убрать замечания на местах без дефекта; сами ключи при этом не открывались.
  Поэтому эти документы названы настроечными, а не слепыми: числа по ним относятся к инструменту, настроенному с их помощью по замечаниям на местах без дефекта, но не по полноте.
- Ключевые слова подбирались вручную. Их строгость проверена на примерах (правильное замечание другими словами проходит, неверное на той же строке — нет),
  но редкая формулировка может не пройти строгую проверку, хотя замечание верное; такие случаи видны как «найдено мягко» без «строго».
- Полнота живых прогонов (с моделью) вероятностна: две попытки одного документа дадут чуть разные числа. Офлайн-результат детерминирован.
- Малая выборка: 52 + 4 документа (по 12–18 дефектов). Проценты — это доли от небольших чисел; рядом всегда стоят сами числа.

## Файлы замера версии 1.4.0

Отчёты, оценки программы и вердикты судьи, на которых стоят числа версии 1.4.0 в [`ACCURACY.md`](ACCURACY.md), лежат в `eval/results/v140_*/`
(код замера — коммит `2a2060a`). Список папок и таблица «число — файл — пересчёт» — в разделе 7 того же файла. Новый набор незнакомых документов
с ключами — `eval/blind3/`. Пересчитать любую оценку: `python eval/score.py eval/results/v140_sonnet/req.json --key eval/docs/requirements.md.key.json`.
"""


def main(argv: list[str] | None = None, runner: Runner | None = None, out=None) -> int:
    ap = argparse.ArgumentParser(description="Прогон Ревизора ТЗ по документам оценки и сводка RESULTS.md")
    ap.add_argument("--provider", default="offline", help="offline (по умолчанию), auto, anthropic, gemini, openai, ollama")
    ap.add_argument("--model", help="имя модели (передаётся в main.py как есть)")
    ap.add_argument("--depth", choices=("full", "fast"), help="глубина проверки main.py")
    ap.add_argument("--only", help="только один документ: имя файла или имя без расширения")
    ap.add_argument("--i-accept-cost", action="store_true", help="разрешить платный (не offline) провайдер")
    ap.add_argument("--no-run", action="store_true", help="не запускать main.py, только оценить уже сохранённые результаты и собрать RESULTS.md")
    args = ap.parse_args(argv)
    out = out or sys.stdout
    runner = runner or default_runner

    items = discover_keys(args.only)
    steps = plan(items, args.provider, args.model, args.depth)
    print("Запланированные команды:" if not args.no_run else "Запуск пропущен (--no-run).", file=out)
    for s in steps:
        print("  " + " ".join(f'"{c}"' if " " in c else c for c in s["cmd"]), file=out)
    if args.provider != "offline" and not args.no_run and not args.i_accept_cost:
        print(f"\nОТКАЗ: провайдер «{args.provider}» может стоить денег. Добавьте --i-accept-cost, если это нужно.", file=out)
        return 2

    failed = []
    if not args.no_run:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        env = build_env(args.provider)
        for s in steps:
            print(f"\n== {s['doc_name']} ==", file=out)
            rc = runner(s["cmd"], env)
            if rc != 0 or not s["twin"].exists():
                failed.append(s["doc_name"])
                print(f"Прогон не удался (код {rc}, двойник {'есть' if s['twin'].exists() else 'не создан'})", file=out)
                continue
            try:
                rep = score.score_files(s["twin"], s["key_path"])
                s["twin"].with_suffix(".score.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
                ov = rep["recall"]["overall"]
                print(f"Строго {ov['strict']}/{ov['total']}, мягко {ov['loose']}/{ov['total']}, замечаний на месте без дефекта {len(rep['false_alarms'])}", file=out)
            except score.ScoreError as exc:
                failed.append(s["doc_name"])
                print(f"Оценка не удалась: {exc}", file=out)

    groups, problems = collect_scored(discover_keys())
    problems += [f"прогон не удался: {name}" for name in failed]
    RESULTS_MD.write_text(render_results(discover_keys(), groups, problems, datetime.now().strftime("%Y-%m-%d %H:%M")), encoding="utf-8", newline="\n")
    print(f"\nСводка записана: {RESULTS_MD}", file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    if str(Path(__file__).resolve().parent.parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from revizor.console import prepare_console  # noqa: E402

    prepare_console()
    sys.exit(main())
