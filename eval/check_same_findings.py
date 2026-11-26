#!/usr/bin/env python3
"""Проверка: после тега v1.1.1 менялась только печать отчёта, найденное агентом то же.

    python eval/check_same_findings.py

Что проверяется (без модели и без сети):
1. Результаты в `reports/` — те же файлы, по которым считалась точность: sha256 совпадает с файлами замера в `eval/results/`.
2. Пересчёт `eval/score.py` по этим результатам даёт числа, записанные при замере (`req.score.json`, `req.score_key_v1.json`).
3. В каждом перерисованном отчёте `reports/*.md` есть каждое замечание из его JSON, ровно один раз.
4. С тега v1.1.1 в коде агента изменились только модули печати (нужен git; без него шаг пропускается).

Код выхода 0 — всё совпало, 1 — есть расхождение.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_TAG = "v1.1.1"
PRINT_MODULES = {"revizor/report_md.py", "revizor/exports.py", "revizor/report_html.py", "revizor/cli.py",
                 "tools/rerender.py", "tools/shots.py",
                 "revizor/webui.py", "revizor/webui_page.html",  # the browser window: it only starts the CLI check
                 "revizor/__init__.py"}  # only the version string: it is printed in the report passport and nowhere else
REPORTS = ["reports/report", "reports/report_haiku", "reports/report_answered"]  # <имя>.json и <имя>.md
PAIRS = [  # (отчёт в репозитории, файл замера)
    ("reports/report.json", "eval/results/final_opus/req.json"),
    ("reports/report_haiku.json", "eval/results/haiku/req.json"),
]
SCORES = [  # (результат, ключ, записанный счёт)
    ("reports/report.json", "eval/docs/requirements.md.key.json", "eval/results/final_opus/req.score.json"),
    ("reports/report.json", "eval/keys_v1/requirements.md.key.json", "eval/results/final_opus/req.score_key_v1.json"),
    ("reports/report_haiku.json", "eval/docs/requirements.md.key.json", "eval/results/haiku/req.score.json"),
    ("reports/report_haiku.json", "eval/keys_v1/requirements.md.key.json", "eval/results/haiku/req.score_key_v1.json"),
]
PATH_FIELDS = ("document", "result_file", "key_file")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    bad = 0
    print("1. Результаты в reports/ — те же, что мерили")
    for a, b in PAIRS:
        same = sha(ROOT / a) == sha(ROOT / b)
        bad += not same
        print(f"   {'совпадает' if same else 'РАЗЛИЧАЕТСЯ'}: {a} = {b} (sha256 {sha(ROOT / a)[:12]})")

    print("2. Пересчёт точности по этим результатам")
    for res, key, stored in SCORES:
        out = subprocess.run([sys.executable, str(ROOT / "eval" / "score.py"), str(ROOT / res), "--key", str(ROOT / key), "--json"],
                             capture_output=True, text=True, cwd=ROOT)
        try:
            now = json.loads(out.stdout)
        except json.JSONDecodeError:
            print(f"   ОШИБКА: score.py не дал JSON для {res} ({out.stderr.strip()[:200]})")
            bad += 1
            continue
        was = json.loads((ROOT / stored).read_text(encoding="utf-8"))
        for d in (now, was):
            for k in PATH_FIELDS:
                d.pop(k, None)
        same = now == was
        bad += not same
        o = now.get("recall", {}).get("overall", {})
        print(f"   {'совпадает' if same else 'РАЗЛИЧАЕТСЯ'}: {res} по ключу {key} — строго {o.get('strict')} из {o.get('total')}, "
              f"по месту {o.get('loose')} из {o.get('total')} (записано в {stored})")

    print("3. Каждое замечание из JSON есть в перерисованном отчёте")
    for stem in REPORTS:
        data = json.loads((ROOT / (stem + ".json")).read_text(encoding="utf-8"))
        want = sorted(f["fid"].lower() for f in data["findings"] if f.get("status", "open") == "open" and not f.get("advisory"))
        got = sorted(re.findall(r'<a id="(d-\d+)"></a>', (ROOT / (stem + ".md")).read_text(encoding="utf-8")))
        same = want == got
        bad += not same
        print(f"   {'совпадает' if same else 'РАЗЛИЧАЕТСЯ'}: {stem}.md — замечаний в JSON {len(want)}, в отчёте {len(got)}, "
              f"повторов {len(got) - len(set(got))}")

    print(f"4. Что изменилось в коде агента после тега {BASE_TAG}")
    try:
        names = subprocess.run(["git", "diff", "--name-only", BASE_TAG, "HEAD", "--", "revizor", "main.py", "tools"],
                               capture_output=True, text=True, cwd=ROOT, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        print("   пропущено: нет git или тега (например, архив без истории)")
    else:
        other = [n for n in names if n not in PRINT_MODULES]
        bad += bool(other)
        print("   изменены: " + (", ".join(names) if names else "ничего"))
        print("   " + ("вне модулей печати: " + ", ".join(other) if other else "все изменённые файлы — модули печати"))

    print("ИТОГ: " + ("найденное то же, менялась только печать" if not bad else f"расхождений: {bad}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
