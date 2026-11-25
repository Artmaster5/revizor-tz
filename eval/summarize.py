#!/usr/bin/env python3
"""Сводная таблица точности по папкам с результатами замера.

    python eval/summarize.py eval/results/tuning_haiku eval/results/blind_haiku eval/results/blind_opus

В папке ожидаются пары файлов одного прогона: <имя>.json (JSON-отчёт агента) и <имя>.score.json (вывод
`eval/score.py --json`). Если рядом лежит <имя>.judge.json (ответ независимого судьи, формат — eval/judge/BRIEF.md),
в таблицу добавляются его числа. Скрипт ничего не запускает и ничего не оценивает сам: он только складывает.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SEV = ("CRITICAL", "MAJOR", "MINOR")


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def judge_counts(j: dict) -> dict:
    d = {"found": 0, "partial": 0, "not_found": 0, "false_alarm": 0, "overrated": 0, "real_not_in_key": 0, "duplicate": 0}
    for x in j.get("defects", []):
        v = x.get("verdict")
        if v in d:
            d[v] += 1
    for x in j.get("extra_findings", []):
        v = x.get("verdict")
        if v in d:
            d[v] += 1
    used = {f for x in j.get("defects", []) if x.get("verdict") in ("found", "partial") for f in (x.get("finding_ids") or [])}
    dup_used = {x.get("fid") for x in j.get("extra_findings", []) if x.get("verdict") == "duplicate"} & used
    d["used"] = len(used - dup_used)
    return d


def row(folder: Path, name: str) -> dict | None:
    s, r = _load(folder / f"{name}.score.json"), _load(folder / f"{name}.json")
    if not s or not r:
        return None
    rec = s["recall"]["overall"]
    crit = (s["recall"].get("by_severity") or {}).get("CRITICAL") or {}
    org = (s["recall"].get("by_origin") or {}).get("organisers")
    op = [f for f in r["findings"] if f["status"] == "open" and not f.get("advisory")]
    fa = s.get("false_alarms") or []
    out = {
        "name": name, "doc": r["run"].get("doc_name", name), "model": r["run"].get("model") or "—",
        "total": rec["total"], "strict": rec["strict"], "loose": rec["loose"],
        "crit_total": crit.get("total", 0), "crit_strict": crit.get("strict", 0),
        "org": org, "false": fa if isinstance(fa, int) else len(fa),
        "sev": {k: sum(1 for f in op if f["severity"] == k) for k in SEV},
        "usd": r["run"].get("cost_usd") or 0.0, "sec": round(r["run"].get("duration_s") or 0),
        "judge": None,
    }
    j = _load(folder / f"{name}.judge.json")
    if j:
        out["judge"] = judge_counts(j)
    return out


def pct(a: int, b: int) -> str:
    return f"{a} из {b} ({round(100 * a / b)}%)" if b else "—"


def table(folder: Path) -> str:
    names = sorted(p.name[:-len(".score.json")] for p in folder.glob("*.score.json"))
    rows = [x for x in (row(folder, n) for n in names) if x]
    if not rows:
        return f"### {folder.name}\n\nнет результатов\n"
    has_judge = any(x["judge"] for x in rows)
    head = ["Документ", "Модель", "Строго", "По месту", "Блокеры ключа строго", "Ложные (счётчик)", "Замечаний (C/M/m)", "Цена", "Время"]
    if has_judge:
        head[4:4] = ["Судья: найдено / частично / нет", "Судья: ложные"]
    L = [f"### {folder.name}", "", "| " + " | ".join(head) + " |", "|" + " :--- |" * len(head)]
    tot = {"total": 0, "strict": 0, "loose": 0, "ct": 0, "cs": 0, "false": 0, "c": 0, "usd": 0.0, "sec": 0,
           "jf": 0, "jp": 0, "jn": 0, "jfa": 0, "jtotal": 0}
    for x in rows:
        cells = [f"`{x['doc']}`", f"`{x['model']}`", pct(x["strict"], x["total"]), pct(x["loose"], x["total"])]
        if has_judge:
            j = x["judge"]
            cells += ([f"{j['found']} / {j['partial']} / {j['not_found']}", str(j["false_alarm"])] if j else ["—", "—"])
            if j:
                tot["jf"] += j["found"]; tot["jp"] += j["partial"]; tot["jn"] += j["not_found"]; tot["jfa"] += j["false_alarm"]
                tot["jtotal"] += x["total"]
        cells += [f"{x['crit_strict']} из {x['crit_total']}", str(x["false"]),
                  f"{sum(x['sev'].values())} ({x['sev']['CRITICAL']}/{x['sev']['MAJOR']}/{x['sev']['MINOR']})",
                  f"${x['usd']:.2f}", f"{x['sec']} с"]
        L.append("| " + " | ".join(cells) + " |")
        tot["total"] += x["total"]; tot["strict"] += x["strict"]; tot["loose"] += x["loose"]; tot["ct"] += x["crit_total"]
        tot["cs"] += x["crit_strict"]; tot["false"] += x["false"]; tot["c"] += x["sev"]["CRITICAL"]
        tot["usd"] += x["usd"]; tot["sec"] += x["sec"]
    if len(rows) > 1:
        cells = [f"**Вместе ({len(rows)})**", "", f"**{pct(tot['strict'], tot['total'])}**", pct(tot["loose"], tot["total"])]
        if has_judge:
            cells += [f"**{tot['jf']} / {tot['jp']} / {tot['jn']}** из {tot['jtotal']}", str(tot["jfa"])]
        cells += [f"{tot['cs']} из {tot['ct']}", str(tot["false"]), f"блокеров в отчётах {tot['c']}", f"${tot['usd']:.2f}", f"{tot['sec']} с"]
        L.append("| " + " | ".join(cells) + " |")
    for x in rows:
        j = x["judge"]
        if j:
            n = sum(x["sev"].values())
            real = j["used"] + j["real_not_in_key"] + j["overrated"]
            L += ["", f"Точность по судье (`{x['doc']}`): из {n} открытых замечаний настоящих {real} "
                      f"(по ключу {j['used']}, вне ключа {j['real_not_in_key']}, настоящих с завышенным уровнем {j['overrated']}), "
                      f"повторов {j['duplicate']}, ложных {j['false_alarm']}."]
    for x in rows:
        if x["org"]:
            L += ["", f"Пункты из материалов организаторов (`{x['doc']}`): строго {x['org']['strict']} из {x['org']['total']}, "
                      f"по месту {x['org']['loose']} из {x['org']['total']}."]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = (argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    for a in args:
        print(table(Path(a)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
