"""PDF and screenshots of the report through headless Chrome.

    python tools/shots.py REPORT.md --out-dir DIR [--width 1280] [--chrome PATH]

Writes DIR/report.pdf (whole report, A4), DIR/report_top.png (first screen, WIDTH x 1600) and
DIR/report_card.png (scrolled to the first finding card). Chrome comes from --chrome, the CHROME_BIN
environment variable, or the default macOS path. Exit code 2 if Chrome or the input is missing.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from revizor import exports, report_md  # noqa: E402
from revizor.report_html import render_html  # noqa: E402

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FLAGS = ["--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
         "--no-default-browser-check", "--disable-extensions"]


def find_chrome(arg: str | None) -> str | None:
    for cand in (arg, os.environ.get("CHROME_BIN"), DEFAULT_CHROME):
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def run_chrome(chrome: str, extra: list[str], url: str, tmp_profile: Path, timeout: int = 120) -> None:
    cmd = [chrome, *FLAGS, f"--user-data-dir={tmp_profile}", *extra, url]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Chrome не ответил за {timeout} с.")


def card_anchor(html_text: str) -> str | None:
    """Anchor of the first heading with a finding id (D-0…); if none, of the first finding card."""
    for m in re.finditer(r"<h[1-6] id=\"([^\"]+)\"[^>]*>(.*?)</h[1-6]>", html_text, re.S):
        if re.search(r"\bD-0\d", m.group(2)):
            return m.group(1)
    m = re.search(r'<div class="card[^"]*"><a id="(d-[^"]+)"', html_text)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDF и скриншоты отчёта через Chrome без интерфейса.")
    ap.add_argument("report", help="отчёт .md")
    ap.add_argument("--out-dir", required=True, help="папка для report.pdf и PNG")
    ap.add_argument("--width", type=int, default=1280, help="ширина скриншотов, px (по умолчанию 1280)")
    ap.add_argument("--chrome", help="путь к Chrome (или переменная CHROME_BIN)")
    a = ap.parse_args(argv)

    chrome = find_chrome(a.chrome)
    if not chrome:
        print("Chrome не найден: установите Google Chrome или укажите путь через --chrome / CHROME_BIN.", file=sys.stderr)
        return 2
    src = Path(a.report)
    try:
        text = src.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        print(f"Не удалось прочитать отчёт «{src}»: проверьте путь и что это текстовый файл.", file=sys.stderr)
        return 2
    out = Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # the details file next to the report makes one page with it, as in the agent's own HTML export
    details = src.with_name(report_md.details_name(src.name))
    if details.is_file():
        text = exports.one_page_md(text, details.read_text(encoding="utf-8-sig"), src.name)
    page = render_html(text, theme="light")
    prof = Path(tempfile.mkdtemp(prefix="shots-profile-"))
    try:
        html_path = out / "report.html"
        html_path.write_text(page, encoding="utf-8")
        url = html_path.as_uri()
        w = max(600, a.width)

        run_chrome(chrome, [f"--print-to-pdf={out / 'report.pdf'}", "--no-pdf-header-footer"], url, prof)
        print("report.pdf" if (out / "report.pdf").exists() else "report.pdf: не получился")

        run_chrome(chrome, [f"--screenshot={out / 'report_top.png'}", f"--window-size={w},1600"], url, prof)
        print("report_top.png" if (out / "report_top.png").exists() else "report_top.png: не получился")

        anchor = card_anchor(page)
        if anchor:
            # Chrome in headless mode paints a blank frame after a #fragment jump, so the page is cut
            # just before the element instead: same styles, same look as if scrolled there.
            cut = page.find(f'<a id="{anchor}"></a>')
            start = max(page.rfind('<div class="card', 0, cut), page.rfind("<h", 0, cut))
            art = page.find('<article class="sheet">')
            if cut < 0 or art < 0 or start < art:
                print("report_card.png пропущен: не удалось найти место замечания в странице")
            else:
                head = page[:art] + '<article class="sheet" style="padding-top:8px">\n'
                card_html = out / "_card.html"
                card_html.write_text(head + page[start:], encoding="utf-8")
                run_chrome(chrome, [f"--screenshot={out / 'report_card.png'}", f"--window-size={w},1000"],
                           card_html.as_uri(), prof)
                card_html.unlink(missing_ok=True)
                print("report_card.png" if (out / "report_card.png").exists() else "report_card.png: не получился")
        else:
            print("report_card.png пропущен: в отчёте нет замечания с номером вида D-0…")
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
