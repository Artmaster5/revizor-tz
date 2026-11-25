#!/usr/bin/env python3
"""Какие этапы агента дают ложные тревоги и повторы — по вердиктам независимого судьи.

    python eval/noise_by_stage.py eval/results/blind_opus [ПАПКА…]
В папке ждёт пары `<имя>.json` (отчёт агента) и `<имя>.judge.json` (вердикт судьи).
Считает только то, что судья отметил в `extra_findings`: ложная тревога, повтор, завышен уровень, настоящее вне ключа.
Ничего не запускает в модели.
"""
import collections
import json
import sys
from pathlib import Path

NAMES = {"false_alarm": "ложных", "duplicate": "повторов", "overrated": "завышен уровень", "real_not_in_key": "настоящих вне ключа"}


def main():
    total = collections.defaultdict(collections.Counter)  # этап -> вердикт -> число
    all_by_stage = collections.Counter()
    for d in sys.argv[1:]:
        for jp in sorted(Path(d).glob("*.judge.json")):
            rp = jp.with_name(jp.name.replace(".judge.json", ".json"))
            if not rp.exists():
                continue
            rep = json.loads(rp.read_text(encoding="utf-8"))
            jud = json.loads(jp.read_text(encoding="utf-8"))
            stage_of = {}
            for f in rep.get("findings", []):
                if f.get("status", "open") != "open" or f.get("advisory"):
                    continue
                stage = f.get("stage") or f.get("source") or "?"
                if f.get("source") == "rule":
                    stage = "правила (без модели)"
                stage_of[f.get("fid")] = stage
                all_by_stage[stage] += 1
            for e in jud.get("extra_findings", []):
                stage = stage_of.get(e.get("fid"), "нет в открытых")
                total[stage][e.get("verdict", "?")] += 1
    print(f"{'этап':32} {'всего':>6} {'ложных':>7} {'повторов':>9} {'завыш.':>7} {'вне ключа':>10}  доля ложных")
    rows = sorted(all_by_stage, key=lambda s: -total[s]["false_alarm"])
    for s in rows + [s for s in total if s not in all_by_stage]:
        c = total[s]
        n = all_by_stage.get(s, 0)
        share = f"{100 * c['false_alarm'] / n:.0f} %" if n else "—"
        print(f"{s:32} {n:>6} {c['false_alarm']:>7} {c['duplicate']:>9} {c['overrated']:>7} {c['real_not_in_key']:>10}  {share}")
    n = sum(all_by_stage.values())
    fa = sum(c["false_alarm"] for c in total.values())
    du = sum(c["duplicate"] for c in total.values())
    print(f"{'ИТОГО':32} {n:>6} {fa:>7} {du:>9}")


if __name__ == "__main__":
    sys.exit(main())
