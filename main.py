"""Ревизор ТЗ: python main.py --input docs/requirements.md --output reports/report.md"""
# No «from __future__» and no new syntax above the version check: this part must run on any Python 3.
import sys

if sys.version_info < (3, 10):
    try:
        sys.stdout.write("Нужен Python 3.10 или новее. Сейчас: %d.%d\n" % sys.version_info[:2])
    except Exception:  # a console without Cyrillic
        sys.stdout.write("Python 3.10 or newer is required. Current: %d.%d\n" % sys.version_info[:2])
    raise SystemExit(1)

from revizor.console import prepare_console  # noqa: E402

prepare_console()  # before the first print: Cyrillic, «₸» and emoji must not crash a cp866/cp1251/ASCII console

from revizor.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
