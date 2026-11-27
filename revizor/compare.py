"""Сравнение двух версий одного ТЗ по двум JSON-отчётам (``--compare OLD.json NEW.json``).

Модель не нужна. Дефект «тот же», если совпал id правила или критерий И пересеклись цитаты (нормализованный текст,
вхождение или половина слов), либо при общем правиле/критерии похожи заголовок и цитата (difflib). Номера строк
не сравниваются: между версиями они сдвигаются. Никогда не падает на чужом файле: бросает ValueError с понятным текстом.
"""
from __future__ import annotations

import difflib
import html as _html
import json
from pathlib import Path
from typing import Any

from .baseline import FORMAT_PREFIX, MAX_BASELINE_BYTES, _norm, _overlap, _old_quotes
from .textfmt import shorten

SIM = 0.8   # порог похожести заголовка/цитаты для запасного совпадения


def load(path: str) -> dict[str, Any]:
    p = Path(path)
    try:
        if p.stat().st_size > MAX_BASELINE_BYTES:
            raise ValueError(f"Файл «{p.name}» слишком большой.")
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"Файл «{p.name}» не прочитан ({type(e).__name__}).") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Файл «{p.name}» — не JSON.") from e
    if not isinstance(data, dict) or not str(data.get("format") or "").startswith(FORMAT_PREFIX) \
            or not isinstance(data.get("findings"), list):
        raise ValueError(f"Файл «{p.name}» не похож на JSON-отчёт Ревизора ТЗ (нет поля format и списка findings).")
    return data


def _open(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in data["findings"]
            if isinstance(f, dict) and f.get("status", "open") == "open" and not f.get("advisory")]


def _ratio(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _same(o: dict[str, Any], n: dict[str, Any]) -> bool:
    rule = bool(set(o.get("rule_ids") or []) & set(n.get("rule_ids") or []))
    crit = bool(o.get("criterion")) and o.get("criterion") == n.get("criterion")
    if not (rule or crit):
        return False
    oq, nq = _old_quotes(o), _old_quotes(n)
    if any(_overlap(a, b) for a in oq for b in nq):
        return True
    if any(_ratio(a, b) >= SIM for a in oq for b in nq):
        return True
    return rule and _ratio(str(o.get("title") or ""), str(n.get("title") or "")) >= SIM and not (oq and nq)


def _brief(f: dict[str, Any]) -> dict[str, str]:
    q = _old_quotes(f)
    return {"fid": str(f.get("fid") or ""), "severity": str(f.get("severity") or ""),
            "title": str(f.get("title") or "")[:160], "quote": shorten(q[0], 160) if q else ""}


def _side(data: dict[str, Any]) -> dict[str, Any]:
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    s = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    rd = data.get("readiness") if isinstance(data.get("readiness"), dict) else {}
    dev = rd.get("dev") if isinstance(rd.get("dev"), dict) else {}
    return {"doc": str(run.get("doc_name") or ""), "when": str(run.get("started_at") or ""),
            "index": s.get("readiness"), "blockers": len(dev.get("blockers") or []),
            "verdict": str(data.get("verdict") or "")}


def compare(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    o_list, n_list = _open(old), _open(new)
    used: set[int] = set()
    closed, remain = [], []
    for o in o_list:
        hit = next((i for i, n in enumerate(n_list) if i not in used and _same(o, n)), None)
        if hit is None:
            closed.append(_brief(o))
        else:
            used.add(hit)
            remain.append({**_brief(n_list[hit]), "old_fid": str(o.get("fid") or "")})
    fresh = [_brief(n) for i, n in enumerate(n_list) if i not in used]
    return {"old": _side(old), "new": _side(new), "closed": closed, "remaining": remain, "new_items": fresh,
            "old_total": len(o_list), "new_total": len(n_list)}


def _ix(v: Any) -> str:
    return "—" if v is None else str(v)


def summary_line(c: dict[str, Any]) -> str:
    return f"Закрыто {len(c['closed'])} · Осталось {len(c['remaining'])} · Новых {len(c['new_items'])}"


def _dynamics(c: dict[str, Any]) -> list[str]:
    o, n = c["old"], c["new"]
    return [f"Индекс готовности: {_ix(o['index'])} → {_ix(n['index'])}",
            f"Блокеры: {o['blockers']} → {n['blockers']}",
            f"Всего дефектов: {c['old_total']} → {c['new_total']}"]


def to_markdown(c: dict[str, Any]) -> str:
    o, n = c["old"], c["new"]
    L = ["# Сравнение двух версий ТЗ", "",
         f"- Было: {o['doc'] or '—'} (проверено {o['when'] or '—'})",
         f"- Стало: {n['doc'] or '—'} (проверено {n['when'] or '—'})", "",
         f"**{summary_line(c)}**", ""] + [f"- {x}" for x in _dynamics(c)] + [""]

    def sect(title: str, items: list[dict[str, str]], quote: bool) -> None:
        L.append(f"## {title} ({len(items)})")
        L.append("")
        if not items:
            L.extend(["Нет.", ""])
        for it in items:
            was = f" (в старой версии {it['old_fid']})" if it.get("old_fid") and it["old_fid"] != it["fid"] else ""
            L.append(f"- **{it['fid']}** [{it['severity']}] {it['title']}{was}")
            if quote and it["quote"]:
                L.append(f"  - Было в тексте: «{it['quote']}»")
        L.append("")
    sect("Закрыто", c["closed"], True)
    sect("Осталось", c["remaining"], False)
    sect("Новые", c["new_items"], False)
    L.append("Сопоставление дефектов идёт по правилу или критерию и по тексту цитаты, а не по номерам строк.")
    return "\n".join(L) + "\n"


_CSS = ("body{font:16px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;background:#fff;color:#1c1f24}"
        "h2{margin-top:1.6rem}.sum{font-size:1.3rem;font-weight:600;margin:1rem 0}"
        ".c{border-left:4px solid #2e7d32}.r{border-left:4px solid #b26a00}.n{border-left:4px solid #c62828}"
        "li{margin:.4rem 0;padding-left:.6rem;list-style:none}.q{color:#555;font-size:.9rem}"
        "code{background:#eee;padding:0 .3rem;border-radius:3px}"
        "@media(prefers-color-scheme:dark){body{background:#15181c;color:#e6e8eb}.q{color:#aab}code{background:#2a2f36}}")


def to_html(c: dict[str, Any]) -> str:
    e = _html.escape
    o, n = c["old"], c["new"]

    def sect(title: str, cls: str, items: list[dict[str, str]], quote: bool) -> str:
        rows = "".join(
            f"<li class='{cls}'><code>{e(i['fid'])}</code> [{e(i['severity'])}] {e(i['title'])}"
            + (f"<div class='q'>Было в тексте: «{e(i['quote'])}»</div>" if quote and i["quote"] else "") + "</li>"
            for i in items) or "<li>Нет.</li>"
        return f"<h2>{e(title)} ({len(items)})</h2><ul>{rows}</ul>"
    body = (f"<h1>Сравнение двух версий ТЗ</h1><p>Было: {e(o['doc'] or '—')} ({e(o['when'] or '—')})<br>"
            f"Стало: {e(n['doc'] or '—')} ({e(n['when'] or '—')})</p>"
            f"<div class='sum'>{e(summary_line(c))}</div><ul>" + "".join(f"<li>{e(x)}</li>" for x in _dynamics(c)) + "</ul>"
            + sect("Закрыто", "c", c["closed"], True) + sect("Осталось", "r", c["remaining"], False)
            + sect("Новые", "n", c["new_items"], False))
    return ("<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Сравнение версий ТЗ</title><style>{_CSS}</style></head><body>{body}</body></html>\n")


def run(old_path: str, new_path: str, output: str | None, say) -> int:
    try:
        c = compare(load(old_path), load(new_path))
    except ValueError as e:
        say(f"Сравнение не выполнено: {e}")
        return 1
    base = Path(output) if output else Path(new_path)
    stem = base.name
    for suf in (".md", ".json", ".html"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    md, hp = base.with_name(stem + ".compare.md"), base.with_name(stem + ".compare.html")
    try:
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(to_markdown(c), encoding="utf-8")
        hp.write_text(to_html(c), encoding="utf-8")
    except OSError as e:
        say(f"Сравнение не записано: {e}")
        return 1
    say(summary_line(c))
    for x in _dynamics(c)[:2]:
        say(x)
    say(f"Записано: {md}")
    say(f"Записано: {hp}")
    return 0
