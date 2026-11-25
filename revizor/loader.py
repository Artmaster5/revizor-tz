"""File → SourceDoc (text + blocks) or Diagnostic. Never raises."""
from __future__ import annotations

import hashlib
import html
import io
import logging
import os
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from .model import Diagnostic, SourceDoc
from .privacy import mask_sensitive
from .structure import INVISIBLE_CHARS, parse_blocks

MAX_CHARS = 400_000
MAX_BYTES = 60 * 1024 * 1024
# A document is rejected only when it cannot hold a requirement: fewer than MIN_WORDS words, or no line of at least
# three words. A short but real list of requirements is reviewed, with a warning (SHORT_CHARS / SHORT_LINES).
MIN_WORDS = 12
SHORT_CHARS = 200
SHORT_LINES = 3

# --- diagnostics ----------------------------------------------------------------

_CONVERT = "Если есть исходный файл в Word — сохраните его заново как .docx («Файл → Сохранить как»)."


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for the integer n: 1 строка / 2-4 строки / 5+ и 11-14 строк."""
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def _diag(code: str, path: str, *, details: str = "", size: int | None = None, detected: str = "", **fmt) -> Diagnostic:
    title, what, todo = _TEXTS[code]
    if isinstance(fmt.get("lines"), int):
        fmt["lines"] = "%d %s" % (fmt["lines"], _plural(fmt["lines"], "непустая строка", "непустые строки", "непустых строк"))
    if isinstance(fmt.get("chars"), int):
        fmt["chars"] = "%d %s" % (fmt["chars"], _plural(fmt["chars"], "знак", "знака", "знаков"))
    name = os.path.basename(path) if path else ""
    return Diagnostic(
        code=code,
        title=title.format(**fmt),
        what_happened=what.format(**fmt),
        what_to_do=[t.format(**fmt) for t in todo],
        details=details[:500],
        path=str(path),
        name=name,
        size_bytes=size,
        detected_type=detected,
    )


_TEXTS: dict[str, tuple[str, str, list[str]]] = {
    "NOT_FOUND": (
        "Файл не найден",
        "По указанному пути нет файла. Возможно, в пути опечатка или файл лежит в другой папке.",
        ["Проверьте путь и имя файла, включая расширение (.md, .txt, .pdf, .docx, .html).",
         "Если путь содержит пробелы, возьмите его в кавычки.",
         "Запустите команду ещё раз."],
    ),
    "IS_DIRECTORY": (
        "Указана папка, а не файл",
        "Агент проверяет один документ за раз, а по этому пути находится папка.",
        ["Укажите путь к самому файлу внутри папки, например папка/требования.md.",
         "Запустите команду ещё раз."],
    ),
    "NO_PERMISSION": (
        "Нет прав на чтение файла",
        "Файл существует, но у программы нет разрешения его прочитать.",
        ["Проверьте права доступа к файлу или скопируйте его в свою папку.",
         "Если файл открыт в другой программе с блокировкой — закройте её.",
         "Запустите команду ещё раз."],
    ),
    "READ_ERROR": (
        "Файл не удалось прочитать",
        "При чтении файла произошла ошибка ввода-вывода.",
        ["Скопируйте файл на локальный диск и повторите запуск.",
         "Если файл лежит на сетевом диске или флешке — проверьте подключение."],
    ),
    "EMPTY_FILE": (
        "Файл пустой",
        "Размер файла 0 байт: в нём нет ни одного символа, проверять нечего.",
        ["Убедитесь, что выбран правильный файл.",
         "Если документ сохраняли из редактора — сохраните его ещё раз и проверьте, что текст на месте."],
    ),
    "ONLY_WHITESPACE": (
        "В файле только пробелы и пустые строки",
        "Файл не пустой, но в нём нет ни одного видимого символа текста.",
        ["Убедитесь, что выбран правильный файл.",
         "Откройте файл в редакторе и проверьте, что текст требований сохранился."],
    ),
    "TOO_SHORT": (
        "Слишком мало текста, чтобы это был документ с требованиями",
        "В файле {chars} текста и {lines}. Для проверки нужен документ хотя бы из нескольких требований.",
        ["Убедитесь, что выбран полный документ, а не заготовка или фрагмент.",
         "Если документ большой, а прочитался кусок — сохраните его в другом формате (.docx или .md) и повторите."],
    ),
    "TOO_LARGE": (
        "Документ слишком большой для одной проверки",
        "В документе {chars} знаков, а за один прогон агент проверяет не больше {limit}.",
        ["Разбейте документ на части по разделам (например, отдельно функциональные требования и описание API).",
         "Проверьте каждую часть отдельным запуском."],
    ),
    "BINARY": (
        "Файл не похож на текстовый документ",
        "В файле много служебных или нечитаемых символов: похоже, это двоичный файл или текст в неизвестной кодировке.",
        ["Убедитесь, что выбран документ с требованиями, а не архив, изображение или программа.",
         "Сохраните документ как .md, .txt (кодировка UTF-8), .docx или текстовый PDF."],
    ),
    "WRONG_TYPE": (
        "Файл не того формата: на самом деле это {real}",
        "Расширение файла говорит одно, а содержимое — другое: внутри {real}. Такой файл агент прочитать не может.",
        ["Откройте файл и проверьте, что это документ с требованиями.",
         "Сохраните требования в одном из форматов: .md, .txt, .docx, .html или текстовый PDF."],
    ),
    "PDF_BROKEN": (
        "PDF повреждён и не читается",
        "Файл начинается как PDF, но его внутренняя структура повреждена — программа не смогла его разобрать.",
        ["Откройте файл в программе просмотра PDF: если он не открывается и там — получите исходный файл заново.",
         "Если открывается — пересохраните его через «Печать → Сохранить как PDF» и повторите.",
         _CONVERT],
    ),
    "PDF_ENCRYPTED": (
        "PDF защищён паролем",
        "Текст в файле зашифрован, без пароля его прочитать нельзя.",
        ["Попросите автора прислать версию без пароля.",
         "Или откройте файл с паролем и сохраните копию без защиты, затем повторите проверку."],
    ),
    "PDF_NO_PAGES": (
        "В PDF нет ни одной страницы",
        "Файл является PDF, но не содержит страниц.",
        ["Получите исходный документ заново и сохраните его в PDF ещё раз.", _CONVERT],
    ),
    "PDF_NO_TEXT": (
        "В PDF нет текстового слоя — похоже на скан",
        "Страницы PDF состоят из картинок: в среднем меньше {per_page} знаков текста на страницу. Агент читает только текст, распознавать изображения он не умеет.",
        ["Найдите исходный документ (Word, Confluence) и сохраните его как .docx или текстовый PDF.",
         "Если исходника нет — распознайте скан программой OCR (например, «Распознать текст» в просмотрщике PDF) и сохраните результат.",
         "Повторите проверку."],
    ),
    "DOCX_BROKEN": (
        "Файл Word (.docx) повреждён",
        "Файл .docx — это архив с текстом внутри, но он не открывается или в нём нет основной части документа.",
        ["Откройте файл в Word: если Word предложит восстановить — согласитесь и сохраните заново.",
         "Или сохраните документ как PDF или .md и повторите проверку."],
    ),
    "DOC_OLD_FORMAT": (
        "Старый формат Word (.doc) не поддерживается",
        "Файл сохранён в формате Word 97–2003. Агент читает современный .docx, текстовый PDF, .md, .txt и .html.",
        ["Откройте файл в Word и выберите «Файл → Сохранить как → Документ Word (.docx)».",
         "Повторите проверку с новым файлом."],
    ),
}

# --- magic bytes ---------------------------------------------------------------------

_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "изображение PNG"),
    (b"\xff\xd8\xff", "изображение JPEG"),
    (b"GIF87a", "изображение GIF"),
    (b"GIF89a", "изображение GIF"),
    (b"BM", None),  # checked separately (too short a signature)
    (b"II*\x00", "изображение TIFF"),
    (b"MM\x00*", "изображение TIFF"),
    (b"Rar!\x1a\x07", "архив RAR"),
    (b"7z\xbc\xaf\x27\x1c", "архив 7-Zip"),
    (b"\x1f\x8b", "архив GZIP"),
    (b"\x7fELF", "программа (исполняемый файл)"),
    (b"MZ", None),
    (b"{\\rtf", "документ RTF"),
    (b"ID3", "аудиофайл MP3"),
    (b"OggS", "аудио/видео OGG"),
    (b"\x00\x00\x00 ftyp", "видео MP4"),
    (b"RIFF", None),
]


def _sniff_binary(data: bytes) -> str:
    for sig, name in _MAGIC:
        if name and data.startswith(sig):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "изображение WEBP"
    if len(data) > 8 and data[4:8] == b"ftyp":
        return "видео или изображение (MP4/HEIC)"
    if data.startswith(b"MZ") and b"This program" in data[:200]:
        return "программа Windows (.exe)"
    if data.startswith(b"BM") and len(data) > 26 and data[6:10] == b"\x00\x00\x00\x00":
        return "изображение BMP"
    return ""


_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# --- text decoding -------------------------------------------------------------


def _bad_share(text: str) -> float:
    if not text:
        return 0.0
    bad = 0
    for c in text:
        o = ord(c)
        if c == "�" or (o < 32 and c not in "\t\n\r\f\v") or 0x7F <= o < 0xA0:
            bad += 1
    return bad / len(text)


def _decode(data: bytes) -> tuple[str | None, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            pass
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            return None, "utf-16"
    # UTF-16 without BOM: every second byte is zero for Latin/Cyrillic-heavy text
    sample = data[:4000]
    if len(sample) >= 8:
        zeros_even = sample[0::2].count(0) / max(1, len(sample[0::2]))
        zeros_odd = sample[1::2].count(0) / max(1, len(sample[1::2]))
        if zeros_odd > 0.3 and zeros_even < 0.05:
            try:
                return data.decode("utf-16-le"), "utf-16-le"
            except UnicodeDecodeError:
                pass
        if zeros_even > 0.3 and zeros_odd < 0.05:
            try:
                return data.decode("utf-16-be"), "utf-16-be"
            except UnicodeDecodeError:
                pass
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1251")
        return text, "cp1251"
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), "utf-8 (с ошибками)"


# --- DOCX ----------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_styles(zf: zipfile.ZipFile) -> dict[str, tuple[int, bool]]:
    """styleId → (heading level or 0, is list style)."""
    out: dict[str, tuple[int, bool]] = {}
    try:
        root = ElementTree.fromstring(zf.read("word/styles.xml"))
    except Exception:
        return out
    for st in root.iter(_W + "style"):
        sid = st.get(_W + "styleId", "")
        name_el = st.find(_W + "name")
        name = (name_el.get(_W + "val", "") if name_el is not None else "").lower()
        level = 0
        m = re.match(r"(?:heading|заголовок)\s*(\d)", name) or re.match(r"(?:heading|заголовок)\s*(\d)", sid.lower())
        if m:
            level = int(m.group(1))
        elif name in ("title", "название", "заглавие"):
            level = 1
        else:
            ol = st.find(f"{_W}pPr/{_W}outlineLvl")
            if ol is not None and (ol.get(_W + "val") or "").isdigit():
                level = int(ol.get(_W + "val")) + 1
        out[sid] = (min(level, 6), "list" in name or "список" in name)
    return out


def _run_text(p) -> str:
    parts = []
    for el in p.iter():
        if el.tag == _W + "t":
            parts.append(el.text or "")
        elif el.tag == _W + "tab":
            parts.append(" ")
        elif el.tag in (_W + "br", _W + "cr"):
            parts.append(" ")
    return "".join(parts)


def _docx_paragraph(p, styles) -> str:
    text = re.sub(r"\s+", " ", _run_text(p)).strip()
    if not text:
        return ""
    ppr = p.find(_W + "pPr")
    level, is_list = 0, False
    if ppr is not None:
        st = ppr.find(_W + "pStyle")
        if st is not None:
            sid = st.get(_W + "val", "")
            level, is_list = styles.get(sid, (0, False))
            if not level:
                m = re.match(r"(?i)(?:heading|заголовок)\s*(\d)", sid)
                level = int(m.group(1)) if m else 0
        ol = ppr.find(_W + "outlineLvl")
        if not level and ol is not None and (ol.get(_W + "val") or "").isdigit():
            level = min(int(ol.get(_W + "val")) + 1, 6)
        if ppr.find(_W + "numPr") is not None:
            is_list = True
    if level:
        return "#" * level + " " + text
    if is_list:
        return "- " + text
    return text


def _cell_md(s: str) -> str:
    return s.replace("|", "\\|")


def _docx_table(tbl, styles) -> list[str]:
    rows = []
    for tr in tbl.findall(_W + "tr"):
        cells = []
        for tc in tr.findall(_W + "tc"):
            texts = [re.sub(r"\s+", " ", _run_text(p)).strip() for p in tc.iter(_W + "p")]
            cells.append(_cell_md(" ".join(t for t in texts if t)))
        rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return [""] + out + [""]


def _docx_body(el, styles, out: list[str]) -> None:
    for child in el:
        if child.tag == _W + "p":
            line = _docx_paragraph(child, styles)
            if line:
                out.append(line)
                if line.startswith("#"):
                    out.append("")
                elif not line.startswith("- "):
                    out.append("")
        elif child.tag == _W + "tbl":
            out.extend(_docx_table(child, styles))
        elif child.tag in (_W + "sdt", _W + "sdtContent", _W + "customXml"):
            _docx_body(child, styles, out)


def docx_to_markdown(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
        styles = _docx_styles(zf)
    root = ElementTree.fromstring(xml)
    body = root.find(_W + "body")
    out: list[str] = []
    if body is not None:
        _docx_body(body, styles, out)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


# --- HTML ----------------------------------------------------------------------------


class _HtmlToMd(HTMLParser):
    BLOCK = {"p", "div", "section", "article", "header", "footer", "blockquote", "dl", "dt", "dd", "br", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.cur: list[str] = []
        self.skip = 0
        self.pre = 0
        self.list_depth = 0
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.heading = 0
        self.tables_stack: list = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        self.cur = []
        if not text:
            return
        if self.heading:
            self.out += ["", "#" * self.heading + " " + text, ""]
        elif self.list_depth:
            self.out.append("  " * (self.list_depth - 1) + "- " + text)
        else:
            self.out += [text, ""]

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "noscript", "template"):
            self.skip += 1
            return
        if self.skip:
            return
        if self.cell is not None and tag not in ("td", "th", "tr", "table"):
            if tag == "br":
                self.cell.append(" ")
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self.heading = int(tag[1])
        elif tag in ("ul", "ol"):
            self._flush()
            self.list_depth += 1
        elif tag == "li":
            self._flush()
        elif tag == "pre":
            self._flush()
            self.pre += 1
            self.out.append("```")
        elif tag == "table":
            self._flush()
            if self.table is not None:
                self.tables_stack.append((self.table, self.row, self.cell))
            self.table, self.row, self.cell = [], None, None
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag in self.BLOCK and not self.pre:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "noscript", "template"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(_cell_md(re.sub(r"\s+", " ", "".join(self.cell)).strip()))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            rows = self.table
            if self.tables_stack:
                self.table, self.row, self.cell = self.tables_stack.pop()
                if self.cell is not None:
                    self.cell.append(" ".join(" ".join(r) for r in rows))
                return
            self.table, self.row, self.cell = None, None, None
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                self.out += ["", "| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
                self.out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.out.append("")
        elif self.cell is not None:
            return
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self.heading = 0
        elif tag in ("ul", "ol"):
            self._flush()
            self.list_depth = max(0, self.list_depth - 1)
            if not self.list_depth:
                self.out.append("")
        elif tag == "li":
            self._flush()
        elif tag == "pre":
            code = "".join(self.cur).strip("\n")
            self.cur = []
            self.out += code.split("\n") + ["```", ""]
            self.pre = max(0, self.pre - 1)
        elif tag in self.BLOCK and not self.pre:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        if self.cell is not None:
            self.cell.append(data)
        else:
            self.cur.append(data)

    def handle_comment(self, data):
        if self.skip:
            return
        self._flush()
        self.out += ["<!--" + data.replace("\n", " ") + "-->", ""]


def html_to_markdown(text: str) -> str:
    p = _HtmlToMd()
    p.feed(text)
    p.close()
    p._flush()
    md = "\n".join(p.out)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def _looks_html(text: str) -> bool:
    head = text[:2000].lower()
    return bool(re.search(r"<!doctype html|<html[\s>]|<body[\s>]", head))


# --- PDF -------------------------------------------------------------------------------


def _load_pdf(data: bytes, path: str, size: int):
    try:
        from pypdf import PdfReader
    except Exception as e:  # pragma: no cover - dependency missing
        return _diag("PDF_BROKEN", path, size=size, details=f"pypdf не установлен: {e}")
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception as e:
                return _diag("PDF_ENCRYPTED", path, size=size, details=str(e))
            if not ok:
                return _diag("PDF_ENCRYPTED", path, size=size, details="пустой пароль не подошёл")
        n = len(reader.pages)
        if n == 0:
            return _diag("PDF_NO_PAGES", path, size=size)
        texts = []
        page_heads: list[dict[str, float]] = []
        body_sizes: dict[float, int] = {}
        for page in reader.pages:
            plain, sized = _pdf_page_plain(page)
            for size_, txt in sized:
                body_sizes[size_] = body_sizes.get(size_, 0) + len(txt)
            texts.append(_pdf_page_layout(page, plain))
            page_heads.append({})
            for size_, txt in sized:
                page_heads[-1][txt] = max(size_, page_heads[-1].get(txt, 0.0))
    except Exception as e:
        msg = type(e).__name__ + ": " + str(e)
        if "encrypt" in msg.lower() or "password" in msg.lower() or "crypt" in msg.lower():
            return _diag("PDF_ENCRYPTED", path, size=size, details=msg)
        return _diag("PDF_BROKEN", path, size=size, details=msg)
    non_space = sum(len(re.sub(r"\s", "", t)) for t in texts)
    if non_space / n < 40:
        return _diag("PDF_NO_TEXT", path, size=size, per_page=40, details=f"страниц: {n}, знаков текста: {non_space}")
    # text set in a font noticeably larger than the body font is a heading; bigger font → higher level
    body = max(body_sizes, key=body_sizes.get) if body_sizes else 0.0
    big = sorted({s_ for heads in page_heads for s_ in heads.values() if body and s_ >= body * 1.15}, reverse=True)
    level_of = {s_: min(k + 1, 6) for k, s_ in enumerate(big)}
    lines: list[str] = []
    line_pages: list[int] = []
    headings: dict[int, int] = {}
    for pno, t in enumerate(texts, 1):
        page_lines = t.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        page_lines = [ln.rstrip() for ln in page_lines]
        heads = {txt: level_of[s_] for txt, s_ in page_heads[pno - 1].items() if s_ in level_of}
        for k, ln in enumerate(page_lines):
            key = re.sub(r"\s+", " ", ln).strip()
            if key and key in heads:
                headings[len(lines) + k + 1] = heads[key]
        if pno < n:
            page_lines.append("")
        lines += page_lines
        line_pages += [pno] * len(page_lines)
    return "\n".join(lines), line_pages, n, headings


def _pdf_page_plain(page) -> tuple[str, list[tuple[float, str]]]:
    """Plain extraction of one page plus (font size, line text) pairs from the text-drawing operators."""
    runs: dict[tuple[float, float], list[str]] = {}

    def visit(text, cm, tm, font_dict, font_size):
        if not text or not text.strip():
            return
        try:
            scale = abs(float(tm[3]) or 1.0) * abs(float(cm[3]) or 1.0)
            eff = round(float(font_size or 0) * scale, 1)
            y = round(float(tm[5]) * abs(float(cm[3]) or 1.0) + float(cm[5]), 0)
        except Exception:
            return
        runs.setdefault((y, eff), []).append(text)

    try:
        plain = page.extract_text(visitor_text=visit) or ""
    except Exception as e:  # one broken page should not kill the rest
        logging.getLogger(__name__).debug("page error: %s", e)
        return "", []
    sized = []
    for (_, eff), parts in runs.items():
        txt = re.sub(r"\s+", " ", "".join(parts)).strip()
        if txt and eff > 0:
            sized.append((eff, txt))
    return plain, sized


def _pdf_page_layout(page, plain: str) -> str:
    """Layout extraction keeps table columns as runs of spaces; fall back to plain text when it loses text."""
    try:
        lay = page.extract_text(extraction_mode="layout") or ""
    except Exception:
        return plain
    if len(re.sub(r"\s", "", lay)) < 0.9 * len(re.sub(r"\s", "", plain)):
        return plain
    out: list[str] = []
    blank = 0
    for ln in lay.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if ln.strip():
            blank = 0
            out.append(ln.rstrip())
        else:
            blank += 1
            if blank <= 2:  # keep at most two blank lines: a gap is a signal, a page of them is noise
                out.append("")
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


# --- main entry ------------------------------------------------------------------------


def load(path) -> SourceDoc | Diagnostic:
    """Read a requirements document. Never raises: problems become a Diagnostic."""
    spath = str(path) if path is not None else ""
    try:
        return _load(spath)
    except Exception as e:  # last line of defence
        return _diag("READ_ERROR", spath, details=f"{type(e).__name__}: {e}")


def _load(spath: str) -> SourceDoc | Diagnostic:
    if not spath:
        return _diag("NOT_FOUND", spath, details="путь не указан")
    p = Path(spath)
    try:
        if not p.exists():
            return _diag("NOT_FOUND", spath)
        if p.is_dir():
            return _diag("IS_DIRECTORY", spath)
        size = p.stat().st_size
    except PermissionError as e:
        return _diag("NO_PERMISSION", spath, details=str(e))
    except OSError as e:
        return _diag("READ_ERROR", spath, details=str(e))
    if size > MAX_BYTES:
        return _diag("TOO_LARGE", spath, size=size, chars=f"{size:,}".replace(",", " ") + " байт, то есть больше", limit=f"{MAX_CHARS:,}".replace(",", " "))
    try:
        with open(p, "rb") as fh:
            data = fh.read()
    except PermissionError as e:
        return _diag("NO_PERMISSION", spath, size=size, details=str(e))
    except OSError as e:
        return _diag("READ_ERROR", spath, size=size, details=str(e))
    size = len(data)
    if size == 0:
        return _diag("EMPTY_FILE", spath, size=0)

    ext = p.suffix.lower()
    sha = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []
    line_pages: list[int] | None = None
    pages: int | None = None
    pdf_headings: dict[int, int] | None = None
    encoding = ""

    if data.startswith(b"%PDF") or (ext == ".pdf" and b"%PDF" in data[:1024]):
        res = _load_pdf(data, spath, size)
        if isinstance(res, Diagnostic):
            return res
        text, line_pages, pages, pdf_headings = res
        fmt = "pdf"
        warnings.append("Документ прочитан из PDF: таблицы и списки могли потерять разметку, номера строк считаются по извлечённому тексту.")
    elif data.startswith(_OLE):
        if ext in (".doc", ".dot", ".docx", ""):
            return _diag("DOC_OLD_FORMAT", spath, size=size, detected="документ Word 97–2003 (.doc)")
        return _diag("WRONG_TYPE", spath, size=size, real="файл Microsoft Office старого формата (.doc/.xls/.ppt)", detected="файл Microsoft Office старого формата")
    elif data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06") or ext == ".docx":
        if not data.startswith(b"PK"):
            if ext == ".docx":
                return _diag("DOCX_BROKEN", spath, size=size, details="файл не является zip-архивом")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except Exception as e:
            if ext == ".docx":
                return _diag("DOCX_BROKEN", spath, size=size, details=f"{type(e).__name__}: {e}")
            return _diag("WRONG_TYPE", spath, size=size, real="повреждённый zip-архив", detected="zip-архив")
        if "word/document.xml" not in names:
            if ext == ".docx":
                return _diag("DOCX_BROKEN", spath, size=size, details="в архиве нет word/document.xml")
            real = "zip-архив"
            if "xl/workbook.xml" in names:
                real = "таблица Excel (.xlsx)"
            elif "ppt/presentation.xml" in names:
                real = "презентация PowerPoint (.pptx)"
            elif "mimetype" in names:
                real = "документ OpenDocument (.odt/.ods)"
            return _diag("WRONG_TYPE", spath, size=size, real=real, detected=real)
        try:
            text = docx_to_markdown(data)
        except Exception as e:
            return _diag("DOCX_BROKEN", spath, size=size, details=f"{type(e).__name__}: {e}")
        fmt = "docx"
        warnings.append("Документ прочитан из Word: рисунки и схемы-картинки не учитываются, нумерация списков заменена маркерами.")
    else:
        real = _sniff_binary(data)
        if real:
            return _diag("WRONG_TYPE", spath, size=size, real=real, detected=real)
        text, encoding = _decode(data)
        if text is None or _bad_share(text) > 0.02 or ("\x00" in text and _bad_share(text) > 0.001):
            return _diag("BINARY", spath, size=size, detected="двоичные данные", details=f"кодировка: {encoding}")
        if encoding == "cp1251":
            warnings.append("Кодировка файла определена как Windows-1251.")
        elif encoding.startswith("utf-16"):
            warnings.append("Кодировка файла — UTF-16.")
        if ext in (".html", ".htm", ".xhtml") or _looks_html(text):
            try:
                text = html_to_markdown(text)
            except Exception as e:
                return _diag("BINARY", spath, size=size, detected="повреждённый HTML", details=f"{type(e).__name__}: {e}")
            fmt = "html"
            warnings.append("Документ прочитан из HTML: оформление и картинки не учитываются.")
        else:
            fmt = "md" if ext in (".md", ".markdown", ".mdown") else "txt"

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n") if not line_pages else text.replace("\r", "").replace("\f", " ")
    visible = re.sub(r"[\s" + INVISIBLE_CHARS + "]", "", text)
    if not visible:
        if fmt == "html":  # a page of scripts and styles: there is markup, just no readable text
            return _diag("TOO_SHORT", spath, size=size, chars=0, lines=0, details="в HTML нет видимого текста")
        return _diag("ONLY_WHITESPACE", spath, size=size)
    nonblank_lines = sum(1 for ln in text.split("\n") if ln.strip())
    words = len(re.findall(r"[^\W_]+", text))
    has_sentence = any(len(re.findall(r"[^\W_]+", ln)) >= 3 for ln in text.split("\n"))
    if words < MIN_WORDS or not has_sentence:
        return _diag("TOO_SHORT", spath, size=size, chars=len(visible), lines=nonblank_lines,
                     details=f"слов: {words}, видимых знаков: {len(visible)}, непустых строк: {nonblank_lines}")
    if len(visible) < SHORT_CHARS or nonblank_lines < SHORT_LINES:
        warnings.append(f"Документ очень короткий ({words} слов, {nonblank_lines} непустых строк): проверено всё, что в нём есть, "
                        "но выводы о полноте набора требований ненадёжны.")
    if len(text) > MAX_CHARS:
        return _diag("TOO_LARGE", spath, size=size, chars=f"{len(text):,}".replace(",", " "), limit=f"{MAX_CHARS:,}".replace(",", " "))

    hidden = sum(text.count(c) for c in INVISIBLE_CHARS)
    if hidden:
        warnings.append(f"В тексте {hidden} невидимых символов (нулевой ширины или управления направлением письма): они учтены при проверке.")

    text, masked = mask_sensitive(text)
    if masked:
        warnings.append(
            f"Замаскировано {len(masked)} значений, похожих на номера карт, ИИН/БИН, адреса почты или телефоны: в модель и в отчёт они не попадают."
        )

    lines = text.split("\n")
    if line_pages is not None and len(line_pages) != len(lines):
        line_pages = (line_pages + [line_pages[-1] if line_pages else 1] * len(lines))[: len(lines)]
    doc = SourceDoc(
        path=spath,
        name=p.name,
        fmt=fmt,
        size_bytes=size,
        sha256=sha,
        text=text,
        lines=lines,
        pages=pages,
        line_pages=line_pages,
        encoding=encoding,
        warnings=warnings,
        masked=masked,
    )
    try:
        doc.blocks = parse_blocks(text, fmt, line_pages, headings=pdf_headings)
    except Exception as e:
        doc.blocks = []
        doc.warnings.append(f"Структуру документа разобрать не удалось ({type(e).__name__}); проверка ограничена.")
    return doc
