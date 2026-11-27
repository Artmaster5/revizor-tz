"""Собрать вторую версию ТЗ: подставить формулировки «Стало» из <отчёт>.fixed.md на место «Было» в исходном документе.

    python tools/apply_fixed.py reports/report_answered.fixed.md docs/requirements.md docs/demo/compare/requirements.v2.md [reports/report_answered.json]

Берутся только таблицы с колонками «Было | Стало»: это готовые формулировки. Таблицы «Что внести» и «Формулировка не
предложена» содержат указания автору, а не текст, их нельзя подставлять, и они пропускаются.

Где искать «Было» (по порядку, первое надёжное совпадение):
  1. дословно; 2. после нормализации (пробелы, markdown-значки, кавычки, тире, многоточие);
  3. по строкам требования из JSON-отчёта (необязательный четвёртый параметр: rid -> line_start..line_end);
  4. близкое совпадение difflib >= 0.85 на строке или группе до трёх строк.
Если мест несколько или лучшее не отрывается от второго, подстановка пропускается, причина печатается.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

FUZZY = 0.85
MARGIN = 0.05
_MAP = {"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "—": "-", "–": "-", "−": "-",
        "…": "...", " ": " ", "ё": "е", "Ё": "Е"}
_DROP = set("*_`#>\\")


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s.replace("\\|", "\0")).replace("\0", "|").strip()


def rows(fixed_md: str):
    """(rid, было, стало) только из таблиц «Было | Стало»."""
    on = False
    for line in fixed_md.splitlines():
        if not line.startswith("|"):
            on = False
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if cells and cells[0] == "Требование":
            on = len(cells) >= 3 and cells[1] == "Было" and cells[2] == "Стало"
            continue
        if on and line.startswith("| REQ") and len(cells) >= 3:
            yield cells[0], _unescape(cells[1]), _unescape(cells[2])


def _norm_map(s: str) -> tuple[str, list[int]]:
    out, idx, space = [], [], True
    for i, ch in enumerate(s):
        if ch in _DROP:
            continue
        ch = _MAP.get(ch, ch)
        if ch.isspace():
            if not space:
                out.append(" "); idx.append(i)
            space = True
            continue
        for c in ch:
            out.append(c); idx.append(i)
        space = False
    while out and out[-1] == " ":
        out.pop(); idx.pop()
    return "".join(out), idx


def _norm(s: str) -> str:
    return re.sub(r"\s*\.\.\.\s*", "...", _norm_map(s)[0])


def _occ(hay: str, needle: str) -> list[int]:
    res, at = [], hay.find(needle)
    while at != -1:
        res.append(at)
        at = hay.find(needle, at + 1)
    return res


def _lines(text: str) -> list[tuple[int, int]]:
    spans, at = [], 0
    for ln in text.splitlines(keepends=True):
        spans.append((at, at + len(ln.rstrip("\r\n"))))
        at += len(ln)
    return spans


def _prefix_len(text: str, start: int) -> int:
    m = re.match(r"[ \t]*(?:[*+-]|\d+[.)])[ \t]+", text[start:])
    return m.end() if m else 0


def locate(text: str, was: str, anchor: tuple[int, int] | None):
    """((start, end), how) или (None, причина). Для подстановки по строкам start сдвинут за маркер списка."""
    spans = _lines(text)
    a0 = spans[anchor[0] - 1][0] if anchor and 0 < anchor[0] <= len(spans) else None
    a1 = spans[min(anchor[1], len(spans)) - 1][1] if a0 is not None else None

    def pick(hits, how):
        if len(hits) == 1:
            return hits[0], how
        if len(hits) > 1 and a0 is not None:
            inside = [h for h in hits if a0 <= h[0] <= a1]
            if len(inside) == 1:
                return inside[0], how + "+якорь"
        return None, f"несколько мест ({len(hits)}), {how}"
    # 1. дословно
    hits = [(i, i + len(was)) for i in _occ(text, was)]
    if hits:
        return pick(hits, "дословно")
    # 2. нормализованно
    nt, idx = _norm_map(text)
    nw = _norm(was)
    nt2 = nt
    if nw and "..." not in nw:
        hits = []
        for i in _occ(nt2, nw):
            s0, e0 = idx[i], idx[i + len(nw) - 1] + 1
            while s0 > 0 and text[s0 - 1] in "*_`":   # захватить парные markdown-значки по краям
                s0 -= 1
            while e0 < len(text) and text[e0] in "*_`":
                e0 += 1
            hits.append((s0, e0))
        if hits:
            return pick(hits, "нормализованно")
    # 3. по строкам требования из отчёта
    if a0 is not None:
        seg = text[a0:a1]
        if difflib.SequenceMatcher(None, _norm(seg), nw).ratio() >= FUZZY:
            return (a0 + _prefix_len(text, a0), a1), "по строкам требования"
        return None, f"строки {anchor[0]}-{anchor[1]} не похожи на «Было» (< {FUZZY})"
    # 4. близкое совпадение на строке или группе строк
    cands = []
    for i in range(len(spans)):
        for k in (1, 2, 3):
            if i + k > len(spans):
                break
            s, e = spans[i][0], spans[i + k - 1][1]
            if e - s < 15:
                continue
            r = difflib.SequenceMatcher(None, _norm(text[s:e]), nw).ratio()
            if r >= FUZZY:
                cands.append((r, s + _prefix_len(text, s), e))
    cands.sort(reverse=True)
    if not cands:
        return None, f"не найдено (лучшее совпадение < {FUZZY})"
    if len(cands) > 1 and cands[0][0] - cands[1][0] < MARGIN and not (cands[1][1] <= cands[0][1] < cands[1][2] or cands[0][1] <= cands[1][1] < cands[0][2]):
        return None, "близких мест несколько, выбор неоднозначен"
    return (cands[0][1], cands[0][2]), f"близко ({cands[0][0]:.2f})"


def anchors(report_json: Path) -> dict[str, tuple[int, int]]:
    try:
        d = json.loads(report_json.read_text(encoding="utf-8"))
        return {r["rid"]: (int(r["line_start"]), int(r["line_end"])) for r in d.get("requirements", [])
                if r.get("rid") and r.get("line_start") and r.get("line_end")}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__)
        return 1
    fixed, doc, out = (Path(a) for a in argv[:3])
    anc = anchors(Path(argv[3])) if len(argv) == 4 else {}
    text = doc.read_text(encoding="utf-8")
    plan, log, seen = [], [], set()
    for rid, was, now in rows(fixed.read_text(encoding="utf-8")):
        if not was or was == now:
            continue
        if (rid, was) in seen:
            log.append(f"{rid}: повтор строки, пропущено")
            continue
        seen.add((rid, was))
        span, how = locate(text, was, anc.get(rid))
        if span is None:
            log.append(f"{rid}: пропущено — {how}")
        else:
            plan.append((span, rid, how, now))
            log.append(f"{rid}: подставлено ({how}), строка {text.count(chr(10), 0, span[0]) + 1}")
    plan.sort()
    ok, last = [], -1
    for span, rid, how, now in plan:   # пересекающиеся места не трогаем
        if span[0] < last:
            log.append(f"{rid}: пропущено — место пересекается с другой подстановкой")
            continue
        ok.append((span, rid, now))
        last = span[1]
    for (s, e), rid, now in reversed(ok):
        text = text[:s] + now + text[e:]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print("\n".join(log))
    print(f"Подставлено {len(ok)} из {len(plan) + sum('пропущено' in l for l in log)} формулировок «Было → Стало».")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
