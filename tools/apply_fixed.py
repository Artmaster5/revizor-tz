"""Собрать вторую версию ТЗ: подставить формулировки «Стало» из <отчёт>.fixed.md на место «Было» в исходном документе.

    python tools/apply_fixed.py reports/report.fixed.md docs/requirements.md docs/demo/compare/requirements.v2.md

Подставляются только строки, где «Было» нашлось в документе слово в слово. Остальные пропускаются и перечисляются.
Нужен для демонстрации «--compare»: показать, что переписанный текст закрывает дефекты.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s.replace("\\|", "\0")).replace("\0", "|").strip()


def rows(fixed_md: str):
    for line in fixed_md.splitlines():
        if not line.startswith("| REQ"):
            continue
        cells = [c for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) == 4:   # только таблица «Было | Стало | Основание»
            yield cells[0].strip(), _unescape(cells[1]), _unescape(cells[2])


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 1
    fixed, doc, out = (Path(a) for a in argv)
    text = doc.read_text(encoding="utf-8")
    done, skipped = [], []
    for rid, was, now in rows(fixed.read_text(encoding="utf-8")):
        if was and was in text and was != now:
            text = text.replace(was, now, 1)
            done.append(rid)
        else:
            skipped.append(rid)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"Подставлено {len(done)}: {', '.join(done)}")
    print(f"Пропущено {len(skipped)} (нет дословного совпадения): {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
