"""The public site (``python main.py --public``): a real server on 127.0.0.1 in a thread, stdlib HTTP client.
No network beyond 127.0.0.1 and no real key: rules-only checks or the scripted FakeBackend of the pipeline tests,
handed in through ``backend_factory``."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import quote

from revizor import cli, public, webui
from revizor.llm import FakeBackend
from revizor.llm import base as llm_base
from revizor.llm.base import LLMUnavailable

from tests.test_pipeline import DOC, make_script

AGENT = Path(__file__).resolve().parents[1]
FIXTURE = AGENT / "tests" / "fixtures" / "p2p_transfer_tz.md"
ENV = {"REVIZOR_NO_DOTENV": "1", "PATH": os.environ.get("PATH", "")}
SERVER_KEY = "AIza" "SERVER-TEST-KEY-0001"  # split so that the secret scan before a commit stays clean
VISITOR_KEY = "AIza" "VISITOR-TEST-KEY-0002"
HOST = "revizor-test.onrender.com"


class _Site(unittest.TestCase):
    env: dict = ENV
    extra: dict = {}

    def setUp(self) -> None:
        self._old = os.environ.get("REVIZOR_NO_DOTENV")
        os.environ["REVIZOR_NO_DOTENV"] = "1"
        self.tmp = Path(tempfile.mkdtemp(prefix="revizor-public-test-"))
        self.logged: list[str] = []
        self.made: list[dict] = []  # what each model check got from the server (provider, env, model)
        self.script = make_script()

        def factory(provider, env, model, cache_dir):
            self.made.append({"provider": provider, "env": dict(env), "model": model, "cache_dir": cache_dir})
            return FakeBackend(self.script)

        self.ui = public.PublicServer(dict(self.env, **self.extra), port=0, host="127.0.0.1", log=self.logged.append,
                                      backend_factory=factory)
        self.port = self.ui.start()
        self.thread = threading.Thread(target=self.ui.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        root = self.ui.temp_root
        self.ui.close()
        self.thread.join(timeout=5)
        self.assertFalse(root.exists(), "the temporary folder must be removed on close()")
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._old is None:
            os.environ.pop("REVIZOR_NO_DOTENV", None)
        else:
            os.environ["REVIZOR_NO_DOTENV"] = self._old

    # client ------------------------------------------------------------------------------------------------
    def req(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None,
            ip: str = "") -> tuple[int, dict, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": HOST}
        if ip:
            h["X-Forwarded-For"] = ip
        h.update(headers or {})
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def jpost(self, path: str, obj=None, *, raw: bytes | None = None, headers: dict | None = None,
              ip: str = "") -> tuple[int, dict]:
        h = {"X-Revizor-UI": "1", "Content-Type": "application/json"}
        h.update(headers or {})
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        st, _h, b = self.req("POST", path, body, h, ip=ip)
        return st, json.loads(b.decode("utf-8"))

    def jget(self, path: str, ip: str = "") -> tuple[int, dict]:
        st, _h, b = self.req("GET", path, ip=ip)
        return st, json.loads(b.decode("utf-8"))

    def upload(self, name: str, data: bytes, kind: str = "doc", ip: str = "") -> tuple[int, dict]:
        return self.jpost(f"/api/upload?kind={kind}&name={quote(name, safe='')}", raw=data,
                          headers={"Content-Type": "application/octet-stream"}, ip=ip)

    def start(self, model: str = "offline", data: bytes | None = None, ip: str = "", **extra) -> tuple[int, dict]:
        st, up = self.upload("doc.md", data if data is not None else DOC.encode("utf-8"), ip=ip)
        self.assertEqual(st, 200, up)
        return self.jpost("/api/run", dict({"doc": up["upload_id"], "model": model, "depth": "full"}, **extra), ip=ip)

    def wait_done(self, job_id: str, timeout: float = 90) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st, data = self.jget(f"/api/status?job={job_id}")
            self.assertEqual(st, 200, data)
            if data["state"] != "running":
                return data
            time.sleep(0.05)
        self.fail("the check did not finish in time")

    def everything_text(self, job_ids: list[str]) -> str:
        """Every answer the site gives about these checks plus every file under its temporary folder."""
        out = []
        for jid in job_ids:
            for path in (f"/api/status?job={jid}", f"/api/details?job={jid}"):
                out.append(self.req("GET", path)[2].decode("utf-8", "replace"))
            _st, s = self.jget(f"/api/status?job={jid}")
            for f in (s.get("result") or {}).get("files") or []:
                out.append(self.req("GET", f["url"])[2].decode("utf-8", "replace"))
        out.append(self.req("GET", "/api/config")[2].decode("utf-8", "replace"))
        for p in sorted(self.ui.temp_root.rglob("*")):
            if p.is_file():
                out.append(p.read_bytes().decode("utf-8", "replace"))
        out += self.logged
        return "\n".join(out)


class BasicsTests(_Site):
    def test_healthz_page_and_headers(self):
        st, h, b = self.req("GET", "/healthz")
        self.assertEqual((st, b), (200, b"ok"))
        self.assertEqual(self.req("HEAD", "/healthz")[0], 200)
        st, h, b = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("default-src 'none'", h["content-security-policy"])
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertIn("max-age", h["strict-transport-security"])
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertNotIn("access-control-allow-origin", h)
        self.assertIn("pub-intro", b.decode("utf-8"))

    def test_config_without_server_key(self):
        st, c = self.jget("/api/config")
        self.assertEqual(st, 200)
        self.assertEqual(c["provider"], "offline")
        self.assertIn("не настроен", c["provider_note"])
        self.assertEqual([m["id"] for m in c["models"]], ["offline"])
        self.assertEqual(c["default_model"], "offline")
        self.assertIsNone(c["job"])
        self.assertEqual([p["id"] for p in c["key_providers"]], ["anthropic", "gemini", "openai"])
        self.assertEqual(c["max_doc_mb"], 5)
        self.assertEqual(c["public"]["example_url"], "/example")
        self.assertIn("никуда не записывается", c["public"]["key_note"])
        self.assertIn("локальной моделью", c["public"]["local_note"])

    def test_ready_example_is_instant_and_sandboxed(self):
        for path in ("/example", "/example/answered"):
            st, h, b = self.req("GET", path)
            self.assertEqual(st, 200, path)
            self.assertEqual(h["content-security-policy"], webui.FILE_CSP)
            self.assertIn("theme-btn", b.decode("utf-8"))
        self.assertEqual(self.made, [], "the ready example calls no model")
        st, ex = self.jpost("/api/example", {})
        self.assertEqual(st, 200, ex)
        self.assertEqual((ex["mode"], ex["job_id"], ex["name"]), ("ready", None, "requirements.md"))
        self.assertEqual(len(ex["upload_id"]), 32)

    def test_no_window_key_endpoints_and_no_latest_job(self):
        self.assertEqual(self.jpost("/api/key", {"provider": "gemini", "key": VISITOR_KEY})[0], 404)
        self.assertEqual(self.jpost("/api/key/forget", {})[0], 404)
        self.assertEqual(self.jget("/api/status")[0], 404)
        self.assertEqual(self.jget("/api/details")[0], 404)

    def test_post_guards(self):
        st, d = self.jpost("/api/run", {}, headers={"X-Revizor-UI": "0"})
        self.assertEqual(st, 403)
        st, d = self.jpost("/api/run", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(st, 403)
        st, d = self.jpost("/api/run", {"doc": "x"}, headers={"Origin": "https://" + HOST})
        self.assertEqual(st, 404, d)  # passed the guard; the upload is unknown
        st, d = self.jpost("/api/run?key=" + VISITOR_KEY, {})
        self.assertEqual(st, 400)
        self.assertNotIn(VISITOR_KEY, json.dumps(d))
        self.assertNotIn(VISITOR_KEY, "\n".join(self.logged))
        self.assertEqual(self.req("OPTIONS", "/api/run")[0], 403)


class RulesOnlyTests(_Site):
    def test_without_server_key_a_server_check_runs_by_rules_and_says_so(self):
        st, run = self.start("server")
        self.assertEqual(st, 200, run)
        self.assertEqual(run["mode"], "offline")
        self.assertEqual(run["note"], public.NO_SERVER_KEY)
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        self.assertEqual((s["model"], s["mode"], s["note"]), ("offline", "offline", public.NO_SERVER_KEY))
        self.assertEqual(s["result"]["out_dir"], "")
        self.assertEqual(self.made, [])
        self.assertFalse(any(str(self.ui.temp_root) in line for line in s["log"]), "no server folder in the log")
        self.assertEqual(len(run["job_id"]), 32)

    def test_upload_limits(self):
        st, d = self.upload("doc.exe", b"MZ")
        self.assertEqual(st, 400)
        self.assertEqual(self.ui.max_doc_bytes, 5 * 1024 * 1024)
        self.ui.max_doc_bytes = 100  # a small limit: a large body would break the pipe before the answer
        st, d = self.upload("doc.md", b"x" * 101)
        self.assertEqual(st, 413)
        self.assertEqual(self.upload("doc.md", b"x" * 100)[0], 200)
        st, d = self.upload("../../evil.md", b"# x\n")
        self.assertEqual(st, 200)
        stored = self.ui.uploads[d["upload_id"]]["path"].resolve()
        self.assertTrue(stored.is_relative_to(self.ui.temp_root))
        self.assertEqual(stored.name, "evil.md")


class IsolationTests(_Site):
    def test_two_jobs_never_see_each_other(self):
        st, a = self.start("offline", DOC.encode("utf-8"), ip="10.0.0.1")
        st, b = self.start("offline", FIXTURE.read_bytes(), ip="10.0.0.2")
        sa, sb = self.wait_done(a["job_id"]), self.wait_done(b["job_id"])
        self.assertEqual((sa["state"], sb["state"]), ("done", "done"))
        self.assertNotEqual(a["job_id"], b["job_id"])
        fa = {f["name"]: f["url"] for f in sa["result"]["files"]}
        fb = {f["name"]: f["url"] for f in sb["result"]["files"]}
        self.assertTrue(all(u.startswith(f"/files/{a['job_id']}/") for u in fa.values()))
        # A's file names under B's id are not B's files, and an unknown id gives nothing
        for name in fa:
            st, _h, body = self.req("GET", f"/files/{b['job_id']}/{quote(name, safe='')}")
            if name in fb:
                self.assertNotEqual(body, self.req("GET", fa[name])[2])
            else:
                self.assertEqual(st, 404)
            self.assertEqual(self.req("GET", f"/files/{'0' * 32}/{quote(name, safe='')}")[0], 404)
        # every check has its own folder: A's report is not inside B's folder
        ja, jb = self.ui.jobs[a["job_id"]], self.ui.jobs[b["job_id"]]
        self.assertNotEqual(ja.folder, jb.folder)
        for p in ja.files.values():
            self.assertTrue(p.resolve().is_relative_to(ja.folder))
        self.assertEqual(self.jget(f"/api/status?job={'f' * 32}")[0], 404)
        self.assertEqual(self.jget(f"/api/details?job={'f' * 32}")[0], 404)
        self.assertEqual(self.jpost("/api/run", {"doc": "0" * 32, "model": "offline", "depth": "full"})[0], 404)

    def test_path_traversal_is_refused(self):
        st, a = self.start("offline")
        s = self.wait_done(a["job_id"])
        jid = a["job_id"]
        for path in (f"/files/{jid}/..%2F..%2F..%2Fetc%2Fpasswd", f"/files/{jid}/../out/doc.md", "/files/../../etc/passwd",
                     f"/files/{jid}/%2Fetc%2Fpasswd", f"/files/{jid}/doc.md/../../x", "/example/../README.md",
                     "/../README.md", "/README.md", "/revizor/public.py", f"/files/{jid}"):
            st, _h, body = self.req("GET", path)
            self.assertEqual(st, 404, path)
            self.assertNotIn(b"root:", body)
        report = s["result"]["files"][0]["url"]
        self.assertEqual(self.req("GET", report)[0], 200)


class VisitorKeyTests(_Site):
    env = dict(ENV, GEMINI_API_KEY=SERVER_KEY)

    def test_own_key_is_used_for_one_check_and_never_shown_or_kept(self):
        st, run = self.start("own", key=" " + VISITOR_KEY + " ", key_provider="anthropic")
        self.assertEqual(st, 200, run)
        self.assertEqual(run["mode"], "own")
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        got = self.made[-1]
        self.assertEqual(got["provider"], "anthropic")
        self.assertEqual(got["env"].get("ANTHROPIC_API_KEY"), VISITOR_KEY)
        self.assertNotIn("GEMINI_API_KEY", got["env"], "the server key never goes into a visitor's check")
        self.assertTrue(Path(got["cache_dir"]).resolve().is_relative_to(self.ui.temp_root))
        text = self.everything_text([run["job_id"]])
        self.assertNotIn(VISITOR_KEY, text)
        self.assertNotIn(SERVER_KEY, text)
        self.assertNotIn(VISITOR_KEY, json.dumps(run))
        with llm_base._SECRETS_LOCK:
            self.assertNotIn(VISITOR_KEY, llm_base._SECRETS, "the key is forgotten when the check ends")
        # the visitor's key does not count against the server's limit
        self.assertEqual(self.ui.limits.used("model", "127.0.0.1"), 0)

    def test_bad_own_key_is_refused_without_echo(self):
        for body in ({"key": "", "key_provider": "gemini"}, {"key": "a b", "key_provider": "gemini"},
                     {"key": VISITOR_KEY, "key_provider": "ollama"}, {"key": VISITOR_KEY}):
            st, d = self.start("own", **body)
            self.assertEqual(st, 400, d)
            self.assertNotIn(VISITOR_KEY, json.dumps(d, ensure_ascii=False))
        self.assertEqual(self.made, [])

    def test_server_key_check_and_estimate(self):
        st, c = self.jget("/api/config")
        self.assertEqual((c["provider"], c["default_model"]), ("server", "server"))
        self.assertNotIn(SERVER_KEY, json.dumps(c))
        st, up = self.upload("doc.md", DOC.encode("utf-8"))
        st, est = self.jpost("/api/estimate", {"doc": up["upload_id"], "model": "server", "depth": "full"})
        self.assertEqual(st, 200, est)
        self.assertEqual(est["cost_text"], "$0 для вас")
        st, est = self.jpost("/api/estimate", {"doc": up["upload_id"], "model": "own", "depth": "fast",
                                               "key_provider": "openai"})
        self.assertEqual(st, 200, est)
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "model": "server", "depth": "full"})
        self.assertEqual((st, run["mode"], run["note"]), (200, "server", ""))
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        self.assertEqual(self.made[-1]["env"].get("GEMINI_API_KEY"), SERVER_KEY)
        self.assertEqual(self.made[-1]["provider"], "gemini")
        self.assertNotIn(SERVER_KEY, self.everything_text([run["job_id"]]), "the server key is shown nowhere")


class LimitTests(_Site):
    env = dict(ENV, GEMINI_API_KEY=SERVER_KEY, PUBLIC_RUNS_PER_IP="1", PUBLIC_DAILY_RUNS="2",
               PUBLIC_CHECKS_PER_IP="3", PUBLIC_TRUST_PROXY="first", PUBLIC_MAX_UPLOAD_MB="1")

    def test_per_ip_and_daily_limits_fall_back_to_rules(self):
        st, r1 = self.start("server", ip="1.1.1.1")
        self.assertEqual(r1["mode"], "server")
        self.wait_done(r1["job_id"])
        st, r2 = self.start("server", ip="1.1.1.1")
        self.assertEqual((st, r2["mode"], r2["note"]), (200, "offline", public.LIMIT_SPENT))
        self.wait_done(r2["job_id"])
        st, c = self.jget("/api/config", ip="1.1.1.1")
        self.assertEqual((c["provider"], c["default_model"]), ("offline", "offline"))
        self.assertIn("исчерпан", c["provider_note"])
        st, r3 = self.start("server", ip="2.2.2.2")
        self.assertEqual(r3["mode"], "server")
        self.wait_done(r3["job_id"])
        st, r4 = self.start("server", ip="3.3.3.3")  # the daily total (2) is spent for everybody
        self.assertEqual(r4["mode"], "offline")
        self.wait_done(r4["job_id"])
        self.assertEqual(len(self.made), 2)
        # any check counts against the address: the fourth from 1.1.1.1 is refused
        st, r5 = self.start("offline", ip="1.1.1.1")
        self.assertEqual(r5["mode"], "offline")
        self.wait_done(r5["job_id"])
        st, r6 = self.start("offline", ip="1.1.1.1")
        self.assertEqual(st, 429, r6)
        self.assertIn("предел", r6["error"])

    def test_upload_size_from_env(self):
        self.assertEqual(self.ui.max_doc_bytes, 1024 * 1024)
        self.assertEqual(self.jget("/api/config")[1]["max_doc_mb"], 1)
        st, d = self.upload("doc.md", b"# x\n" * 10)
        self.assertEqual(st, 200)

    def test_limits_roll_over_at_almaty_midnight(self):
        now = [1_800_000_000.0]
        lim = public.Limits(lambda: now[0])
        self.assertEqual(lim.take([("model", "a", 1)]), "")
        self.assertEqual(lim.take([("model", "a", 1)]), "model")
        now[0] += 24 * 3600
        self.assertEqual(lim.take([("model", "a", 1)]), "")
        self.assertEqual(lim.take([("check", "a", 5), ("model", "a", 1)]), "model")
        self.assertEqual(lim.used("check", "a"), 0, "a refused take takes nothing")


class _Blocking:
    def __init__(self) -> None:
        self.go = threading.Event()
        self.started = threading.Event()
        self.inner = make_script()

    def __call__(self, stage, system, user):
        self.started.set()
        self.go.wait(20)
        return self.inner(stage, system, user)


class BusyTests(_Site):
    env = dict(ENV, GEMINI_API_KEY=SERVER_KEY)

    def test_one_model_check_at_a_time(self):
        block = _Blocking()
        self.script = block
        st, r1 = self.start("server")
        self.assertEqual(st, 200, r1)
        self.assertTrue(block.started.wait(20))
        try:
            st, r2 = self.start("own", key=VISITOR_KEY, key_provider="gemini")
            self.assertEqual(st, 429, r2)
            self.assertEqual(r2["error"], public.BUSY_MODEL)
            st, r3 = self.start("offline")  # a rules-only check still runs next to it
            self.assertEqual(st, 200, r3)
            self.wait_done(r3["job_id"])
        finally:
            block.go.set()
        self.assertEqual(self.wait_done(r1["job_id"])["state"], "done")
        st, r4 = self.start("server")
        self.assertEqual(st, 200, r4)
        self.wait_done(r4["job_id"])


class AccessCodeTests(_Site):
    env = dict(ENV, GEMINI_API_KEY=SERVER_KEY, ACCESS_CODE="jury-2026")

    def test_server_key_needs_the_code(self):
        st, c = self.jget("/api/config")
        self.assertTrue(c["public"]["access_code"])
        self.assertNotIn("jury-2026", json.dumps(c))
        st, d = self.start("server")
        self.assertEqual(st, 403, d)
        st, d = self.start("server", access_code="wrong")
        self.assertEqual(st, 403, d)
        st, d = self.start("server", access_code="jury-2026")
        self.assertEqual((st, d["mode"]), (200, "server"))
        self.wait_done(d["job_id"])
        st, d = self.start("offline")  # rules only need no code
        self.assertEqual(st, 200, d)
        self.wait_done(d["job_id"])
        self.assertNotIn("ACCESS_CODE", self.made[0]["env"])


class RerunTests(_Site):
    def first_check(self) -> tuple[str, dict]:
        st, up = self.upload(FIXTURE.name, FIXTURE.read_bytes())
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "model": "offline", "depth": "full"})
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        return run["job_id"], s

    def test_questions_then_rerun_with_answers(self):
        job, first = self.first_check()
        st, d = self.jget(f"/api/details?job={job}")
        self.assertEqual(st, 200)
        self.assertTrue(d["questions"])
        q1 = d["questions"][0]
        typed = {q1["id"]: (q1["options"][0] if q1["options"] else "ответ автора")}
        st, run = self.jpost("/api/rerun", {"job": job, "answers": typed})
        self.assertEqual(st, 200, run)
        self.assertNotEqual(run["job_id"], job)
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        self.assertEqual(s["answers_name"], webui.ANSWERS_FROM_WINDOW)
        a = s["result"]["answers"]
        self.assertEqual(a["taken"], 1)
        self.assertEqual(a["previous"]["job_id"], job)
        st, d2 = self.jget(f"/api/details?job={run['job_id']}")
        self.assertEqual(d2["questions"][0]["answer"], typed[q1["id"]])
        qfile = next(f for f in s["result"]["files"] if f["kind"] == "questions")
        body = self.req("GET", qfile["url"])[2].decode("utf-8")
        self.assertIn("Перепроверить с ответами", body)
        self.assertNotIn(str(self.ui.temp_root), body)

    def test_rerun_errors(self):
        job, _s = self.first_check()
        self.assertEqual(self.jpost("/api/rerun", {"job": "", "answers": {"В-01": "x"}})[0], 404)
        self.assertEqual(self.jpost("/api/rerun", {"job": "0" * 32, "answers": {"В-01": "x"}})[0], 404)
        self.assertEqual(self.jpost("/api/rerun", {"job": job, "answers": {}})[0], 400)
        self.assertEqual(self.jpost("/api/rerun", {"job": job, "answers": ["x"]})[0], 400)
        # a check made with a visitor's key needs the key again: the site kept none
        self.ui.jobs[job].mode = "own"
        st, d = self.jpost("/api/rerun", {"job": job, "answers": {"В-01": "ответ"}})
        self.assertEqual(st, 400)
        self.assertIn("ещё раз", d["error"])


class TimeLimitTests(unittest.TestCase):
    def test_no_request_after_the_deadline(self):
        class B:
            def _raw(self, system, user, max_tokens, **kw):
                return "ok", None

            def can_answer(self):
                return True

        b = B()
        public.limit_time(b, time.monotonic() + 60)
        self.assertEqual(b._raw("s", "u", 10, effort="low")[0], "ok")
        self.assertTrue(b.can_answer())
        public.limit_time(b, time.monotonic() - 1)
        with self.assertRaises(LLMUnavailable):
            b._raw("s", "u", 10)
        self.assertFalse(b.can_answer())

    def test_settings_defaults_and_bad_values(self):
        s = public.Settings({})
        self.assertEqual((s.max_doc_mb, s.runs_per_ip, s.daily_runs, s.checks_per_ip, s.run_timeout, s.trust_proxy,
                          s.provider, s.access_code), (5, 3, 20, 20, 900, 0, "gemini", ""))
        s = public.Settings({"PUBLIC_MAX_UPLOAD_MB": "abc", "PUBLIC_RUNS_PER_IP": "-5", "PUBLIC_TRUST_PROXY": "first",
                             "PUBLIC_PROVIDER": "ollama"})
        self.assertEqual((s.max_doc_mb, s.runs_per_ip, s.trust_proxy, s.provider), (5, 0, -1, "gemini"))

    def test_public_flag_parses(self):
        args = cli.build_parser().parse_args(["--public", "--port", "0"])
        self.assertTrue(args.public)


class ProxyAddressTests(_Site):
    extra = {"PUBLIC_TRUST_PROXY": "1"}

    def test_rightmost_hop(self):
        self.ui.settings.trust_proxy = 1
        st, r = self.start("offline", ip="6.6.6.6, 7.7.7.7")
        self.wait_done(r["job_id"])
        self.assertEqual(self.ui.limits.used("check", "7.7.7.7"), 1)
        self.ui.settings.trust_proxy = 0
        st, r = self.start("offline", ip="8.8.8.8")
        self.wait_done(r["job_id"])
        self.assertEqual(self.ui.limits.used("check", "127.0.0.1"), 1)


class PageTests(unittest.TestCase):
    def test_public_parts_of_the_page(self):
        text = (AGENT / "revizor" / "webui_page.html").read_text(encoding="utf-8")
        for needle in ('id="pub-intro"', 'id="pub-example"', 'id="code-field"', "function applyPublic",
                       "Открыть готовый пример", "S.pubKey"):
            self.assertIn(needle, text)
        block = text.split("/* key:begin", 1)[1].split("/* key:end */", 1)[0]
        self.assertIn("function keyApplyPublic", block)
        for needle in ("localStorage", "sessionStorage", "cookie"):
            self.assertNotIn(needle, block)


if __name__ == "__main__":
    unittest.main()
