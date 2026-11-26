"""A console that never crashes on characters it cannot show.

The agent prints Russian text, «₸», arrows, check marks and emoji. On Windows (cmd or PowerShell with code page
866/1251, ``PYTHONUTF8`` unset), with ``LC_ALL=C`` or with output piped to a file, the standard streams may use an
encoding that has no such characters, and the first ``print`` raises ``UnicodeEncodeError``. ``prepare_console()``
is called once at program start (``main.py`` and the helper scripts):

* every stream gets ``errors="replace"``: a character the console cannot show becomes «?», never an exception;
* output that goes to a file or a pipe (not a terminal) is switched to UTF-8, unless ``PYTHONIOENCODING`` says
  otherwise: a log written by ``python main.py ... > log.txt`` keeps all characters on every system;
* a real terminal keeps its encoding (Windows terminals already get UTF-16 from Python itself).

Standard library only; never raises. It does not touch the analysis or the files the agent writes.
"""
from __future__ import annotations

import codecs
import os
import sys
from typing import Mapping


def _is_utf8(encoding: str | None) -> bool:
    try:
        return codecs.lookup(encoding or "ascii").name == "utf-8"
    except LookupError:
        return False


def prepare_stream(stream, *, forced: bool = False) -> None:
    """Make one text stream safe (see the module docstring). ``forced`` — the user set PYTHONIOENCODING."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # None (pythonw), or a replaced stream without reconfigure()
        return
    try:
        tty = bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        tty = False
    try:
        if not forced and not tty and not _is_utf8(getattr(stream, "encoding", None)):
            reconfigure(encoding="utf-8", errors="replace")
        else:
            reconfigure(errors="replace")
    except Exception:  # a closed or exotic stream: leave it as it is
        pass


def prepare_console(environ: Mapping[str, str] | None = None) -> None:
    """Apply ``prepare_stream`` to ``sys.stdout`` and ``sys.stderr``. Call once, before the first print."""
    env = os.environ if environ is None else environ
    forced = bool((env.get("PYTHONIOENCODING") or "").strip())
    for name in ("stdout", "stderr"):
        prepare_stream(getattr(sys, name, None), forced=forced)
