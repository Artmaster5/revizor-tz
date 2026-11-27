"""Исправленная редакция документа целиком из сохранённого отчёта.

    python tools/fixed_document.py reports/report_answered.json --input docs/requirements.md [--out-dir reports]

Берёт из отчёта все правки «было → стало», прошедшие проверку смысла (``revizor/rewrite_gate.py``), и заменяет «было»
на «стало» в самом документе, только если фрагмент «было» найден в документе дословно ровно один раз. Всё остальное
остаётся без изменений, байт в байт. Не вставленное в заголовке перечислено с причиной и не навязывается.
Пишет ``<имя>_fixed.md`` и ``<имя>_fixed.docx`` (минимальный OOXML, изменённые места выделены). Модель не нужна.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class Placed:
    fids: list[str]
    rid: str
    line: int
    before: str
    after: str


@dataclass
class Skipped:
    fids: list[str]
    rid: str
    before: str
    reason: str


def apply_rewrites(text: str, rows) -> tuple[list[tuple[str, bool]], list[Placed], list[Skipped]]:
    """rows: объекты с rid, before, after, fids. Возвращает куски итогового текста (текст, изменён ли), вставленное и нет."""
    found: list[tuple[int, int, object]] = []
    skipped: list[Skipped] = []
    for r in rows:
        before, after = r.before or "", r.after or ""
        sk = lambda why: skipped.append(Skipped(list(r.fids), r.rid, before, why))
        if not before.strip():
            sk("нет исходного текста")
            continue
        n = text.count(before)
        if n == 0:
            sk("фрагмент «было» не найден в документе дословно")
        elif n > 1:
            sk(f"фрагмент «было» встречается в документе {n} раза: место неоднозначно")
        elif before == after:
            sk("«стало» совпадает с «было»")
        else:
            s = text.find(before)
            found.append((s, s + len(before), r))
    found.sort(key=lambda t: t[0])
    chosen: list[tuple[int, int, object]] = []
    for s, e, r in found:
        if chosen and s < chosen[-1][1]:
            skipped.append(Skipped(list(r.fids), r.rid, r.before, "пересекается с другой правкой: место занято"))
        else:
            chosen.append((s, e, r))
    segs: list[tuple[str, bool]] = []
    placed: list[Placed] = []
    pos = 0
    for s, e, r in chosen:
        if s > pos:
            segs.append((text[pos:s], False))
        segs.append((r.after, True))
        placed.append(Placed(list(r.fids), r.rid, text.count("\n", 0, s) + 1, r.before, r.after))
        pos = e
    if pos < len(text):
        segs.append((text[pos:], False))
    return segs, placed, skipped


def _one(s: str, limit: int = 700) -> str:
    s = re.sub(r"\s+", " ", s).strip().replace("|", "\\|")
    return s if len(s) <= limit else s[:limit - 1] + "…"


def header(report_name: str, doc_name: str, placed: list[Placed], skipped: list[Skipped]) -> str:
    out = [f"# Исправленная редакция документа «{doc_name}»", "",
           f"Источник правок: отчёт `{report_name}`. Заменено мест: {len(placed)}. Не вставлено: {len(skipped)}. "
           "Заменён только текст, найденный в документе дословно ровно один раз; остальной документ не менялся. "
           "Каждую правку перед принятием должен прочитать автор.", ""]
    if placed:
        out += ["**Что заменено**", ""]
        for p in placed:
            out.append(f"- строка {p.line} ({', '.join(p.fids)}): было: «{_one(p.before)}» → стало: «{_one(p.after)}»")
        out.append("")
    if skipped:
        out += ["**Не вставлено**", ""]
        for k in skipped:
            out.append(f"- {k.rid or '—'} ({', '.join(k.fids)}): не вставлено: {k.reason}")
        out.append("")
    out += ["---", ""]
    return "\n".join(out) + "\n"


def to_markdown(head: str, segs: list[tuple[str, bool]]) -> str:
    return head + "".join(t for t, _c in segs)


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _run(text: str, bold: bool = False, mark: bool = False, size: int | None = None) -> str:
    pr = ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "") + \
         ('<w:highlight w:val="yellow"/>' if mark else "")
    return f'<w:r>{"<w:rPr>" + pr + "</w:rPr>" if pr else ""}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def to_docx_bytes(head: str, segs: list[tuple[str, bool]]) -> bytes:
    import io
    # строки итогового текста; каждая строка — список (текст, изменён)
    lines: list[list[tuple[str, bool]]] = [[]]
    for t, ch in [(head, False)] + list(segs):
        parts = t.split("\n")
        for i, part in enumerate(parts):
            if i:
                lines.append([])
            if part:
                lines[-1].append((part, ch))
    paras = []
    for ln in lines:
        flat = "".join(t for t, _c in ln)
        m = re.match(r"(#{1,6})\s+", flat)
        level = len(m.group(1)) if m else 0
        runs, skip = [], len(m.group(0)) if m else 0
        for t, ch in ln:
            if skip:
                cut = min(skip, len(t))
                t, skip = t[cut:], skip - cut
            if t:
                runs.append(_run(t, bold=bool(level) or ch, mark=ch, size=(40 - 4 * level) if level else None))
        paras.append("<w:p>" + "".join(runs) + "</w:p>")
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{_W}"><w:body>'
                + "".join(paras) + "</w:body></w:document>")
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
          'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/'
          'document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"'
          '/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def build(result, text: str, report_name: str, doc_name: str):
    from revizor import rewrite_gate
    rows, _withheld = rewrite_gate.plan(result)
    segs, placed, skipped = apply_rewrites(text, rows)
    head = header(report_name, doc_name, placed, skipped)
    return head, segs, placed, skipped


def main(argv: list[str] | None = None) -> int:
    from revizor import loader, report_json
    from revizor.model import Diagnostic
    ap = argparse.ArgumentParser(description="Исправленная редакция документа целиком по правкам «было → стало».")
    ap.add_argument("report", help="JSON-отчёт агента")
    ap.add_argument("--input", required=True, help="проверенный документ (SHA-256 должен совпасть с отчётом)")
    ap.add_argument("--out-dir", default=None, help="куда писать (по умолчанию рядом с отчётом)")
    args = ap.parse_args(argv)
    src = Path(args.report).resolve()
    result = report_json.load_result(src)
    doc = loader.load(args.input)
    if isinstance(doc, Diagnostic):
        print(f"Документ не прочитан: {doc.title}", file=sys.stderr)
        return 1
    if result.run.doc_sha256 and doc.sha256 != result.run.doc_sha256:
        print("SHA-256 документа не совпадает с отчётом: это другой документ.", file=sys.stderr)
        return 1
    text = Path(args.input).read_bytes().decode("utf-8")
    head, segs, placed, skipped = build(result, text, src.name, Path(args.input).name)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.input).stem
    md = out_dir / f"{stem}_fixed.md"
    dx = out_dir / f"{stem}_fixed.docx"
    md.write_text(to_markdown(head, segs), encoding="utf-8", newline="")
    dx.write_bytes(to_docx_bytes(head, segs))
    print(f"Заменено мест: {len(placed)}, не вставлено: {len(skipped)}.")
    print(md)
    print(dx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
