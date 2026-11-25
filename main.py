"""Ревизор ТЗ: python main.py --input docs/requirements.md --output reports/report.md"""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):
    sys.stdout.write("Нужен Python 3.10 или новее. Сейчас: %d.%d\n" % sys.version_info[:2])
    raise SystemExit(1)

from revizor.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
