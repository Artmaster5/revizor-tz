"""``python main.py --ui --public``: the same window as a public site (Render.com and the like).

The local window (``webui.py``) serves one person on their own computer. Here every visitor is a stranger, so:
- each upload and each check has its own random id and its own folder in the temporary directory; a check, its
  report, its questions and answers are reachable only by that id; nothing global points at «the latest check»;
- there is no stored key: the server key comes from the environment (``GEMINI_API_KEY`` by default) and is used only
  within the daily limits; a visitor's own key arrives with one check, lives in memory until that check ends and is
  never written, logged or sent back;
- limits (env vars, see ``Settings``): upload size, checks per address per day, model checks per address and in all per
  day, one model check at a time, a time limit per check; when the server key is missing or its limit is spent the
  check runs by the rules only and the page says so;
- «Открыть готовый пример» serves a report that ships with the agent (``reports/report.html``) without any model call.

The analysis is the same ``cli.execute`` as in the terminal and in the local window.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

from . import __version__, cli, config, webui
from .llm import is_placeholder, make_backend
from .llm import base as llm_base
from .llm.base import LLMUnavailable, redact, register_secret
from .webui import (ANSWERS_FROM_WINDOW, DEPTHS, KEY_LABELS, KEY_PROVIDERS, MAX_RERUN_BYTES, OFFLINE_ID, OFFLINE_LABEL,
                    STAGES, ApiError, Job, answers_file_text, build_argv, details_from, key_shape_problem, result_summary)
from . import answers as ans_mod

AGENT_DIR = Path(__file__).resolve().parents[1]
# The ready example: the submitted report on the organizers' document (Opus, deep mode) and its re-check with answers.
EXAMPLE_REPORTS = {"/example": AGENT_DIR / "reports" / "report.html",
                   "/example/answered": AGENT_DIR / "reports" / "report_answered.html"}
PUBLIC_FORMATS = (".md", ".txt", ".pdf", ".docx", ".html")
MAX_ANSWERS_BYTES = 512 * 1024
TOTAL_SLOTS = 2  # checks at once: one with a model plus one by the rules only
DAY_SHIFT_S = 5 * 3600  # the limits roll over at midnight in Almaty (UTC+5)
STALE_GRACE_S = 300  # a check that outlives its time limit by this much loses its slot
# Variables never handed to a check that runs with a visitor's key (the server's keys and addresses stay on the server).
SERVER_ONLY_VARS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OLLAMA_BASE_URL",
                    "ANTHROPIC_BASE_URL", "GEMINI_BASE_URL", "OPENAI_BASE_URL", "LLM_PROVIDER", "ACCESS_CODE")
KEY_VAR = {name: var for name, _label, var in KEY_PROVIDERS}

BUSY_MODEL = ("Сейчас идёт другая проверка с моделью: сайт проверяет по одному документу за раз. "
              "Попробуйте через минуту или проверьте без модели.")
BUSY_ALL = "Сервер сейчас занят другими проверками. Попробуйте через минуту."
NO_SERVER_KEY = "Ключ модели на сервере не настроен: проверка идёт по автоматическим правилам, без модели."
LIMIT_SPENT = ("Лимит проверок с моделью сервера на сегодня исчерпан: проверка идёт по автоматическим правилам, "
               "без модели. Полная проверка — со своим ключом.")
LOCAL_NOTE = ("Секретный документ лучше проверить у себя: агент работает на вашем компьютере без интернета "
              "или с локальной моделью.")
KEY_NOTE = ("Ключ нужен только для одной проверки: он хранится в памяти сервера до её конца, "
            "никуда не записывается и нигде не показывается.")
# The last line of the questionnaire (must start with «Когда ответите»: the answers parser cuts the file there).
PUBLIC_RERUN_NOTE = ("Когда ответите: на странице проверки впишите ответы и нажмите «Перепроверить с ответами» или "
                     "загрузите тот же документ и эту анкету в поле «Ответы автора». То же из терминала: "
                     "`python main.py --input <путь к документу> --output {output} --answers {answers}`")


def _env_int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = str(env.get(name) or "").strip()
    try:
        v = int(raw) if raw else default
    except ValueError:
        v = default
    return max(lo, min(hi, v))


class Settings:
    """The site's limits from the environment; a missing or broken value takes the default."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self.max_doc_mb = _env_int(env, "PUBLIC_MAX_UPLOAD_MB", 5, 1, 50)
        self.runs_per_ip = _env_int(env, "PUBLIC_RUNS_PER_IP", 3, 0, 10_000)
        self.daily_runs = _env_int(env, "PUBLIC_DAILY_RUNS", 20, 0, 100_000)
        self.checks_per_ip = _env_int(env, "PUBLIC_CHECKS_PER_IP", 20, 1, 100_000)
        self.uploads_per_ip = _env_int(env, "PUBLIC_UPLOADS_PER_IP", 40, 1, 100_000)
        self.run_timeout = _env_int(env, "PUBLIC_RUN_TIMEOUT_S", 900, 30, 7200)
        self.job_ttl = _env_int(env, "PUBLIC_JOB_TTL_MIN", 120, 1, 7 * 24 * 60) * 60
        self.max_jobs = _env_int(env, "PUBLIC_MAX_JOBS", 200, 10, 10_000)
        # The visitor's address behind a proxy: "" or 0 = the TCP peer; "first" = the first X-Forwarded-For entry
        # (any proxy chain; a visitor can forge it, the daily total still holds); N = the N-th entry from the right.
        tp = str(env.get("PUBLIC_TRUST_PROXY") or "").strip().lower()
        self.trust_proxy = -1 if tp == "first" else _env_int(env, "PUBLIC_TRUST_PROXY", 0, 0, 5)
        self.access_code = str(env.get("ACCESS_CODE") or "").strip()
        p = str(env.get("PUBLIC_PROVIDER") or "gemini").strip().lower()
        self.provider = p if p in KEY_LABELS else "gemini"


class Limits:
    """Counters per day (Almaty time): («kind», who) → number. Kept in memory: a restart starts the day afresh."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.day = ""
        self.counts: dict[tuple[str, str], int] = {}

    def _roll(self) -> None:
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clock() + DAY_SHIFT_S))
        if day != self.day:
            self.day, self.counts = day, {}

    def used(self, kind: str, who: str) -> int:
        with self.lock:
            self._roll()
            return self.counts.get((kind, who), 0)

    def take(self, items: list[tuple[str, str, int]]) -> str:
        """Take one of each («kind», who, limit) at once, or none of them: returns the kind that is spent, or ""."""
        with self.lock:
            self._roll()
            for kind, who, limit in items:
                if self.counts.get((kind, who), 0) >= limit:
                    return kind
            for kind, who, _limit in items:
                self.counts[(kind, who)] = self.counts.get((kind, who), 0) + 1
            return ""


@dataclass
class Plan:
    """How one check runs: «server» (the server key), «own» (the visitor's key) or «offline» (rules only)."""
    mode: str
    provider: str
    model: str
    env: dict = field(repr=False, default_factory=dict)
    key: str = field(repr=False, default="")
    note: str = ""
    rerun: bool = False


class PublicJob(Job):
    """A check of the site: the window's Job plus its mode, a note for the page and its own folder."""

    def __init__(self, job_id: str, *, mode: str, note: str, folder: Path, hide: list[str], **kw) -> None:
        super().__init__(job_id, **kw)
        self.mode, self.note, self.folder, self.hide = mode, note, folder, hide

    def feed(self, line: str) -> None:
        for prefix in self.hide:  # the server's temporary folders are nobody's business
            line = line.replace(prefix, "")
        super().feed(line)

    def status(self, since: int = 0) -> dict:
        out = super().status(since)
        out["out_dir"] = ""
        out["mode"], out["note"] = self.mode, self.note
        if out.get("result"):
            out["result"] = dict(out["result"], out_dir="")
        return out


def _default_factory(provider: str, env: Mapping[str, str], model: str, cache_dir: str):
    return make_backend(provider, env, model=model, cache_dir=cache_dir)


def limit_time(backend: Any, deadline: float) -> None:
    """After ``deadline`` (monotonic) the backend sends nothing more: each further request fails at once as
    «модель недоступна», and the pipeline finishes the check with what it has."""
    raw, can = backend._raw, backend.can_answer

    def timed_raw(system, user, max_tokens, **kw):
        if time.monotonic() > deadline:
            raise LLMUnavailable("Время проверки на сайте вышло: оставшиеся шаги сделаны без модели.")
        return raw(system, user, max_tokens, **kw)

    backend._raw = timed_raw
    backend.can_answer = lambda: time.monotonic() <= deadline and can()


def forget_secret(value: str) -> None:
    with llm_base._SECRETS_LOCK:
        llm_base._SECRETS.discard((value or "").strip())


def has_key(provider: str, env: Mapping[str, str]) -> bool:
    keys = dict(cli.AUTO_ORDER).get(provider, ())
    return any((env.get(k) or "").strip() and not is_placeholder(env.get(k)) for k in keys)


class PublicServer(webui.UIServer):
    ID_BYTES = 16

    def __init__(self, env: Mapping[str, str], *, port: int = 8000, host: str = "0.0.0.0",
                 page_path: str | Path | None = None, log: Callable[[str], None] | None = None,
                 backend_factory: Callable[..., Any] | None = None,
                 example_reports: Mapping[str, str | Path] | None = None,
                 example_path: str | Path | None = None, clock: Callable[[], float] | None = None) -> None:
        self.settings = s = Settings(env)
        super().__init__(env, port=port, out_dir="", provider="offline", page_path=page_path, log=log,
                         max_doc_bytes=s.max_doc_mb * 1024 * 1024, max_answers_bytes=MAX_ANSWERS_BYTES,
                         example_path=example_path)
        self.host = host
        self.backend_factory = backend_factory or _default_factory
        self.limits = Limits(clock or time.time)
        self.active: dict[str, PublicJob] = {}
        self.example_reports = {k: Path(v) for k, v in (example_reports or EXAMPLE_REPORTS).items()}
        base = {k: v for k, v in dict(env).items() if k not in SERVER_ONLY_VARS}
        self.clean_env = base  # what a rules-only check and a visitor's check start from
        self.server_provider = s.provider
        keep = set(dict(cli.AUTO_ORDER)[s.provider]) | {s.provider.upper() + "_BASE_URL"}
        self.server_env = dict(base, **{k: v for k, v in dict(env).items() if k in keep})
        self.server_ready = has_key(s.provider, self.server_env)
        self.server_model = config.resolve_model(s.provider, self.server_env)
        self.jobs_dir = self.temp_root / "jobs"
        self.jobs_dir.mkdir()

    # lifecycle -----------------------------------------------------------------------------------------------
    def start(self) -> int:
        httpd = _PublicHTTPServer((self.host, self.port), _PublicHandler)
        httpd.ui = self
        self.httpd = httpd
        self.port = httpd.server_address[1]
        return self.port

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "", "::") else self.host
        return f"http://{host}:{self.port}/"

    # what the page needs -------------------------------------------------------------------------------------
    def config(self, ip: str = "") -> dict:
        s = self.settings
        left_ip = max(0, s.runs_per_ip - self.limits.used("model", ip))
        left_all = max(0, s.daily_runs - self.limits.used("model", "*"))
        server_ok = self.server_ready and left_ip > 0 and left_all > 0
        note = "" if server_ok else (LIMIT_SPENT if self.server_ready else NO_SERVER_KEY)
        if note:
            note += " Можно вставить свой ключ ниже."
        models = ([{"id": "server", "label": f"{self.server_model} — ключ сервера, бесплатно"}]
                  if self.server_ready else [])
        models.append({"id": OFFLINE_ID, "label": OFFLINE_LABEL})
        limits_text = (f"Файл до {s.max_doc_mb} МБ. С ключом сервера — до {s.runs_per_ip} "
                       f"{_plural(s.runs_per_ip, ('проверки', 'проверок', 'проверок'))} с одного адреса в день; "
                       f"когда лимит исчерпан, проверка идёт по правилам, без модели. Со своим ключом лимита модели нет.")
        return {"ok": True, "version": __version__, "provider": "server" if server_ok else "offline",
                "provider_note": note, "models": models,
                "default_model": "server" if server_ok else OFFLINE_ID,
                "user_key": {"set": False, "provider": "", "label": ""},
                "key_providers": [{"id": i, "label": label} for i, label, _var in KEY_PROVIDERS],
                "key_models": {i: config.resolve_model(i, {}) for i, _l, _v in KEY_PROVIDERS},
                "depths": [{"id": i, "label": label} for i, label in DEPTHS], "default_depth": "full",
                "formats": list(PUBLIC_FORMATS), "max_doc_mb": s.max_doc_mb,
                "max_answers_mb": round(MAX_ANSWERS_BYTES / 1024 / 1024, 2),
                "default_cap": "", "out_dir": "", "stages": list(STAGES),
                "example": ({"name": webui.EXAMPLE_NAME, "size": self.example_path.stat().st_size}
                            if self.example_path.is_file() else None),
                "preloaded": None, "job": None,
                "public": {"server_model": self.server_ready, "access_code": bool(s.access_code),
                           "left_today": left_ip if self.server_ready else 0,
                           "limits_text": limits_text, "local_note": LOCAL_NOTE, "key_note": KEY_NOTE,
                           "example_url": "/example" if self.example_reports["/example"].is_file() else "",
                           "example_answered_url": ("/example/answered"
                                                    if self.example_reports.get("/example/answered", Path()).is_file()
                                                    else "")}}

    def set_key(self, body: dict) -> dict:  # the site keeps no key: it comes with each check
        raise ApiError(404, "Не найдено.")

    def forget_key(self) -> dict:
        raise ApiError(404, "Не найдено.")

    def take_upload(self, ip: str) -> None:
        if self.limits.take([("upload", ip, self.settings.uploads_per_ip)]):
            raise ApiError(429, "С этого адреса сегодня загружено слишком много файлов. Попробуйте завтра.")

    def example(self, body: dict) -> dict:
        """«Попробовать на примере»: the organizers' document becomes this visitor's upload; nothing starts."""
        src = self.example_path
        if not src.is_file():
            raise ApiError(404, "Примера в этой установке нет.")
        uid = secrets.token_hex(self.ID_BYTES)
        folder = self.temp_root / uid
        dest = folder / webui.EXAMPLE_NAME
        try:
            folder.mkdir()
            shutil.copyfile(src, dest)
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise ApiError(400, "Пример не удалось загрузить.")
        entry = {"id": uid, "kind": "doc", "name": webui.EXAMPLE_NAME, "path": dest, "size": dest.stat().st_size,
                 "t": time.monotonic(), "pinned": False}
        with self.lock:
            self.uploads[uid] = entry
            self._drop_old_uploads()
        return {"ok": True, "upload_id": uid, "name": webui.EXAMPLE_NAME, "size": entry["size"], "mode": "ready",
                "job_id": None}

    def estimate(self, body: dict) -> dict:
        u = self._upload(body.get("doc"), "doc")
        depth = self._depth(body.get("depth", "full"))
        choice = body.get("model")
        if choice == OFFLINE_ID:
            provider, model, env = "offline", OFFLINE_ID, {}
        elif choice == "server" and self.server_ready:
            provider, model, env = self.server_provider, self.server_model, self.clean_env
        elif choice == "own" and body.get("key_provider") in KEY_LABELS:
            provider = body["key_provider"]
            provider, model, env = provider, config.resolve_model(provider, {}), self.clean_env
        else:
            raise ApiError(400, "Такой модели нет в списке.")
        try:
            out = webui.estimate_for(str(u["path"]), provider=provider, model=model, depth=depth, env=env)
        except Exception as e:
            raise ApiError(400, f"Оценка не удалась: внутренняя ошибка ({type(e).__name__}).")
        if choice == "server" and out.get("readable"):
            out["cost_text"], out["suggested_cap"] = "$0 для вас", ""
            out["note"] = "Модель сервера на бесплатном тарифе: для вас проверка бесплатна."
        return out

    # starting a check ----------------------------------------------------------------------------------------
    def _plan(self, choice: Any, body: dict, *, rerun: bool = False) -> Plan:
        if choice == "own":
            provider = body.get("key_provider")
            if not isinstance(provider, str) or provider not in KEY_LABELS:
                raise ApiError(400, "Ключ не принят: выберите провайдера из списка.")
            why = key_shape_problem(body.get("key"))
            if why:
                raise ApiError(400, "Ключ не принят: " + why)
            key = body["key"].strip()
            return Plan("own", provider, config.resolve_model(provider, {}),
                        dict(self.clean_env, **{KEY_VAR[provider]: key}), key, rerun=rerun)
        if choice == "server":
            if not self.server_ready:
                return Plan("offline", "offline", OFFLINE_ID, dict(self.clean_env), note=NO_SERVER_KEY, rerun=rerun)
            code = self.settings.access_code
            if code:
                got = body.get("access_code")
                got = got.strip() if isinstance(got, str) else ""
                if not hmac.compare_digest(got.encode("utf-8"), code.encode("utf-8")):
                    raise ApiError(403, "Код доступа не подошёл. Без кода можно проверить по правилам или со своим ключом.")
            return Plan("server", self.server_provider, self.server_model, dict(self.server_env), rerun=rerun)
        if choice == OFFLINE_ID:
            return Plan("offline", "offline", OFFLINE_ID, dict(self.clean_env), rerun=rerun)
        raise ApiError(400, "Такой модели нет в списке.")

    def run(self, body: dict, ip: str = "") -> dict:
        doc = self._upload(body.get("doc"), "doc")
        ans = self._upload(body["answers"], "answers") if body.get("answers") else None
        depth = self._depth(body.get("depth", "full"))
        plan = self._plan(body.get("model"), body)
        cap = self._cap(body.get("max_cost")) if plan.mode == "own" else ""
        return self._start_public(doc, ans, depth, cap, plan, ip)

    def rerun(self, body: dict, ip: str = "") -> dict:
        """«Перепроверить с ответами»: the same document and depth, plus the answers typed on the page. The mode is
        the first check's; a visitor's key has to come again (the site keeps none)."""
        job = self._job(body.get("job"))
        raw = body.get("answers")
        if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
            raise ApiError(400, "Ответы должны прийти списком «номер вопроса — текст».")
        with job.lock:
            d, state, mode = job.details, job.state, job.mode
        if state == "running":
            raise ApiError(409, "Проверка ещё идёт: дождитесь её окончания.")
        if d is None or not d["questions"]:
            raise ApiError(400, "У этой проверки нет вопросов, на которые можно ответить.")
        if mode == "offline":
            plan = self._plan(OFFLINE_ID, body, rerun=True)
        elif body.get("key"):
            plan = self._plan("own", body, rerun=True)
        elif mode == "own":
            raise ApiError(400, "Вставьте ключ ещё раз: сайт не хранит его после проверки.")
        else:
            plan = self._plan("server", body, rerun=True)
        cap = self._cap(body.get("max_cost")) if plan.mode == "own" else ""
        known = {q["id"]: q for q in d["questions"]}
        typed: dict[str, str] = {}
        for qid, text in raw.items():
            q = known.get(qid)
            if q is None:
                continue
            text = " ".join(text.split())[:ans_mod.MAX_ANSWER_CHARS]
            if ans_mod.interpret(q["id"], text, q["options"]) is not None:
                typed[q["id"]] = text
        if not typed:
            raise ApiError(400, "Ни на один вопрос нет ответа: впишите хотя бы один.")
        with self.lock:
            doc = self.uploads.get(job.doc_upload)
        if doc is None or not Path(doc["path"]).is_file():
            raise ApiError(404, "Документ уже удалён с сервера: загрузите его ещё раз и проверьте заново.")
        uid = secrets.token_hex(self.ID_BYTES)
        folder = self.temp_root / uid
        dest = folder / "answers.md"
        data = answers_file_text(typed, {qid: known[qid].get("fingerprint", "") for qid in typed},
                                 d.get("doc_fingerprint", "")).encode("utf-8")
        try:
            folder.mkdir()
            dest.write_bytes(data)
        except OSError:
            shutil.rmtree(folder, ignore_errors=True)
            raise ApiError(400, "Ответы не удалось сохранить.")
        entry = {"id": uid, "kind": "answers", "name": "answers.md", "path": dest, "size": len(data),
                 "t": time.monotonic(), "pinned": False}
        with self.lock:
            self.uploads[uid] = entry
        try:
            return self._start_public(doc, entry, job.depth, cap, plan, ip, typed=typed, previous=job,
                                      answers_label=ANSWERS_FROM_WINDOW)
        except ApiError:
            with self.lock:
                self.uploads.pop(uid, None)
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def _start_public(self, doc: dict, ans: dict | None, depth: str, cap: str, plan: Plan, ip: str, *,
                      typed: dict | None = None, previous: Job | None = None, answers_label: str = "") -> dict:
        s = self.settings
        with self.lock:
            self._cleanup_locked()
            if plan.mode != "offline" and any(j.mode != "offline" for j in self.active.values()):
                raise ApiError(429, BUSY_MODEL)
            if len(self.active) >= TOTAL_SLOTS:
                raise ApiError(429, BUSY_ALL)
            check = ("check", ip, s.checks_per_ip)
            items = [check] + ([("model", ip, s.runs_per_ip), ("model", "*", s.daily_runs)]
                               if plan.mode == "server" else [])
            spent = self.limits.take(items)
            if spent == "model":
                if plan.rerun:
                    raise ApiError(429, "Лимит проверок с моделью сервера на сегодня исчерпан. Перепроверить с "
                                        "моделью можно со своим ключом: вставьте его выше.")
                plan = Plan("offline", "offline", OFFLINE_ID, dict(self.clean_env), note=LIMIT_SPENT)
                spent = self.limits.take([check])
            if spent:
                raise ApiError(429, f"С этого адреса сегодня уже было {s.checks_per_ip} "
                                    f"{_plural(s.checks_per_ip, ('проверка', 'проверки', 'проверок'))}: это предел "
                                    "сайта. Завтра счёт начнётся заново.")
            job_id = secrets.token_hex(self.ID_BYTES)
            folder = self.jobs_dir / job_id
            out_dir = folder / "out"
            out_dir.mkdir(parents=True)
            stem = Path(doc["name"]).stem or "document"
            out_path = out_dir / (stem + ("_answered" if ans else "") + ".md")
            common = dict(doc_path=str(doc["path"]), out_path=str(out_path), depth=depth,
                          answers_path=str(ans["path"]) if ans else "")
            argv = build_argv(model=plan.model, provider_arg=plan.provider, max_cost=cap, **common)
            argv_offline = build_argv(model=OFFLINE_ID, **common)
            dirs = {folder, Path(doc["path"]).parent} | ({Path(ans["path"]).parent} if ans else set())
            hide = sorted({form for d in dirs for form in (str(d) + os.sep, d.as_posix() + "/", str(d))},
                          key=len, reverse=True)
            job = PublicJob(job_id, mode=plan.mode, note=plan.note, folder=folder, hide=hide,
                            doc_name=doc["name"], answers_name=(answers_label or ans["name"]) if ans else "",
                            provider=plan.provider, model=plan.model, depth=depth, max_cost=cap, out_dir="",
                            report_stem=out_path.stem)
            job.doc_upload, job.answers_upload = doc["id"], ans["id"] if ans else ""
            job.typed = dict(typed or {})
            job.previous = webui.previous_snapshot(previous) if previous is not None else None
            job.cache_dir = Path(doc["path"]).parent / ".llm-cache"  # the same document's re-check reuses answers
            self.jobs[job_id] = job
            self.active[job_id] = job
        self._say(f"Проверка {job_id[:6]} начата: режим {plan.mode}, глубина {depth}"
                  + (", с ответами" if ans else ""))
        threading.Thread(target=self._work_public, args=(job, argv, argv_offline, plan, hide), name="revizor-public-check",
                         daemon=True).start()
        return {"ok": True, "job_id": job_id, "mode": plan.mode, "note": plan.note}

    def _work_public(self, job: PublicJob, argv: list[str], argv_offline: list[str], plan: Plan,
                     hide: list[str]) -> None:
        backend = None
        if plan.key:
            register_secret(plan.key)  # every error text hides it from now on
        try:
            if plan.mode != "offline":
                try:
                    backend = self.backend_factory(plan.provider, plan.env, plan.model, str(job.cache_dir))
                    limit_time(backend, job.t0 + self.settings.run_timeout)
                except LLMUnavailable as e:
                    job.feed("Модель недоступна: " + redact(e.user_message)[:300])
                    backend, argv = None, argv_offline
                    with job.lock:
                        job.provider, job.model = "offline", OFFLINE_ID
            args = cli.build_parser().parse_args(argv)
            args.rerun_note = PUBLIC_RERUN_NOTE
            args.hide_prefixes = [h for h in hide if h.endswith(("/", os.sep))]
            _code, result, written = cli.execute(args, plan.env, job.feed, injected_backend=backend)
            if job.state != "running":  # the slot was already taken back as stale
                return
            if result is None or not written:
                last = next((x for x in reversed(job.log) if x.startswith("Не удалось")), "")
                job.finish("failed", error=last or "Отчёт не записан.")
            else:
                summary, files = result_summary(result, written, job.id, job.report_stem, "",
                                                with_answers=bool(job.answers_upload), previous=job.previous)
                summary["stage_seconds"] = job.final_seconds(getattr(result.run, "stage_seconds", None))
                if result.diagnostic is None:
                    job.details = details_from(result, offline=job.model == OFFLINE_ID or job.provider == "offline")
                job.finish("done", result=summary, files=files)
        except cli.UsageError as e:
            job.finish("failed", error=f"Ошибка в параметрах: {redact(e)[:300]}")
        except Exception as e:  # never a traceback on the page or in the server log
            job.finish("failed", error=f"Внутренняя ошибка агента ({type(e).__name__}). Отчёт не записан.")
        finally:
            backend = None
            if plan.key:
                forget_secret(plan.key)
            plan.key, plan.env = "", {}
            with self.lock:
                self.active.pop(job.id, None)
            self._say(f"Проверка {job.id[:6]} закончена: " + ("отчёт записан" if job.state == "done" else "без отчёта"))

    # bookkeeping ---------------------------------------------------------------------------------------------
    def _drop_old_uploads(self) -> None:  # called by upload() under self.lock
        self._cleanup_locked()

    def _cleanup_locked(self) -> None:
        """Old checks and uploads leave memory and disk; a check far past its time limit loses its slot."""
        s = self.settings
        now = time.monotonic()
        for job in list(self.active.values()):
            if now - job.t0 > s.run_timeout + STALE_GRACE_S:
                job.finish("failed", error="Проверка заняла слишком долго и остановлена.")
                self.active.pop(job.id, None)
        finished = sorted((j for j in self.jobs.values() if j.id not in self.active), key=lambda j: j.t0)
        excess = len(self.jobs) - s.max_jobs
        for job in finished:
            old = job.t_end is not None and now - job.t_end > s.job_ttl
            if old or excess > 0:
                self.jobs.pop(job.id, None)
                excess -= 1
                if isinstance(job, PublicJob) and job.folder.parent == self.jobs_dir:
                    shutil.rmtree(job.folder, ignore_errors=True)
        busy = {u for j in self.active.values() for u in (j.doc_upload, j.answers_upload)}
        busy |= {j.doc_upload for j in self.jobs.values()}  # a kept check may still be re-checked
        own = sorted(self.uploads.values(), key=lambda u: u["t"])
        excess = len(own) - s.max_jobs * 2
        for u in own:
            stale = now - u["t"] > s.job_ttl and u["id"] not in busy
            if stale or (excess > 0 and u["id"] not in busy):
                self.uploads.pop(u["id"], None)
                excess -= 1
                folder = (self.temp_root / u["id"]).resolve()
                if folder.parent == self.temp_root:
                    shutil.rmtree(folder, ignore_errors=True)

    def _job(self, job_id: Any) -> Job:
        if not isinstance(job_id, str) or not job_id:
            raise ApiError(404, "Такой проверки нет.")
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise ApiError(404, "Такой проверки нет: возможно, она устарела и удалена. Загрузите документ ещё раз.")
        return job

    def status(self, query: dict) -> dict:
        job_id = (query.get("job") or [""])[0]
        if not job_id:
            raise ApiError(404, "Такой проверки нет.")
        try:
            since = int((query.get("since") or ["0"])[0])
        except ValueError:
            raise ApiError(400, "Параметр since должен быть числом.")
        return self._job(job_id).status(max(0, since))


def _plural(n: int, forms: tuple[str, str, str]) -> str:
    from .textfmt import plural
    return plural(n, forms)


# -- HTTP ---------------------------------------------------------------------------------------------------------
class _PublicHTTPServer(webui._HTTPServer):
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        self.ui._say("Ошибка при обработке запроса.")


SITE_HEADERS = {"Strict-Transport-Security": "max-age=31536000", "X-Frame-Options": "DENY",
                "Cross-Origin-Opener-Policy": "same-origin", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}


class _PublicHandler(webui._Handler):
    server_version = "Revizor"
    timeout = 120  # a connection that sends nothing for two minutes is closed
    server: _PublicHTTPServer

    def _send(self, status: int, body: bytes, ctype: str, headers: Mapping[str, str] | None = None) -> None:
        merged = dict(SITE_HEADERS)
        merged.update(headers or {})
        super()._send(status, body, ctype, merged)

    def _host_ok(self) -> bool:  # a public site answers under any name the platform gives it
        return True

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        o = urlsplit(origin.strip().lower())
        return bool(host) and o.scheme in ("http", "https") and o.netloc == host

    def client_ip(self) -> str:
        hops = self.server.ui.settings.trust_proxy
        ip = str(self.client_address[0])
        if hops:
            parts = [p.strip() for p in (self.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
            if parts:
                ip = parts[0] if hops < 0 or len(parts) < hops else parts[-hops]
        return ip[:64]

    def log_request(self, code="-", size="-") -> None:  # as the window, but a check's id is cut to 6 signs
        if self.command in ("GET", "HEAD") and str(code).isdigit() and int(code) < 400:
            return
        path = re.sub(r"[0-9a-f]{12,}", lambda m: m.group(0)[:6] + "…", urlsplit(self.path or "").path)
        path = "".join(ch if ch.isprintable() else "?" for ch in path)[:200]
        self.server.ui._say(f"{self.command} {path} {int(code) if str(code).isdigit() else code}")

    def do_HEAD(self) -> None:
        self._guarded(self._get)

    def _get(self) -> None:
        ui = self.server.ui
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
        elif path in ui.example_reports:
            try:
                body = ui.example_reports[path].read_bytes()
            except OSError:
                raise ApiError(404, "Примера в этой установке нет.")
            self._send(200, body, "text/html; charset=utf-8", {"Content-Security-Policy": webui.FILE_CSP})
        elif path == "/api/config":
            self._json(200, ui.config(self.client_ip()))
        else:
            super()._get()

    def _post(self) -> None:
        ui = self.server.ui
        if self.headers.get("X-Revizor-UI") != "1" or not self._origin_ok():
            self.close_connection = True
            raise ApiError(403, "Запрос отклонён: он пришёл не со страницы сайта.")
        ip = self.client_ip()
        parts = urlsplit(self.path)
        if parts.query and parts.path != "/api/upload":  # a key or a code is accepted in the body only
            self.close_connection = True
            raise ApiError(400, "Параметры принимаются только в теле запроса, не в адресе.")
        if parts.path == "/api/upload":
            q = parse_qs(parts.query)
            kind = (q.get("kind") or [""])[0]
            name = (q.get("name") or [""])[0]
            n = self._length()
            limit = ui.max_doc_bytes if kind == "doc" else ui.max_answers_bytes
            if n is None or n <= 0 or n > limit or kind not in ("doc", "answers"):
                self.close_connection = True  # the body stays unread
            try:
                ui.take_upload(ip)
            except ApiError:
                self.close_connection = True
                raise
            self._json(200, ui.upload(kind, name, n, self.rfile))
        elif parts.path == "/api/estimate":
            self._json(200, ui.estimate(self._read_json()))
        elif parts.path == "/api/run":
            self._json(200, ui.run(self._read_json(), ip))
        elif parts.path == "/api/example":
            body = self._read_json()
            ui.take_upload(ip)
            self._json(200, ui.example(body))
        elif parts.path == "/api/rerun":
            self._json(200, ui.rerun(self._read_json(MAX_RERUN_BYTES), ip))
        else:
            raise ApiError(404, "Не найдено.")


# -- entry point --------------------------------------------------------------------------------------------------
def serve(args, env: Mapping[str, str], say: Callable[[str], None]) -> int:
    """``python main.py --ui --public``: the site. Port: $PORT, else --port. Address: $PUBLIC_HOST, else 0.0.0.0."""
    port = _env_int(env, "PORT", args.port, 0, 65535) if str(env.get("PORT") or "").strip() else args.port
    if not 0 <= port <= 65535:
        raise cli.UsageError("порт должен быть числом от 1 до 65535")
    host = str(env.get("PUBLIC_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    ui = PublicServer(env, port=port, host=host, log=say)
    try:
        try:
            ui.start()
        except OSError:
            say(f"Порт {port} занят: укажите другой (--port или переменная PORT).")
            return 1
        s = ui.settings
        say(f"Сайт открыт: {ui.url} (слушает {host}:{ui.port})")
        say(("Модель сервера: " + ui.server_provider + " / " + ui.server_model) if ui.server_ready
            else "Ключа модели на сервере нет: без своего ключа проверки идут по правилам.")
        say(f"Лимиты: файл до {s.max_doc_mb} МБ; с ключом сервера {s.runs_per_ip} на адрес и {s.daily_runs} всего "
            f"в день; проверок на адрес в день {s.checks_per_ip}; одна проверка с моделью за раз; "
            f"до {s.run_timeout} с на проверку" + ("; нужен код доступа" if s.access_code else ""))
        say("Остановить: Ctrl+C")
        old_handlers = webui._stop_on_sigterm()
        try:
            ui.serve_forever()
        except KeyboardInterrupt:
            say("Сайт остановлен.")
        finally:
            import signal
            for sig, old in old_handlers:
                try:
                    signal.signal(sig, old)
                except (ValueError, OSError, TypeError):
                    pass
        return 0
    finally:
        ui.close()
