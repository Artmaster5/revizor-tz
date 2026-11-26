#!/usr/bin/env python3
"""Проверка ключей дефектов: python eval/check_keys.py [папка_или_файл]

По умолчанию — eval/docs. Для каждого *.key.json проверяет: JSON читается; поле document указывает на
существующий файл; id уникальны; severity и criterion из известных; якоря и «good»-якоря стоят в документе
дословно, в одной строке, длиной 15–120 знаков; ключевые слова — непустые группы регулярных выражений
в нижнем регистре, которые компилируются. Код выхода 1, если нашлась хоть одна проблема.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from revizor.model import REQ_CRITERIA, SET_CRITERIA, SEVERITIES  # noqa: E402

CRITERIA = set(REQ_CRITERIA) | set(SET_CRITERIA)
MIN_ANCHOR, MAX_ANCHOR = 15, 120
ORIGINS = {"organisers", "own"}


def _read(path: Path) -> str:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def check_anchor(anchor: object, doc_text: str, label: str) -> list[str]:
    if not isinstance(anchor, str) or not anchor:
        return [f"{label}: якорь не строка или пуст"]
    out = []
    if "\n" in anchor or "\r" in anchor:
        out.append(f"{label}: якорь занимает больше одной строки")
    if not MIN_ANCHOR <= len(anchor) <= MAX_ANCHOR:
        out.append(f"{label}: длина якоря {len(anchor)}, нужно {MIN_ANCHOR}–{MAX_ANCHOR}")
    if anchor not in doc_text:
        out.append(f"{label}: якорь не найден в документе дословно: «{anchor[:50]}»")
    return out


def check_key(path: Path) -> list[str]:
    """Список проблем одного ключа (пустой — ключ в порядке)."""
    try:
        key = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"не читается как JSON: {exc}"]
    if not isinstance(key, dict):
        return ["корень ключа должен быть объектом"]
    problems: list[str] = []
    for field in ("document", "domain", "language", "defects", "good"):
        if field not in key:
            problems.append(f"нет поля «{field}»")
    doc_rel = key.get("document")
    doc_text = None
    if isinstance(doc_rel, str):
        doc_path = (Path(path).parent / doc_rel).resolve()
        if doc_path.is_file():
            doc_text = _read(doc_path)
        else:
            problems.append(f"документ не найден: {doc_path}")
    defects = key.get("defects")
    if not isinstance(defects, list) or not defects:
        problems.append("defects должен быть непустым списком")
        defects = []
    seen: set[str] = set()
    for n, d in enumerate(defects, 1):
        if not isinstance(d, dict):
            problems.append(f"defects[{n}]: не объект")
            continue
        did = d.get("id")
        label = str(did) if did else f"defects[{n}]"
        if not did or not isinstance(did, str):
            problems.append(f"{label}: нет id")
        elif did in seen:
            problems.append(f"{label}: id повторяется")
        else:
            seen.add(did)
        if d.get("severity") not in SEVERITIES:
            problems.append(f"{label}: неизвестный severity «{d.get('severity')}»")
        if d.get("criterion") not in CRITERIA:
            problems.append(f"{label}: неизвестный criterion «{d.get('criterion')}»")
        if "origin" in d and d["origin"] not in ORIGINS:
            problems.append(f"{label}: origin должен быть один из {sorted(ORIGINS)}")
        for field in ("title", "why"):
            if not isinstance(d.get(field), str) or not d.get(field, "").strip():
                problems.append(f"{label}: нет поля «{field}»")
        anchors = d.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            problems.append(f"{label}: нужен хотя бы один якорь")
        elif doc_text is not None:
            for a in anchors:
                problems += check_anchor(a, doc_text, label)
        groups = d.get("keywords")
        if not isinstance(groups, list) or not groups:
            problems.append(f"{label}: нужна хотя бы одна группа ключевых слов")
            continue
        for g_i, group in enumerate(groups, 1):
            if not isinstance(group, list) or not group:
                problems.append(f"{label}: группа ключевых слов {g_i} пуста или не список")
                continue
            for rx in group:
                if not isinstance(rx, str) or not rx:
                    problems.append(f"{label}: группа {g_i}: пустое выражение")
                    continue
                if rx != rx.lower():
                    problems.append(f"{label}: выражение «{rx}» не в нижнем регистре")
                try:
                    re.compile(rx)
                except re.error as exc:
                    problems.append(f"{label}: выражение «{rx}» не компилируется: {exc}")
    good = key.get("good")
    if not isinstance(good, list):
        problems.append("good должен быть списком")
        good = []
    for n, g in enumerate(good, 1):
        label = f"good[{n}]"
        if not isinstance(g, dict) or "anchor" not in g:
            problems.append(f"{label}: нет поля anchor")
            continue
        if not isinstance(g.get("why"), str) or not g["why"].strip():
            problems.append(f"{label}: нет поля why")
        if doc_text is not None:
            problems += check_anchor(g["anchor"], doc_text, label)
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    target = Path(args[0]) if args else Path(__file__).resolve().parent / "docs"
    keys = [target] if target.is_file() else sorted(target.glob("*.key.json"))
    if not keys:
        print(f"Ключи не найдены: {target}")
        return 1
    bad = 0
    for k in keys:
        problems = check_key(k)
        if problems:
            bad += 1
            print(f"ОШИБКИ  {k.name}")
            for p in problems:
                print(f"    - {p}")
        else:
            data = json.loads(k.read_text(encoding="utf-8"))
            print(f"в порядке  {k.name}: дефектов {len(data['defects'])}, «хорошего» {len(data['good'])}")
    print(f"Проверено ключей: {len(keys)}, с ошибками: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    from revizor.console import prepare_console

    prepare_console()
    sys.exit(main())
