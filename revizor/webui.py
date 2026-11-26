"""``python main.py --ui``: a small local window in the browser on top of the same command-line check.

Standard library only. The server binds to 127.0.0.1, serves one page (``webui_page.html``) and a JSON API
(see ``fable/ОКНО_договор.md``). A check started from the page builds an ordinary argv list, parses it with
``cli.build_parser()`` and calls ``cli.execute`` in a worker thread: there is no second pipeline. Live progress
is read from the same console lines the terminal prints. The window takes no part in the analysis.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, cli, config, verdict as verdict_mod
from .llm import choose_provider, is_placeholder, normalize_provider, review_batch_size_for
from .llm.pricing import PRICES
from .model import Result
from .pipeline import STEPS
from .textfmt import num

# The nine stages exactly as pipeline.run numbers them in its «[k/9] …» lines (a test keeps the two in step).
STAGES = ("Чтение документа", "Нарезка на требования", "Проверка правилами", "Проверка каждого требования",
          "Проверка набора требований", "Сверка цитат и повторов", "Перепроверка замечаний", "Вопросы автору",
          "Индексы и вердикт")

DOC_FORMATS = (".md", ".txt", ".pdf", ".docx", ".html")
ANSWER_FORMATS = (".md", ".txt")
MAX_DOC_MB = 60
MAX_ANSWERS_MB = 2
MAX_JSON_BYTES = 64 * 1024
MAX_UPLOADS = 20
MAX_JOBS = 20
MAX_NAME = 120
DEFAULT_OUT_DIR = "reports/ui"
DEPTHS = (("full", "полная"), ("fast", "быстрая"))
OFFLINE_ID = "offline"
OFFLINE_LABEL = "без модели — только правила, бесплатно"
HIDDEN_MODELS = ("claude-fable-5-2",)
MODEL_PREFIX = {"anthropic": "claude-", "openai": "gpt-"}
ESTIMATE_NOTE = ("Оценка для прогона без кэша. Если этот документ уже проверяли этой моделью, ответы возьмутся из кэша: "
                 "быстрее и бесплатно.")
VERDICT_LEVEL = {"BLOCKED": "stop", "CONDITIONAL": "warn", "READY": "ok", "NOT_CHECKED": "unknown"}

# kind of an output file by its name ending; the order matters (longest endings first)
FILE_KINDS = ((".questions.md", "questions", "Анкета для автора"),
              (".details.md", "details", "Подробности проверки"),
              (".tests.md", "tests", "Черновики тестов"),
              (".fixed.md", "fixed", "Предложенные исправления текста"),
              (".summary.md", "summary", "Краткая сводка"),
              (".diagnostic.md", "other", "Подробности прогона"),
              (".jira.csv", "jira", "Замечания для Jira (CSV)"),
              (".findings.csv", "csv", "Замечания (CSV)"),
              (".html", "report_html", "Отчёт"),
              (".json", "json", "Отчёт в JSON"))
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".md": "text/plain; charset=utf-8",
                 ".csv": "text/plain; charset=utf-8", ".json": "text/plain; charset=utf-8",
                 ".txt": "text/plain; charset=utf-8"}
PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
FILE_CSP = "sandbox allow-scripts allow-popups"

_STAGE_LINE = re.compile(r"^\[(\d+)/" + str(STEPS) + r"\] (.*)$")
_STEP_LINE = re.compile(r"^\s+([✓✗]) (.+?) (\d+)/(\d+) · \d+ с(?: · (\$[0-9.]+))?(?: · (.*))?$")
_WAIT_LINE = re.compile(r"^\s+… (.+)$")
_SEND_LINE = re.compile(r"^\s+Отправляю модели \d+ запрос")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# -- file names -------------------------------------------------------------------------------------------------
WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                              *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})


def safe_name(raw: str, allowed: tuple[str, ...]) -> str:
    """A safe base name for a client file name, or raises ApiError(400) when the extension is not allowed.

    The last component after splitting on «/» and «\\»; control characters and leading dots dropped; only letters
    (any script), digits, space, «.», «_», «-», «(», «)» kept; at most MAX_NAME characters; «document<ext>» when
    nothing is left; a name Windows reserves for a device («CON», «nul.txt», «COM1») gets a «_» in front, and no
    dot or space is left at the end of the name before the extension. An ordinary name («requirements.md») comes back
    unchanged."""
    s = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C")
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] == "L" or unicodedata.category(ch) == "Nd"
                or ch in " ._-()")
    base, dot, ext = s.rpartition(".")
    if not dot or not ext or "." + ext.lower() not in allowed:
        raise ApiError(400, "Этот формат не подходит. Можно: " + ", ".join(allowed))
    base = base.lstrip(". ").rstrip()
    ext = "." + ext
    if not base:
        base = "document"
    base = base[:MAX_NAME - len(ext)].rstrip(". ")
    if base.split(".", 1)[0].upper() in WINDOWS_RESERVED:  # «CON.md» on Windows is the console, not a file
        base = "_" + base
    return base + ext


def file_kind(name: str, report_stem: str) -> tuple[str, str]:
    low = name.lower()
    if name == report_stem + ".md":
        return "report_md", "Отчёт в Markdown"
    for suffix, kind, label in FILE_KINDS:
        if low.endswith(suffix):
            return kind, label
    return "other", "Дополнительный файл"


def _content_disposition(name: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


# -- models and provider ----------------------------------------------------------------------------------------
def active_provider(provider_arg: str, env: Mapping[str, str]) -> tuple[str, str]:
    """(provider the window will use, a Russian note when it falls back to rules only). Never shows a value."""
    p = normalize_provider(provider_arg or "auto")
    chosen = choose_provider(env) if p in ("", "auto") else p
    rules_only = "Проверка пойдёт только по автоматическим правилам, без модели: это бесплатно."
    if chosen == "offline":
        why = "выбран режим без модели" if p == "offline" else "ключ модели не настроен"
        return "offline", f"{why[0].upper()}{why[1:]}. {rules_only}"
    keys = dict(cli.AUTO_ORDER).get(chosen)
    if keys is None:
        return "offline", f"Провайдер «{chosen[:30]}» неизвестен. {rules_only}"
    if not any((env.get(k) or "").strip() and not is_placeholder(env.get(k)) for k in keys):
        return "offline", f"Для провайдера {chosen} не найден ключ ({' или '.join(keys)}). {rules_only}"
    return chosen, ""


def model_choices(provider: str, env: Mapping[str, str], cli_model: str | None) -> tuple[list[dict], str]:
    """(models for the page, default id). The provider's default, its other priced models, --model, and offline."""
    if provider == "offline":
        return [{"id": OFFLINE_ID, "label": OFFLINE_LABEL}], OFFLINE_ID
    default = config.resolve_model(provider, env)
    ids: list[str] = []
    for m in [default, cli_model or ""] + sorted(PRICES):
        if not m or m in ids or (m in HIDDEN_MODELS and m != cli_model):
            continue
        if m in PRICES and m not in (default, cli_model) and not m.startswith(MODEL_PREFIX.get(provider, "\0")):
            continue
        ids.append(m)
    models = [{"id": m, "label": m + (" — по умолчанию" if m == default else "")} for m in ids]
    models.append({"id": OFFLINE_ID, "label": OFFLINE_LABEL})
    return models, (cli_model or default or OFFLINE_ID)


# -- one check --------------------------------------------------------------------------------------------------
class Job:
    """State of one check, fed by the console lines of ``cli.execute``."""

    def __init__(self, job_id: str, *, doc_name: str, answers_name: str, provider: str, model: str, depth: str,
                 max_cost: str, out_dir: str, report_stem: str) -> None:
        self.id = job_id
        self.lock = threading.Lock()
        self.state = "running"
        self.doc_name, self.answers_name = doc_name, answers_name
        self.provider, self.model, self.depth, self.max_cost = provider, model, depth, max_cost
        self.out_dir, self.report_stem = out_dir, report_stem
        self.t0 = time.monotonic()
        self.t_end: float | None = None
        self.stages = [{"n": i + 1, "label": label, "state": "pending", "detail": ""} for i, label in enumerate(STAGES)]
        self.stages[0]["state"] = "running"
        self.step_text = ""
        self.calls = {"model": 0, "cache": 0}
        self.spent_text = "$0" if provider == "offline" else "$0.00"
        self.log: list[str] = []
        self.result: dict | None = None
        self.error = ""
        self.files: dict[str, Path] = {}  # base name → path; the only files /files/<job>/ serves
        self.doc_upload = self.answers_upload = ""  # upload ids in use (kept while the check runs)

    # progress ------------------------------------------------------------------------------------------------
    def feed(self, line: str) -> None:
        line = str(line)
        with self.lock:
            self.log.append(line)
            self._parse(line)

    def _parse(self, line: str) -> None:
        m = _STAGE_LINE.match(line)
        if m:
            k = int(m.group(1))
            if not 1 <= k <= len(self.stages):
                return
            _title, _sep, detail = m.group(2).partition("…")
            detail = detail.strip()
            st = self.stages[k - 1]
            st["state"] = "skipped" if detail.startswith("пропущено") else "done"
            st["detail"] = detail
            # The pipeline prints «[k/9]» right after stage k has finished, so the next stage is the one at work.
            if k < len(self.stages) and self.stages[k]["state"] == "pending":
                self.stages[k]["state"] = "running"
            return
        if _SEND_LINE.match(line):
            # stages 4 and 5 share one pool of model requests: both are at work until «[4/9]» and «[5/9]»
            for st in self.stages[3:5]:
                if st["state"] == "pending":
                    st["state"] = "running"
            return
        m = _STEP_LINE.match(line)
        if m:
            mark, group, k, n, money, note = m.groups()
            self.step_text = f"{group} {k}/{n}"
            if note and "из кэша агента" in note:
                self.calls["cache"] += 1
            else:
                self.calls["model"] += 1
            if money:
                self.spent_text = money
            return
        m = _WAIT_LINE.match(line)
        if m:
            self.step_text = m.group(1).strip()

    def finish(self, state: str, *, result: dict | None = None, error: str = "",
               files: dict[str, Path] | None = None) -> None:
        with self.lock:
            for st in self.stages:
                if st["state"] in ("pending", "running"):
                    st["state"] = "skipped"
            self.files = dict(files or {})
            self.result = result
            self.error = error
            self.t_end = time.monotonic()
            self.state = state

    def status(self, since: int = 0) -> dict:
        with self.lock:
            end = self.t_end if self.t_end is not None else time.monotonic()
            return {"ok": True, "job_id": self.id, "state": self.state, "doc_name": self.doc_name,
                    "answers_name": self.answers_name, "provider": self.provider, "model": self.model,
                    "depth": self.depth, "max_cost": self.max_cost, "elapsed_s": round(end - self.t0, 1),
                    "stages": [dict(s) for s in self.stages], "step_text": self.step_text,
                    "calls": dict(self.calls), "spent_text": self.spent_text, "log": self.log[max(0, since):],
                    "log_next": len(self.log), "result": self.result, "error": self.error}


# The last line of the questionnaire: the terminal prints a ready command there, but a document chosen in the window
# lies in a temporary folder, so the window explains the same step in its own terms. It must start with «Когда ответите»:
# the answers parser cuts the questionnaire at that line.
RERUN_NOTE = ("Когда ответите: в окне Ревизора выберите тот же документ, приложите эту анкету в поле «Ответы автора» "
              "и нажмите «Проверить». То же из терминала: `python main.py --input <путь к документу> "
              "--output {output} --answers {answers}`")


def build_argv(*, doc_path: str, out_path: str, depth: str, model: str, provider_arg: str = "auto",
               answers_path: str = "", max_cost: str = "", no_cache: bool = False) -> list[str]:
    """The command line of one check started from the page (the same flags a person would type)."""
    argv = ["--input", doc_path, "--output", out_path, "--depth", depth]
    if model == OFFLINE_ID:
        argv += ["--provider", "offline"]
    else:
        argv += ["--model", model]
        p = normalize_provider(provider_arg or "auto")
        if p not in ("", "auto"):
            argv += ["--provider", p]
    if answers_path:
        argv += ["--answers", answers_path]
    if max_cost:
        argv += ["--max-cost", max_cost]
    if no_cache:
        argv.append("--no-cache")
    return argv


def result_summary(result: Result, written: list[Path], job_id: str, report_stem: str,
                   out_dir: str) -> tuple[dict, dict[str, Path]]:
    """The «result» object of the contract, built from the Result itself (not from printed text)."""
    r = result.run
    if result.diagnostic is None:
        c = [f for f in result.findings if f.status == "open" and not f.advisory]
        sev = {s: sum(1 for f in c if f.severity == s) for s in ("CRITICAL", "MAJOR", "MINOR")}
        findings = {"total": len(c), "critical": sev["CRITICAL"], "major": sev["MAJOR"], "minor": sev["MINOR"]}
        diag = None
    else:
        findings = {"total": 0, "critical": 0, "major": 0, "minor": 0}
        d = result.diagnostic
        diag = {"title": d.title, "what_happened": d.what_happened, "what_to_do": list(d.what_to_do)}
    files: dict[str, Path] = {}
    listed = []
    for p in written:
        name = p.name
        if name in files:
            continue
        files[name] = p
        kind, label = file_kind(name, report_stem)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        listed.append({"name": name, "kind": kind, "label": label, "size": size,
                       "url": f"/files/{job_id}/{quote(name, safe='')}"})
    return {
        "verdict": result.verdict,
        "verdict_text": result.verdict_text,
        "verdict_level": VERDICT_LEVEL.get(result.verdict, "unknown"),
        "score": r.score,
        "requirements": len(result.requirements),
        "findings": findings,
        "questions": len(result.questions),
        "duration_text": f"{r.duration_s:.1f} с",
        "cost_text": cli.cost_text(result),
        "llm_calls": r.llm_calls,
        "cache_hits": r.llm_cache_hits,
        "ledger": cli.ledger_line(result),
        "diagnostic": diag,
        "warnings": list(r.warnings),
        "out_dir": out_dir,
        "files": listed,
    }, files


# -- estimate ---------------------------------------------------------------------------------------------------
_TIME = re.compile(r"^\s*Время: (≈ \d+ – \d+ мин)")
_COST = re.compile(r"^\s*Стоимость: (≈ \S+ – \S+)")
_COST_UNKNOWN = re.compile(r"^\s*Стоимость: неизвестна")
_CAP = re.compile(r"--max-cost (\S+)\s*$")


def estimate_for(path: str, *, provider: str, model: str, depth: str, env: Mapping[str, str]) -> dict:
    """The same estimate as ``--estimate``: estimate.build + estimate.report. The short texts for the page are taken
    from the very lines ``--estimate`` prints, so the two can never disagree."""
    from . import estimate

    if model == OFFLINE_ID:
        est = estimate.build(path, depth=depth, contexts=[], batch_size=None)
        if est.error:
            lines = estimate.report(est, provider="anthropic", model="", depth=depth)
        else:
            lines = ["Оценка прогона (модель не вызывалась)",
                     f"  Документ: {est.doc_name} — {num(est.lines)} строк, ≈ {est.requirements} требований",
                     "  Без модели: только автоматические правила. Модель не вызывается, это бесплатно и занимает "
                     "несколько секунд."]
        return {"ok": True, "readable": not est.error, "calls": 0,
                "time_text": "" if est.error else "несколько секунд", "cost_text": "" if est.error else "$0",
                "suggested_cap": "", "doc_text": _doc_text(est), "lines": lines,
                "note": "" if est.error else "Без модели проверка бесплатна."}
    est = estimate.build(path, depth=depth, contexts=[], batch_size=review_batch_size_for(provider, env))
    lines = estimate.report(est, provider=provider, model=model, depth=depth, env=env)
    out = {"ok": True, "readable": not est.error, "calls": 0 if est.error else len(est.calls), "time_text": "",
           "cost_text": "", "suggested_cap": "", "doc_text": _doc_text(est), "lines": lines,
           "note": "" if est.error else ESTIMATE_NOTE}
    for line in lines:
        if m := _TIME.match(line):
            out["time_text"] = m.group(1)
        elif m := _COST.match(line):
            out["cost_text"] = m.group(1)
        elif _COST_UNKNOWN.match(line):
            out["cost_text"] = "неизвестна"
        elif m := _CAP.search(line):
            out["suggested_cap"] = m.group(1)
    return out


def _doc_text(est) -> str:
    if est.error:
        return f"Документ не прочитан: {est.error}"
    return f"{est.doc_name} — {num(est.lines)} строк, ≈ {est.requirements} требований"


def _mb(n: int) -> int | float:
    mb = round(n / 1024 / 1024, 2)
    return int(mb) if mb == int(mb) else mb


# -- the server -------------------------------------------------------------------------------------------------
class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # POSIX: TIME_WAIT only; a second listener on the same port still fails. On Windows SO_REUSEADDR would let a
    # second window bind the same port silently, so there the port is taken exclusively instead.
    allow_reuse_address = os.name != "nt"
    allow_reuse_port = False
    ui: "UIServer"

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # HTTPServer.server_bind also asks socket.getfqdn(), a reverse DNS lookup that can hang for seconds on some
        # Windows networks; the window only ever listens on 127.0.0.1
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), port

    def handle_error(self, request, client_address) -> None:  # no traceback in the terminal
        self.ui._say("Ошибка при обработке запроса окна.")


class UIServer:
    """The window's server. ``start()`` binds 127.0.0.1 and returns the real port; ``serve_forever()``; ``close()``
    stops it and removes the temporary folder with uploads."""

    def __init__(self, env: Mapping[str, str], *, port: int = 8765, out_dir: str | Path = DEFAULT_OUT_DIR,
                 defaults: Mapping[str, Any] | None = None, provider: str = "auto", no_cache: bool = False,
                 backend=None, page_path: str | Path | None = None, preload: str | None = None,
                 log: Callable[[str], None] | None = None, max_doc_bytes: int = MAX_DOC_MB * 1024 * 1024,
                 max_answers_bytes: int = MAX_ANSWERS_MB * 1024 * 1024) -> None:
        self.env = dict(env)
        self.port = port
        self.out_dir = str(out_dir)
        defaults = dict(defaults or {})
        self.provider_arg = provider or "auto"
        self.no_cache = no_cache
        self.backend = backend
        self.page_path = Path(page_path) if page_path else Path(__file__).with_name("webui_page.html")
        self.max_doc_bytes = max_doc_bytes
        self.max_answers_bytes = max_answers_bytes
        self._log = log or (lambda s: print(s, flush=True))
        self._log_lock = threading.Lock()
        self.provider, self.provider_note = active_provider(self.provider_arg, self.env)
        self.models, self.default_model = model_choices(self.provider, self.env, defaults.get("model"))
        self.default_depth = defaults.get("depth") if defaults.get("depth") in ("full", "fast") else "full"
        self.default_cap = str(defaults.get("max_cost") or "")
        self.lock = threading.Lock()
        self.uploads: dict[str, dict] = {}
        self.jobs: dict[str, Job] = {}
        self.latest: Job | None = None
        self.running: Job | None = None
        self.preload_id: str | None = None
        self.temp_root = Path(tempfile.mkdtemp(prefix="revizor-ui-")).resolve()
        self.httpd: _HTTPServer | None = None
        self._serving = False
        if preload:
            p = Path(preload)
            uid = secrets.token_hex(8)
            self.uploads[uid] = {"id": uid, "kind": "doc", "name": p.name, "path": p,
                                 "size": p.stat().st_size if p.is_file() else 0, "t": time.monotonic(), "pinned": True}
            self.preload_id = uid

    # lifecycle -----------------------------------------------------------------------------------------------
    def start(self) -> int:
        httpd = _HTTPServer(("127.0.0.1", self.port), _Handler)
        httpd.ui = self
        self.httpd = httpd
        self.port = httpd.server_address[1]
        return self.port

    def serve_forever(self) -> None:
        assert self.httpd is not None
        self._serving = True
        try:
            self.httpd.serve_forever(poll_interval=0.25)
        finally:
            self._serving = False

    def close(self) -> None:
        if self.httpd is not None:
            if self._serving:  # shutdown() waits for serve_forever(); never call it when nothing serves
                self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        _remove_tree(self.temp_root)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _say(self, line: str) -> None:
        with self._log_lock:
            self._log(line)

    # API -----------------------------------------------------------------------------------------------------
    def config(self) -> dict:
        with self.lock:
            pre = self.uploads.get(self.preload_id or "")
            latest = self.latest
        return {"ok": True, "version": __version__, "provider": self.provider, "provider_note": self.provider_note,
                "models": self.models, "default_model": self.default_model,
                "depths": [{"id": i, "label": label} for i, label in DEPTHS], "default_depth": self.default_depth,
                "formats": list(DOC_FORMATS), "max_doc_mb": _mb(self.max_doc_bytes),
                "max_answers_mb": _mb(self.max_answers_bytes),
                "default_cap": self.default_cap, "out_dir": self.out_dir, "stages": list(STAGES),
                "preloaded": ({"upload_id": pre["id"], "name": pre["name"], "size": pre["size"]} if pre else None),
                "job": latest.status(0) if latest else None}

    def upload(self, kind: str, raw_name: str, length: int | None, rfile) -> dict:
        if kind not in ("doc", "answers"):
            raise ApiError(400, "Неизвестный вид файла: нужен kind=doc или kind=answers.")
        allowed = DOC_FORMATS if kind == "doc" else ANSWER_FORMATS
        limit = self.max_doc_bytes if kind == "doc" else self.max_answers_bytes
        name = safe_name(raw_name, allowed)
        if length is None:
            raise ApiError(400, "Не указан размер файла (Content-Length).")
        if length > limit:
            raise ApiError(413, f"Файл слишком большой: можно до {limit / 1024 / 1024:g} МБ.")
        if length <= 0:
            raise ApiError(400, "Файл пустой.")
        uid = secrets.token_hex(8)
        folder = self.temp_root / uid
        dest = folder / name
        if dest.resolve().parent != folder.resolve() or not dest.resolve().is_relative_to(self.temp_root):
            raise ApiError(400, "Недопустимое имя файла.")
        folder.mkdir()
        got = 0
        try:
            with open(dest, "wb") as f:
                while got <= limit:
                    chunk = rfile.read(min(64 * 1024, length - got, limit + 1 - got))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if got >= length:
                        break
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise ApiError(400, "Файл не удалось сохранить.")
        if got > limit:
            shutil.rmtree(folder, ignore_errors=True)
            raise ApiError(413, f"Файл слишком большой: можно до {limit / 1024 / 1024:g} МБ.")
        if got != length:
            shutil.rmtree(folder, ignore_errors=True)
            raise ApiError(400, "Файл пришёл не целиком.")
        entry = {"id": uid, "kind": kind, "name": name, "path": dest, "size": got, "t": time.monotonic(),
                 "pinned": False}
        with self.lock:
            self.uploads[uid] = entry
            self._drop_old_uploads()
        return {"ok": True, "upload_id": uid, "name": name, "size": got}

    def _drop_old_uploads(self) -> None:
        busy = {self.running.doc_upload, self.running.answers_upload} if self.running is not None else set()
        own = sorted((u for u in self.uploads.values() if not u["pinned"]), key=lambda u: u["t"])
        excess = len(own) - MAX_UPLOADS
        for u in own:
            if excess <= 0:
                break
            if u["id"] in busy:
                continue
            self.uploads.pop(u["id"], None)
            folder = (self.temp_root / u["id"]).resolve()
            if folder.parent == self.temp_root:
                shutil.rmtree(folder, ignore_errors=True)
            excess -= 1

    def _upload(self, uid: Any, kind: str) -> dict:
        if not isinstance(uid, str):
            raise ApiError(400, "Не выбран файл." if kind == "doc" else "Файл ответов указан неверно.")
        with self.lock:
            u = self.uploads.get(uid)
        if u is None or u["kind"] != kind:
            raise ApiError(404, "Файл не найден: загрузите его ещё раз.")
        return u

    def _model(self, value: Any) -> str:
        ids = {m["id"] for m in self.models}
        if not isinstance(value, str) or value not in ids:
            raise ApiError(400, "Такой модели нет в списке.")
        return value

    @staticmethod
    def _depth(value: Any) -> str:
        if value not in ("full", "fast"):
            raise ApiError(400, "Глубина может быть только «полная» или «быстрая».")
        return value

    @staticmethod
    def _cap(value: Any) -> str:
        if value in (None, ""):
            return ""
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ApiError(400, "Потолок стоимости должен быть числом в долларах, например 0.50.")
        cap, err = config.max_cost_from(str(value), {})
        if err or cap is None or not cap > 0 or cap != cap or cap == float("inf"):
            raise ApiError(400, "Потолок стоимости должен быть положительным числом в долларах, например 0.50.")
        return f"{cap:g}"

    def estimate(self, body: dict) -> dict:
        u = self._upload(body.get("doc"), "doc")
        model = self._model(body.get("model"))
        depth = self._depth(body.get("depth", "full"))
        try:
            return estimate_for(str(u["path"]), provider=self.provider, model=model, depth=depth, env=self.env)
        except Exception as e:
            raise ApiError(400, f"Оценка не удалась: внутренняя ошибка ({type(e).__name__}).")

    def run(self, body: dict) -> dict:
        doc = self._upload(body.get("doc"), "doc")
        ans = self._upload(body["answers"], "answers") if body.get("answers") else None
        model = self._model(body.get("model"))
        depth = self._depth(body.get("depth", "full"))
        cap = self._cap(body.get("max_cost"))
        stem = Path(doc["name"]).stem or "document"
        out_path = Path(self.out_dir) / (stem + ("_answered" if ans else "") + ".md")
        argv = build_argv(doc_path=str(doc["path"]), out_path=str(out_path), depth=depth, model=model,
                          provider_arg=self.provider_arg, answers_path=str(ans["path"]) if ans else "",
                          max_cost=cap, no_cache=self.no_cache)
        provider = "offline" if model == OFFLINE_ID else self.provider
        with self.lock:
            if self.running is not None:
                raise ApiError(409, "Проверка уже идёт: дождитесь её окончания.")
            job = Job(secrets.token_hex(8), doc_name=doc["name"], answers_name=ans["name"] if ans else "",
                      provider=provider, model=model, depth=depth, max_cost=cap, out_dir=self.out_dir,
                      report_stem=out_path.stem)
            job.doc_upload, job.answers_upload = doc["id"], ans["id"] if ans else ""
            self.running = job
            self.latest = job
            self.jobs[job.id] = job
            while len(self.jobs) > MAX_JOBS:
                self.jobs.pop(next(iter(self.jobs)))
        backend = None if model == OFFLINE_ID else self.backend
        self._say(f"Проверка начата: {doc['name']} · {model} · глубина {depth}")
        threading.Thread(target=self._work, args=(job, argv, backend), name="revizor-ui-check", daemon=True).start()
        return {"ok": True, "job_id": job.id}

    def _work(self, job: Job, argv: list[str], backend) -> None:
        try:
            args = cli.build_parser().parse_args(argv)
            args.rerun_note = RERUN_NOTE
            # uploads lie under temp_root; a document given on the command line keeps its real path, as in the terminal
            dirs = [d for d in {Path(x).parent for x in (args.input, args.answers) if x}
                    if self.temp_root in d.parents or d == self.temp_root]
            # both spellings: «C:\\…\\» as Windows prints it and «C:/…/» as a command in the questionnaire writes it
            args.hide_prefixes = sorted({form for d in dirs for form in (str(d) + os.sep, d.as_posix() + "/")},
                                        key=len, reverse=True)
            code, result, written = cli.execute(args, self.env, job.feed, injected_backend=backend)
            if result is None or not written:
                last = next((x for x in reversed(job.log) if x.startswith("Не удалось")), "")
                job.finish("failed", error=last or "Отчёт не записан.")
            else:
                summary, files = result_summary(result, written, job.id, job.report_stem, self.out_dir)
                job.finish("done", result=summary, files=files)
        except cli.UsageError as e:
            job.finish("failed", error=f"Ошибка в параметрах: {str(e)[:300]}")
        except Exception as e:  # never a traceback on the page or in the terminal
            job.finish("failed", error=f"Внутренняя ошибка агента ({type(e).__name__}). Отчёт не записан.")
        finally:
            with self.lock:
                if self.running is job:
                    self.running = None
            self._say(f"Проверка закончена: {job.doc_name} — "
                      + ("отчёт записан" if job.state == "done" else "отчёт не записан"))

    def status(self, query: dict) -> dict:
        job_id = (query.get("job") or [""])[0]
        try:
            since = int((query.get("since") or ["0"])[0])
        except ValueError:
            raise ApiError(400, "Параметр since должен быть числом.")
        with self.lock:
            job = self.jobs.get(job_id) if job_id else self.latest
        if job is None:
            raise ApiError(404, "Такой проверки нет.")
        return job.status(max(0, since))

    def file(self, job_id: str, name: str) -> Path | None:
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            return None
        with job.lock:
            return job.files.get(name)


class _Handler(BaseHTTPRequestHandler):
    server_version = "Revizor-UI"
    sys_version = ""
    server: _HTTPServer

    # logging: method, path without query, status — nothing else
    def log_request(self, code="-", size="-") -> None:
        if self.command == "GET" and str(code).isdigit() and int(code) < 400:
            return  # the page asks for the status twice a second: only actions and refusals get a line
        path = urlsplit(self.path or "").path
        path = "".join(ch if ch.isprintable() else "?" for ch in path)[:200]
        self.server.ui._say(f"{self.command} {path} {int(code) if str(code).isdigit() else code}")

    def log_message(self, format, *args) -> None:  # noqa: A002 - silence the default stderr log
        pass

    # helpers -------------------------------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        if urlsplit(self.path or "").path.startswith("/api/"):
            self._json(status, {"ok": False, "error": message})
        else:
            self._send(status, message.encode("utf-8"), "text/plain; charset=utf-8")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        port = self.server.ui.port
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.ui.port
        return origin.strip().lower() in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.strip().isdigit():
            return None
        return int(raw.strip())

    def _read_json(self) -> dict:
        n = self._length()
        if n is None:
            raise ApiError(400, "Не указан размер запроса (Content-Length).")
        if n > MAX_JSON_BYTES:
            self.close_connection = True
            raise ApiError(413, "Запрос слишком большой.")
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, "Запрос не в формате JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "Запрос не в формате JSON.")
        return data

    def _guarded(self, fn: Callable[[], None]) -> None:
        try:
            if not self._host_ok():
                self.close_connection = True
                self._send(403, "Доступ запрещён.".encode("utf-8"), "text/plain; charset=utf-8")
                return
            fn()
        except ApiError as e:
            self._error(e.status, e.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self.close_connection = True
            try:
                self._error(500, f"Внутренняя ошибка окна ({type(e).__name__}).")
            except OSError:
                pass

    # methods -------------------------------------------------------------------------------------------------
    def do_GET(self) -> None:
        self._guarded(self._get)

    def do_POST(self) -> None:
        self._guarded(self._post)

    def do_OPTIONS(self) -> None:
        self.close_connection = True
        self._send(403, "Доступ запрещён.".encode("utf-8"), "text/plain; charset=utf-8")

    def _get(self) -> None:
        ui = self.server.ui
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/":
            try:
                body = ui.page_path.read_bytes()
            except OSError:
                self._send(500, "Файл страницы окна не найден.".encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8",
                       {"Content-Security-Policy": PAGE_CSP, "X-Frame-Options": "DENY"})
        elif path == "/api/config":
            self._json(200, ui.config())
        elif path == "/api/status":
            self._json(200, ui.status(parse_qs(parts.query)))
        elif path.startswith("/files/"):
            self._file(path, parse_qs(parts.query))
        else:
            raise ApiError(404, "Не найдено.")

    def _file(self, path: str, query: dict) -> None:
        segs = path.split("/")  # «», «files», job id, name — nothing else
        if len(segs) != 4 or not segs[2] or not segs[3]:
            raise ApiError(404, "Файл не найден.")
        target = self.server.ui.file(segs[2], unquote(segs[3]))
        if target is None:
            raise ApiError(404, "Файл не найден.")
        try:
            body = target.read_bytes()
        except OSError:
            raise ApiError(404, "Файл не найден.")
        headers = {"Content-Security-Policy": FILE_CSP}
        if (query.get("download") or [""])[0] == "1":
            headers["Content-Disposition"] = _content_disposition(target.name)
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, body, ctype, headers)

    def _post(self) -> None:
        ui = self.server.ui
        if self.headers.get("X-Revizor-UI") != "1" or not self._origin_ok():
            self.close_connection = True
            raise ApiError(403, "Запрос отклонён: он пришёл не со страницы окна.")
        parts = urlsplit(self.path)
        if parts.path == "/api/upload":
            q = parse_qs(parts.query)
            kind = (q.get("kind") or [""])[0]
            name = (q.get("name") or [""])[0]
            n = self._length()
            if n is not None and n > 0 and kind in ("doc", "answers"):
                limit = ui.max_doc_bytes if kind == "doc" else ui.max_answers_bytes
                if n > limit:
                    self.close_connection = True  # the body stays unread
            else:
                self.close_connection = True
            self._json(200, ui.upload(kind, name, n, self.rfile))
        elif parts.path == "/api/estimate":
            self._json(200, ui.estimate(self._read_json()))
        elif parts.path == "/api/run":
            self._json(200, ui.run(self._read_json()))
        else:
            raise ApiError(404, "Не найдено.")


# -- entry point from the command line --------------------------------------------------------------------------
def serve(args, env: Mapping[str, str], say: Callable[[str], None], backend=None) -> int:
    """``python main.py --ui``: start the window, print where it is, wait for Ctrl+C. Returns the exit code."""
    cli._provider_of(args)
    cli._cap_of(args, env)
    if args.depth not in ("full", "fast"):
        raise cli.UsageError("глубина может быть только full или fast")
    if not 0 <= args.port <= 65535:
        raise cli.UsageError("порт должен быть числом от 1 до 65535")
    inp = args.input or (args.paths[0] if args.paths else None)
    if inp and not Path(inp).is_file():
        say(f"Документ «{inp}» не найден: выберите его в окне.")
        inp = None
    ui = UIServer(env, port=args.port, out_dir=args.output or DEFAULT_OUT_DIR,
                  defaults={"model": args.model, "depth": args.depth, "max_cost": args.max_cost or ""},
                  provider=args.provider, no_cache=args.no_cache, backend=backend, preload=inp, log=say)
    try:
        try:
            ui.start()
        except OSError:
            say(f"Порт {args.port} занят: возможно, окно уже открыто в другом терминале. "
                f"Откройте его или запустите с другим портом, например: python main.py --ui --port {args.port + 1}")
            return 1
        say(f"Окно открыто: {ui.url} (отчёты пишутся в папку {ui.out_dir})")
        if ui.provider == "offline":
            say(ui.provider_note)
        say("Остановить: Ctrl+C")
        if not args.no_browser:
            opened = False
            if _has_display():
                try:
                    opened = bool(webbrowser.open(ui.url))
                except Exception:
                    opened = False
            if not opened:
                say("Браузер не открылся сам: откройте адрес выше вручную.")
        old_handlers = _stop_on_sigterm()
        try:
            ui.serve_forever()
        except KeyboardInterrupt:
            say("Окно закрыто.")
        finally:
            for sig, old in old_handlers:
                try:
                    signal.signal(sig, old)
                except (ValueError, OSError, TypeError):
                    pass
        return 0
    finally:
        ui.close()


def _stop_on_sigterm() -> list[tuple[int, Any]]:
    """SIGTERM (kill, closing a terminal tab) and, on Windows, Ctrl+Break stop the window like Ctrl+C, so the
    temporary folder is removed. Returns [(signal, previous handler)] for the ones set; [] outside the main thread."""
    def stop(_sig, _frame):
        raise KeyboardInterrupt

    if threading.current_thread() is not threading.main_thread():
        return []
    out = []
    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            out.append((sig, signal.signal(sig, stop)))
        except (ValueError, OSError):
            pass
    return out


def _has_display(environ: Mapping[str, str] | None = None, platform: str | None = None) -> bool:
    """False on a Linux/BSD machine without a graphical session (a server, SSH, Docker): there ``webbrowser`` would
    start a text browser such as lynx inside this very terminal. Windows and macOS always have a display."""
    env = os.environ if environ is None else environ
    plat = sys.platform if platform is None else platform
    if plat.startswith(("win", "cygwin", "darwin")):
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY") or env.get("BROWSER"))


def _remove_tree(folder: Path, tries: int = 5) -> None:
    """``shutil.rmtree`` that waits a little for files still open on Windows (a check that is just finishing, an
    antivirus scan of an upload) instead of leaving the temporary folder behind at once."""
    for k in range(tries):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.1 * (k + 1))
