"""Fill the slide template with the numbers file and print it to PDF through headless Chrome.

    python tools/build_slides.py --numbers docs/slides/numbers.json [--out presentation.pdf] [--shots-dir DIR]
    python tools/build_slides.py --numbers docs/slides/numbers.example.json --allow-example --html-only --out slides.html

Placeholders look like {{key}}. Keys starting with ``img_`` hold an image path (relative to --shots-dir, then to the
repository root); the image is embedded as a base64 data: URI. Any other value is inserted as escaped text.
The build fails with a full list when a placeholder has no value, a value is unused, an image is missing, or the
numbers file is still the example (``"_example": true`` or a value containing «ПРИМЕР»).
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "docs" / "slides" / "slides.html"
PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
EXAMPLE_WORD = "ПРИМЕР"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shots import find_chrome  # noqa: E402


class BuildError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def placeholders(template: str) -> set[str]:
    return set(PLACEHOLDER.findall(template))


def load_numbers(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise BuildError([f"Не удалось прочитать файл с числами «{path}»."])
    except ValueError as e:
        raise BuildError([f"Файл с числами «{path}» — не JSON: {e}."])
    if not isinstance(data, dict):
        raise BuildError([f"Файл с числами «{path}» должен быть объектом JSON."])
    return data


def problems_for(template: str, data: dict, allow_example: bool) -> list[str]:
    keys = {k for k in data if not k.startswith("_")}
    need = placeholders(template)
    out = []
    missing = sorted(need - keys)
    unused = sorted(keys - need)
    if missing:
        out.append("Нет значений для: " + ", ".join(missing))
    if unused:
        out.append("Лишние значения, которых нет в шаблоне: " + ", ".join(unused))
    bad = sorted(k for k in keys & need if not isinstance(data[k], str) or not data[k].strip())
    if bad:
        out.append("Значение должно быть непустой строкой: " + ", ".join(bad))
    if not allow_example:
        flagged = sorted(k for k in keys if isinstance(data[k], str) and EXAMPLE_WORD in data[k])
        if data.get("_example") is True:
            out.append("Файл помечен как пример (\"_example\": true): подставьте реальные числа и уберите метку "
                       "или передайте --allow-example для пробной сборки.")
        if flagged:
            out.append("В значениях остался маркер примера «" + EXAMPLE_WORD + "»: " + ", ".join(flagged))
    return out


def embed_image(value: str, shots_dir: Path | None) -> str:
    candidates = []
    if shots_dir:
        candidates.append(shots_dir / value)
    candidates.append(ROOT / value)
    for p in candidates:
        if p.is_file():
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
    raise BuildError([f"Не найдена картинка «{value}» (искал: " + "; ".join(str(c) for c in candidates) + ")."])


def fill(template: str, data: dict, shots_dir: Path | None = None, allow_example: bool = False) -> str:
    probs = problems_for(template, data, allow_example)
    if probs:
        raise BuildError(probs)
    imgs, errs = {}, []
    for k in sorted(placeholders(template)):
        if k.startswith("img_"):
            try:
                imgs[k] = embed_image(data[k], shots_dir)
            except BuildError as e:
                errs += e.problems
    if errs:
        raise BuildError(errs)

    def sub(m: re.Match) -> str:
        k = m.group(1)
        return imgs[k] if k in imgs else html.escape(data[k], quote=True)

    return PLACEHOLDER.sub(sub, template)


def print_pdf(chrome: str, html_path: Path, pdf_path: Path, timeout: int = 180) -> None:
    prof = Path(tempfile.mkdtemp(prefix="slides-profile-"))
    try:
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", "--no-default-browser-check",
               f"--user-data-dir={prof}", f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer", html_path.as_uri()]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise BuildError([f"Chrome не ответил за {timeout} с."])
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    if not pdf_path.exists():
        raise BuildError(["Chrome не создал PDF."])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Собирает презентацию из шаблона и файла с числами.")
    ap.add_argument("--numbers", required=True, help="JSON с числами (образец: docs/slides/numbers.example.json)")
    ap.add_argument("--out", help="куда писать (по умолчанию presentation.pdf или presentation.html)")
    ap.add_argument("--shots-dir", help="папка с картинками из tools/shots.py")
    ap.add_argument("--html-only", action="store_true", help="записать только заполненный HTML, без Chrome")
    ap.add_argument("--allow-example", action="store_true", help="разрешить пробную сборку на примерных числах")
    ap.add_argument("--chrome", help="путь к Chrome (или CHROME_BIN)")
    a = ap.parse_args(argv)
    try:
        template = TEMPLATE.read_text(encoding="utf-8")
        data = load_numbers(Path(a.numbers))
        shots = Path(a.shots_dir) if a.shots_dir else None
        page = fill(template, data, shots, a.allow_example)
        if a.html_only:
            out = Path(a.out or "presentation.html")
            out.write_text(page, encoding="utf-8")
            print(f"Готово: {out}")
            return 0
        chrome = find_chrome(a.chrome)
        if not chrome:
            print("Chrome не найден: установите Google Chrome или укажите путь через --chrome / CHROME_BIN.", file=sys.stderr)
            return 2
        out = Path(a.out or "presentation.pdf")
        tmp = Path(tempfile.mkdtemp(prefix="slides-html-"))
        try:
            hp = tmp / "slides.html"
            hp.write_text(page, encoding="utf-8")
            out.parent.mkdir(parents=True, exist_ok=True)
            print_pdf(chrome, hp, out.resolve())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print(f"Готово: {out}")
        return 0
    except BuildError as e:
        print("Сборка остановлена:", file=sys.stderr)
        for p in e.problems:
            print(" - " + p, file=sys.stderr)
        return 2
    except OSError as e:
        print(f"Ошибка файла: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
