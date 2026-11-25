"""Build the example and test fixture files.

    python tools/make_fixtures.py

Writes:
  examples/requirements.pdf          the organisers' document rendered to PDF (the PDF path)
  examples/broken/*                  one file per failure the loader must explain (no exceptions)
  tests/fixtures/*                   encodings, DOCX, HTML, plain text, encrypted / empty PDFs

Only this tool needs fpdf2 (requirements-dev.txt); the agent itself reads PDFs with pypdf.
The PDF needs a TrueType font with Cyrillic: Arial Unicode / Arial (macOS), DejaVu Sans (Linux) or Arial (Windows).
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
BROKEN = EXAMPLES / "broken"
FIX = ROOT / "tests" / "fixtures"
SOURCE_MD = ROOT / "docs" / "requirements.md"

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

FALLBACK_FONTS = ["/System/Library/Fonts/SFNS.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


def _font() -> str:
    for f in FONT_CANDIDATES:
        if os.path.exists(f):
            return f
    sys.exit("Не найден шрифт с кириллицей (Arial Unicode, Arial или DejaVu Sans). "
             "Установите один из них или впишите путь в FONT_CANDIDATES в tools/make_fixtures.py.")


def _fpdf():
    try:
        from fpdf import FPDF
    except ImportError:
        sys.exit("Нужен fpdf2: pip install -r requirements-dev.txt")
    return FPDF


def _text_pdf(lines: list[str], *, size: float = 8, encrypt_user: str | None = None) -> bytes:
    FPDF = _fpdf()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_font("Body", "", _font())
    for fb in FALLBACK_FONTS:  # Arial has no tenge sign; Geneva / DejaVu do
        if os.path.exists(fb):
            pdf.add_font("Fallback", "", fb)
            pdf.set_fallback_fonts(["Fallback"])
            break
    pdf.set_font("Body", size=size)
    if encrypt_user is not None:
        from fpdf.enums import EncryptionMethod
        pdf.set_encryption(owner_password="owner-only", user_password=encrypt_user,
                           encryption_method=EncryptionMethod.RC4)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin
    for line in lines:
        pdf.multi_cell(width, size * 0.5, line if line.strip() else " ", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _md_pdf(md: str) -> bytes:
    """Render Markdown the way a word processor would print it: headings as lines, tables as spaced rows, no markup."""
    import re
    FPDF = _fpdf()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_font("Body", "", _font())
    for fb in FALLBACK_FONTS:
        if os.path.exists(fb):
            pdf.add_font("Fallback", "", fb)
            pdf.set_fallback_fonts(["Fallback"])
            break
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin
    in_code = False
    table: list[list[str]] = []

    def clean(t: str) -> str:
        t = re.sub(r"\*\*|__|`", "", t).replace("&nbsp;", " ")
        return re.sub(r"<br\s*/?>", " ", t).rstrip("\\ ")

    def flush_table() -> None:
        # a real table with ruled cells, as a word processor exports it
        if not table:
            return
        pdf.set_font("Body", size=8)
        ncol = max(len(r) for r in table)
        with pdf.table(text_align="LEFT", line_height=4, first_row_as_headings=False) as t:
            for row in table:
                r = t.row()
                for c in row + [""] * (ncol - len(row)):
                    r.cell(c)
        pdf.ln(2)
        table.clear()

    for line in md.split("\n"):
        if line.strip().startswith("```"):
            flush_table()
            in_code = not in_code
            continue
        size, text = 9.0, line
        if not in_code:
            if re.match(r"^\s*\|?\s*:?-{3,}", line) and "|" in line:
                continue  # table separator row
            if line.strip().startswith("|"):
                table.append([clean(c.strip()) for c in line.strip().strip("|").split("|")])
                continue
            flush_table()
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                size, text = (16.0, 14.0, 12.0, 10.5, 10.0, 10.0)[len(m.group(1)) - 1], m.group(2)
                pdf.ln(2)
            elif re.match(r"^\s*-{3,}\s*$", line):
                continue
            text = clean(text)
        pdf.set_font("Body", size=size)
        pdf.multi_cell(width, size * 0.5, text if text.strip() else " ", new_x="LMARGIN", new_y="NEXT")
    flush_table()
    return bytes(pdf.output())


def _scan_pdf() -> bytes:
    """Two pages with drawings only — what a scanned document without OCR looks like to a text extractor."""
    FPDF = _fpdf()
    pdf = FPDF(format="A4")
    for _ in range(2):
        pdf.add_page()
        for k in range(12):
            pdf.rect(20, 20 + k * 20, 170, 12, style="F" if k % 3 == 0 else "D")
    return bytes(pdf.output())


def _no_pages_pdf() -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [] /Count 0 >>"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for k, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % k + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

# --- DOCX built by hand (no python-docx dependency) -----------------------------------------------

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {_W}>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="2"><w:name w:val="Заголовок 2"/></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/></w:style>
</w:styles>"""


def _p(text: str, style: str | None = None, numbered: bool = False) -> str:
    ppr = ""
    if style or numbered:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              ('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "") + "</w:pPr>"
    # split the text into two runs to make sure runs are joined back
    half = len(text) // 2
    runs = "".join(f'<w:r><w:t xml:space="preserve">{_x(t)}</w:t></w:r>' for t in (text[:half], text[half:]) if t)
    return f"<w:p>{ppr}{runs}</w:p>"


def _x(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tbl(rows: list[list[str]]) -> str:
    body = "".join("<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows)
    return f"<w:tbl>{body}</w:tbl>"


def _docx() -> bytes:
    body = "".join([
        _p("Спецификация: выписка по счёту", "Heading1"),
        _p("Версия 1.0 от 01.11.2026."),
        _p("Требования", "2"),
        _p("ВП-1. Клиент заказывает выписку за период до 12 месяцев.", numbered=True),
        _p("ВП-2. Выписка формируется быстро.", numbered=True),
        _p("Поля выписки", "2"),
        _tbl([["Поле", "Тип", "Описание"], ["date", "string", "Дата операции"], ["amount", "integer", "Сумма в тиынах"]]),
    ])
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {_W}><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", _STYLES)
    return out.getvalue()


def _zip_without_document() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("word/styles.xml", _STYLES)
    return out.getvalue()


# --- texts ------------------------------------------------------------------------------------

CP1251_TEXT = """ТЗ: уведомления о входе в интернет-банк

1. Требования

1.1. Система отправляет клиенту push-уведомление о каждом входе с нового устройства в течение 30 секунд.
1.2. В уведомлении указываются дата, время, город и модель устройства.
1.3. Клиент может заблокировать вход из уведомления одной кнопкой.
"""

UTF16_TEXT = """Требования к экрану истории операций

Экран показывает операции за последние 90 дней, по 20 операций на странице.
Операции сортируются по дате и времени проведения, новые сверху.
Поиск работает по сумме, имени получателя и назначению платежа.
Каждая операция открывается в карточке с квитанцией, которую можно сохранить в PDF.
"""

PLAIN_SPEC = """ТЕХНИЧЕСКОЕ ЗАДАНИЕ
на сервис напоминаний о платежах

Версия: 1.0
Дата: 10.11.2026

1. ТРЕБОВАНИЯ К НАПОМИНАНИЯМ

1.1. Сервис отправляет клиенту напоминание о платеже по кредиту за 3 календарных дня
     до даты платежа, в 10:00 по времени Астаны.
1.2. Если платёж уже внесён, напоминание не отправляется.
1.3. Клиент отключает напоминания в настройках приложения.

2. КАНАЛЫ

2.1. Каналы отправки и порядок их выбора:

     N   Канал        Условие
     1   push         приложение установлено и уведомления разрешены
     2   SMS          push недоступен
     3   e-mail       нет номера телефона

3. СОГЛАСОВАНИЕ

     Подразделение                     ФИО              Дата
     Управление розничных продуктов
     Служба информационной безопасности
"""

HTML_DOC = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ТЗ</title><style>p{color:red}</style>
<script>var ignored = "этот текст не должен попасть в документ";</script></head>
<body>
<h1>Спецификация кешбэка</h1>
<p>Версия 2.0 от 05.11.2026.</p>
<h2>Требования</h2>
<ol>
<li>КБ-1. Кешбэк начисляется в размере 1% от суммы покупки, но не более 5 000 ₸ в месяц.</li>
<li>КБ-2. Кешбэк зачисляется на бонусный счёт до 5-го числа следующего месяца.</li>
</ol>
<!-- черновик: уточнить у маркетинга -->
<h2>Категории</h2>
<table>
<tr><th>Категория</th><th>Ставка</th></tr>
<tr><td>Кафе и рестораны</td><td>3%</td></tr>
<tr><td>Прочее</td><td>1%</td></tr>
</table>
<pre>{"status": "ok"}</pre>
</body></html>
"""

EMPTY_HTML = """<!DOCTYPE html><html><head><title>x</title><script>console.log("nothing to read here at all");</script>
<style>body { font-family: sans-serif; }</style></head><body><div></div></body></html>
"""


def _write(path: Path, data: bytes | str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode(encoding)
    path.write_bytes(data)
    print(f"  {path.relative_to(ROOT)}  ({len(data)} байт)")


def main() -> None:
    src = SOURCE_MD.read_text(encoding="utf-8")
    real_pdf = _md_pdf(src)
    print("Примеры:")
    _write(EXAMPLES / "requirements.pdf", real_pdf)
    _write(BROKEN / "empty.md", b"")
    _write(BROKEN / "spaces.txt", "   \n\n\t\t  \n    \n" * 20)
    _write(BROKEN / "scan_no_text.pdf", _scan_pdf())
    _write(BROKEN / "broken.pdf", real_pdf[: len(real_pdf) // 3])
    _write(BROKEN / "fake.pdf", PNG_BYTES)
    _write(BROKEN / "broken.docx", "Это не документ Word, а обычный текст с расширением .docx. " * 3)
    _write(BROKEN / "tiny.md", "# ТЗ\n\nСделать перевод.\n")
    print("Дополнительные случаи (tests/fixtures):")
    _write(FIX / "encrypted.pdf", _text_pdf(["Секретное ТЗ " * 5] * 10, encrypt_user="secret"))
    _write(FIX / "no_pages.pdf", _no_pages_pdf())
    _write(FIX / "old_format.doc", bytes.fromhex("d0cf11e0a1b11ae1") + bytes(504))
    _write(FIX / "binary.txt", bytes(range(256)) * 8)
    _write(FIX / "no_document.docx", _zip_without_document())
    _write(FIX / "image.png.txt", PNG_BYTES + bytes(300))
    _write(FIX / "cp1251.txt", CP1251_TEXT, "cp1251")
    _write(FIX / "utf16.txt", UTF16_TEXT, "utf-16")
    _write(FIX / "sample.docx", _docx())
    _write(FIX / "sample.html", HTML_DOC)
    _write(FIX / "empty_text.html", EMPTY_HTML)
    _write(FIX / "plain_spec.txt", PLAIN_SPEC)
    print("Готово.")


if __name__ == "__main__":
    main()
