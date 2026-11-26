#!/usr/bin/env python3
"""Какие этапы агента дают ложные тревоги и повторы — по вердиктам независимого судьи.

    python eval/noise_by_stage.py eval/results/blind_opus [ПАПКА…]
В папке ждёт пары `<имя>.json` (отчёт агента) и `<имя>.judge.json` (вердикт судьи).
Считает только то, что судья отметил в `extra_findings`: ложная тревога, повтор, завышен уровень, настоящее вне ключа.
Вторая таблица — то же по уровню замечания (блокер, существенное, мелкое).
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
    by_level = collections.defaultdict(collections.Counter)  # уровень -> вердикт -> число
    all_by_level = collections.Counter()
    for d in sys.argv[1:]:
        for jp in sorted(Path(d).glob("*.judge.json")):
            rp = jp.with_name(jp.name.replace(".judge.json", ".json"))
            if not rp.exists():
                continue
            rep = json.loads(rp.read_text(encoding="utf-8"))
            jud = json.loads(jp.read_text(encoding="utf-8"))
            stage_of, level_of = {}, {}
            for f in rep.get("findings", []):
                if f.get("status", "open") != "open" or f.get("advisory"):
                    continue
                stage = f.get("stage") or f.get("source") or "?"
                if f.get("source") == "rule":
                    stage = "правила (без модели)"
                stage_of[f.get("fid")] = stage
                all_by_stage[stage] += 1
                level_of[f.get("fid")] = f.get("severity") or "?"
                all_by_level[level_of[f.get("fid")]] += 1
            for e in jud.get("extra_findings", []):
                stage = stage_of.get(e.get("fid"), "нет в открытых")
                total[stage][e.get("verdict", "?")] += 1
                by_level[level_of.get(e.get("fid"), "нет в открытых")][e.get("verdict", "?")] += 1
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
    print()
    print(f"{'уровень':32} {'всего':>6} {'ложных':>7} {'повторов':>9} {'завыш.':>7} {'вне ключа':>10}  доля ложных")
    names = {"CRITICAL": "блокер (CRITICAL)", "MAJOR": "существенное (MAJOR)", "MINOR": "мелкое (MINOR)"}
    for lv in [x for x in ("CRITICAL", "MAJOR", "MINOR") if x in all_by_level] + [x for x in by_level if x not in names]:
        c = by_level[lv]
        n = all_by_level.get(lv, 0)
        share = f"{100 * c['false_alarm'] / n:.0f} %" if n else "—"
        print(f"{names.get(lv, lv):32} {n:>6} {c['false_alarm']:>7} {c['duplicate']:>9} {c['overrated']:>7} {c['real_not_in_key']:>10}  {share}")


if __name__ == "__main__":
    if str(Path(__file__).resolve().parent.parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from revizor.console import prepare_console  # noqa: E402

    prepare_console()
    sys.exit(main())
