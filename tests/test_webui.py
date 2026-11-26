"""The browser window (python main.py --ui): a real server on 127.0.0.1 in a thread, stdlib HTTP client. No network
beyond 127.0.0.1, no real model: offline runs or the scripted FakeBackend of the pipeline tests."""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import quote

from revizor import __version__, cli, pipeline, webui
from revizor.llm import FakeBackend

from tests.test_pipeline import DOC, make_script

AGENT = Path(__file__).resolve().parents[1]
FIXTURE = AGENT / "tests" / "fixtures" / "p2p_transfer_tz.md"
ENV = {"REVIZOR_NO_DOTENV": "1", "PATH": os.environ.get("PATH", "")}
SECRET = "sk-" "ant-TEST-SECRET-123"  # split so that the secret scan before a commit stays clean
KEY_ENV = dict(ENV, ANTHROPIC_API_KEY=SECRET)
PAGE = "<!doctype html><title>test page</title><body data-state=\"idle\">окно</body>"


class _Server(unittest.TestCase):
    env = ENV
    backend = None
    server_kw: dict = {}

    def setUp(self) -> None:
        self._old = os.environ.get("REVIZOR_NO_DOTENV")
        os.environ["REVIZOR_NO_DOTENV"] = "1"
        self.tmp = Path(tempfile.mkdtemp(prefix="revizor-ui-test-"))
        self.page = self.tmp / "page.html"
        self.page.write_text(PAGE, encoding="utf-8")
        self.out = self.tmp / "out"
        self.logged: list[str] = []
        self.ui = webui.UIServer(self.env, port=0, out_dir=str(self.out), provider="auto", backend=self.backend,
                                 page_path=self.page, log=self.logged.append, **self.server_kw)
        self.port = self.ui.start()
        self.thread = threading.Thread(target=self.ui.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        root = self.ui.temp_root
        self.ui.close()
        self.thread.join(timeout=5)
        self.assertFalse(root.exists(), "the temporary folder with uploads must be removed on close()")
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._old is None:
            os.environ.pop("REVIZOR_NO_DOTENV", None)
        else:
            os.environ["REVIZOR_NO_DOTENV"] = self._old

    # client ------------------------------------------------------------------------------------------------
    def req(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None,
            host: str | None = None) -> tuple[int, dict, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def post(self, path: str, obj=None, *, raw: bytes | None = None, headers: dict | None = None):
        h = {"X-Revizor-UI": "1", "Content-Type": "application/json"}
        h.update(headers or {})
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        st, hd, b = self.req("POST", path, body, h)
        return st, hd, b

    def jpost(self, path: str, obj=None, **kw) -> tuple[int, dict]:
        st, _h, b = self.post(path, obj, **kw)
        return st, json.loads(b.decode("utf-8"))

    def jget(self, path: str) -> tuple[int, dict]:
        st, _h, b = self.req("GET", path)
        return st, json.loads(b.decode("utf-8"))

    def upload(self, name: str, data: bytes, kind: str = "doc") -> tuple[int, dict]:
        return self.jpost(f"/api/upload?kind={kind}&name={quote(name, safe='')}", raw=data,
                          headers={"Content-Type": "application/octet-stream"})

    def wait_done(self, job_id: str, timeout: float = 90) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st, data = self.jget(f"/api/status?job={job_id}")
            self.assertEqual(st, 200)
            if data["state"] != "running":
                return data
            time.sleep(0.05)
        self.fail("the check did not finish in time")


class GuardTests(_Server):
    def test_page_and_guards(self):
        st, h, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertEqual(body.decode("utf-8"), PAGE)
        self.assertEqual(h["cache-control"], "no-store")
        self.assertIn("connect-src 'self'", h["content-security-policy"])
        self.assertIn("default-src 'none'", h["content-security-policy"])
        self.assertEqual(self.req("GET", "/", host="localhost:%d" % self.port)[0], 200)
        # DNS rebinding: any other Host is refused
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}"):
            self.assertEqual(self.req("GET", "/api/config", host=host)[0], 403, host)
        # POST needs the header and, when present, our own Origin
        st, _h, _b = self.req("POST", "/api/run", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(st, 403)
        st, _h, _b = self.post("/api/run", {}, headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)
        st, _h, _b = self.post("/api/run", {}, headers={"Origin": f"http://127.0.0.1:{self.port + 1}"})
        self.assertEqual(st, 403)
        self.assertEqual(self.req("GET", "/nope")[0], 404)
        self.assertEqual(self.req("GET", "/api/nope")[0], 404)
        st, h, _b = self.req("OPTIONS", "/api/run", headers={"Origin": "http://evil.example",
                                                             "Access-Control-Request-Method": "POST"})
        self.assertEqual(st, 403)
        self.assertFalse([k for k in h if k.startswith("access-control-")])
        st, _d = self.jpost("/api/run", raw=b"x" * (webui.MAX_JSON_BYTES + 1))
        self.assertEqual(st, 413)

    def test_no_cors_headers_anywhere(self):
        for path in ("/", "/api/config", "/nope"):
            _st, h, _b = self.req("GET", path, headers={"Origin": "http://evil.example"})
            self.assertFalse([k for k in h if k.startswith("access-control-")], path)

    def test_config_offline_without_key(self):
        st, cfg = self.jget("/api/config")
        self.assertEqual(st, 200)
        self.assertEqual(cfg["version"], __version__)
        self.assertEqual(cfg["version"], "1.2.1")
        self.assertEqual(cfg["provider"], "offline")
        self.assertEqual([m["id"] for m in cfg["models"]], ["offline"])
        self.assertTrue(cfg["provider_note"])
        self.assertEqual(cfg["stages"], list(webui.STAGES))
        self.assertEqual(cfg["formats"], [".md", ".txt", ".pdf", ".docx", ".html"])
        self.assertIsNone(cfg["preloaded"])
        self.assertIsNone(cfg["job"])
        self.assertEqual(self.jget("/api/status")[0], 404)

    def test_request_log_is_method_path_status(self):
        self.req("GET", "/api/status?job=abc&since=3")
        self.assertIn("GET /api/status 404", self.logged)
        self.assertFalse([x for x in self.logged if "since" in x or "abc" in x])

    def test_successful_reads_stay_out_of_the_log(self):
        # the page asks for the status twice a second: a line per poll would bury the lines that matter
        self.req("GET", "/")
        self.req("GET", "/api/config")
        self.assertEqual(self.logged, [])
        self.req("POST", "/api/run", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(self.logged, ["POST /api/run 403"])


class UploadTests(_Server):
    server_kw = {"max_doc_bytes": 100, "max_answers_bytes": 50}

    def stored(self, data: dict) -> Path:
        path = self.ui.uploads[data["upload_id"]]["path"]
        self.assertTrue(path.resolve().is_relative_to(self.ui.temp_root))
        self.assertEqual(path.parent.parent.resolve(), self.ui.temp_root)
        self.assertTrue(path.is_file())
        return path

    def test_ordinary_names_unchanged(self):
        for name in ("requirements.md", "Требования к переводам (v2).docx", "spec_1-final.TXT"):
            st, d = self.upload(name, b"# x\n")
            self.assertEqual(st, 200, name)
            self.assertEqual(d["name"], name)
            self.assertEqual(self.stored(d).name, name)
            self.assertEqual(d["size"], 4)

    def test_hostile_names_stay_inside_temp_root(self):
        cases = {"../../etc/passwd.md": "passwd.md", "..\\..\\x.md": "x.md", "/abs/x.md": "x.md",
                 "a\x00b.md": "ab.md", "a\nb.md": "ab.md", ".hidden.md": "hidden.md", ".md": "document.md",
                 "<script>&.md": "script.md", "x" * 300 + ".md": "x" * 117 + ".md"}
        for raw, want in cases.items():
            st, d = self.upload(raw, b"text")
            self.assertEqual(st, 200, raw)
            self.assertEqual(d["name"], want, raw)
            self.assertEqual(self.stored(d).name, want)

    def test_rejections(self):
        self.assertEqual(self.upload("x.exe", b"MZ")[0], 400)
        self.assertEqual(self.upload("x.md.sh", b"x")[0], 400)
        self.assertEqual(self.upload("noext", b"x")[0], 400)
        self.assertEqual(self.upload("x.md", b"")[0], 400)
        self.assertEqual(self.upload("x.md", b"y" * 101)[0], 413)
        self.assertEqual(self.upload("x.md", b"y" * 100)[0], 200)
        self.assertEqual(self.upload("a.pdf", b"x", kind="answers")[0], 400)
        self.assertEqual(self.upload("a.md", b"y" * 51, kind="answers")[0], 413)
        self.assertEqual(self.upload("a.md", b"x", kind="other")[0], 400)
        st, d = self.upload("x.md", b"y" * 101)
        self.assertFalse(d["ok"])
        self.assertTrue(d["error"])
        # Content-Length is required
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", "/api/upload?kind=doc&name=x.md", skip_host=True)
            conn.putheader("Host", f"127.0.0.1:{self.port}")
            conn.putheader("X-Revizor-UI", "1")
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()

    def test_old_uploads_dropped(self):
        ids = [self.upload(f"d{i}.md", b"x")[1]["upload_id"] for i in range(webui.MAX_UPLOADS + 3)]
        self.assertEqual(len(self.ui.uploads), webui.MAX_UPLOADS)
        for uid in ids[:3]:
            self.assertNotIn(uid, self.ui.uploads)
            self.assertFalse((self.ui.temp_root / uid).exists())
        self.assertIn(ids[-1], self.ui.uploads)


class SafeNameTests(unittest.TestCase):
    def test_safe_name(self):
        f = webui.safe_name
        self.assertEqual(f("requirements.md", webui.DOC_FORMATS), "requirements.md")
        self.assertEqual(f("C:\\Users\\a\\ТЗ на кэшбэк.pdf", webui.DOC_FORMATS), "ТЗ на кэшбэк.pdf")
        with self.assertRaises(webui.ApiError) as cm:
            f("..", webui.DOC_FORMATS)
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(webui.ApiError):
            f("x.docx", webui.ANSWER_FORMATS)


class RunValidationTests(_Server):
    def test_bad_requests(self):
        st, d = self.upload("doc.md", DOC.encode("utf-8"))
        uid = d["upload_id"]
        base = {"doc": uid, "answers": None, "model": "offline", "depth": "full", "max_cost": ""}
        self.assertEqual(self.jpost("/api/run", dict(base, doc="0" * 16))[0], 404)
        self.assertEqual(self.jpost("/api/run", dict(base, doc=None))[0], 400)
        self.assertEqual(self.jpost("/api/run", dict(base, answers="f" * 16))[0], 404)
        self.assertEqual(self.jpost("/api/run", dict(base, answers=uid))[0], 404)  # a document is not an answers file
        self.assertEqual(self.jpost("/api/run", dict(base, model="claude-opus-5-5"))[0], 400)  # not offered offline
        self.assertEqual(self.jpost("/api/run", dict(base, model="claude-fable-5-2"))[0], 400)
        self.assertEqual(self.jpost("/api/run", dict(base, depth="deep"))[0], 400)
        for cap in ("-1", "abc", "0", "nan", "inf", True, [1]):
            self.assertEqual(self.jpost("/api/run", dict(base, max_cost=cap))[0], 400, cap)
        self.assertEqual(self.jpost("/api/run", raw=b"[1, 2]")[0], 400)
        self.assertEqual(self.jpost("/api/run", raw=b"{not json")[0], 400)
        self.assertEqual(self.jpost("/api/estimate", dict(base, model="gpt-4o"))[0], 400)
        self.assertEqual(self.jpost("/api/estimate", dict(base, doc="0" * 16))[0], 404)
        self.assertEqual(self.jget("/api/status?job=nope")[0], 404)
        self.assertEqual(self.jget("/api/status?since=x")[0], 400)
        self.assertIsNone(self.ui.running)


class OfflineRunTests(_Server):
    def test_full_offline_check(self):
        data = FIXTURE.read_bytes()
        st, up = self.upload(FIXTURE.name, data)
        self.assertEqual((st, up["name"]), (200, FIXTURE.name))
        st, est = self.jpost("/api/estimate", {"doc": up["upload_id"], "model": "offline", "depth": "full"})
        self.assertEqual(st, 200)
        self.assertEqual((est["calls"], est["time_text"], est["cost_text"], est["suggested_cap"]),
                         (0, "несколько секунд", "$0", ""))
        self.assertTrue(est["readable"])
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "answers": None, "model": "offline",
                                          "depth": "full", "max_cost": ""})
        self.assertEqual(st, 200)
        job = run["job_id"]
        s = self.wait_done(job)
        self.assertEqual(s["state"], "done", s["error"])
        self.assertEqual(s["provider"], "offline")
        states = [x["state"] for x in s["stages"]]
        self.assertEqual(len(states), 9)
        # without a model the pipeline itself prints «пропущено» for stages 4, 5 and 7
        self.assertEqual(states, ["done", "done", "done", "skipped", "skipped", "done", "skipped", "done", "done"])
        self.assertTrue(all(x["detail"] for x in s["stages"]))
        self.assertEqual(s["calls"], {"model": 0, "cache": 0})
        res = s["result"]

        # the same file through the terminal: the verdict line is the same
        lines: list[str] = []
        cli_out = self.tmp / "cli" / "r.md"
        code = cli.main(["--provider", "offline", "-i", str(FIXTURE), "-o", str(cli_out)], env=dict(ENV),
                        out=lines.append)
        self.assertEqual(code, 0)
        itog = next(x for x in lines if x.startswith("Итог: "))
        self.assertEqual(itog, "Итог: " + res["verdict_text"])
        self.assertEqual(res["verdict_level"], webui.VERDICT_LEVEL[res["verdict"]])
        self.assertEqual(res["cost_text"], "$0 (модель не вызывалась)")
        self.assertTrue(res["ledger"].startswith("Расход: модель не вызывалась"))
        self.assertIn(f"Требований: {res['requirements']} · Замечаний: {res['findings']['total']}",
                      next(x for x in lines if x.startswith("Требований: ")))
        # the log is the terminal output of the same check
        self.assertEqual([x[:5] for x in s["log"] if x.startswith("[")], [f"[{k}/9]" for k in range(1, 10)])
        st, tail = self.jget(f"/api/status?job={job}&since={s['log_next'] - 1}")
        self.assertEqual(len(tail["log"]), 1)

        kinds = {f["kind"] for f in res["files"]}
        self.assertTrue({"report_md", "report_html", "json", "questions", "jira"} <= kinds, kinds)
        md = next(f for f in res["files"] if f["kind"] == "report_md")
        self.assertEqual(md["name"], "p2p_transfer_tz.md")
        for f in res["files"]:
            path = self.out / f["name"]
            self.assertTrue(path.is_file(), f["name"])
            self.assertEqual(f["size"], path.stat().st_size)
            st, h, body = self.req("GET", f["url"])
            self.assertEqual(st, 200, f["url"])
            self.assertEqual(body, path.read_bytes())
            self.assertEqual(h["content-security-policy"], "sandbox allow-scripts allow-popups")
            self.assertEqual(h["x-content-type-options"], "nosniff")
            self.assertNotIn("content-disposition", h)
        # no written file names the temporary folder of the upload; the questionnaire explains the next step for the window
        root = self.ui.temp_root.name.encode("utf-8")
        for f in res["files"]:
            self.assertNotIn(root, (self.out / f["name"]).read_bytes(), f["name"])
        q = (self.out / next(f["name"] for f in res["files"] if f["kind"] == "questions")).read_text(encoding="utf-8")
        last = q.rstrip("\n").rsplit("\n", 1)[-1]
        self.assertTrue(last.startswith("Когда ответите: в окне Ревизора"), last)
        self.assertIn("--answers " + str(self.out / "p2p_transfer_tz.questions.md"), last)
        self.assertIn("--output " + str(self.out / "p2p_transfer_tz_answered.md"), last)
        self.assertNotIn("Когда ответите, запустите", q)
        self.assertIn("Когда ответите, запустите", (cli_out.parent / "r.questions.md").read_text(encoding="utf-8"))
        html = next(f for f in res["files"] if f["kind"] == "report_html")
        self.assertTrue(self.req("GET", html["url"])[1]["content-type"].startswith("text/html"))
        self.assertTrue(self.req("GET", md["url"])[1]["content-type"].startswith("text/plain"))
        st, h, _b = self.req("GET", md["url"] + "?download=1")
        self.assertEqual(st, 200)
        self.assertEqual(h["content-disposition"],
                         "attachment; filename=\"p2p_transfer_tz.md\"; filename*=UTF-8''p2p_transfer_tz.md")
        for bad in (f"/files/{job}/../../main.py", f"/files/{job}/%2e%2e%2fmain.py", f"/files/{job}/main.py",
                    f"/files/{job}/..%2f..%2fmain.py", f"/files/{job}/", f"/files/{'0' * 16}/{md['name']}",
                    f"/files/{job}/{md['name']}/x", f"/files/{job}/%2fetc%2fpasswd"):
            self.assertEqual(self.req("GET", bad)[0], 404, bad)
        # the latest check comes back with the configuration (the page restores itself after a reload)
        st, cfg = self.jget("/api/config")
        self.assertEqual(cfg["job"]["job_id"], job)
        self.assertEqual(len(cfg["job"]["log"]), s["log_next"])

    def test_answers_go_to_a_separate_report(self):
        st, up = self.upload("doc.md", DOC.encode("utf-8"))
        st, ans = self.upload("doc.questions.md", "Ответов пока нет.\n".encode("utf-8"), kind="answers")
        self.assertEqual(st, 200)
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "answers": ans["upload_id"], "model": "offline",
                                          "depth": "fast", "max_cost": ""})
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        self.assertEqual(s["answers_name"], "doc.questions.md")
        names = [f["name"] for f in s["result"]["files"]]
        self.assertIn("doc_answered.md", names)
        self.assertNotIn("doc.md", names)

    def test_unreadable_document_is_done_with_diagnostic(self):
        st, up = self.upload("empty.txt", b"   \n\n")
        self.assertEqual(st, 200)
        st, est = self.jpost("/api/estimate", {"doc": up["upload_id"], "model": "offline", "depth": "full"})
        self.assertFalse(est["readable"])
        self.assertTrue(est["lines"][0].startswith("Оценка невозможна"))
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "model": "offline", "depth": "full"})
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        self.assertEqual(s["result"]["verdict_level"], "unknown")
        self.assertIsNotNone(s["result"]["diagnostic"])
        self.assertEqual(s["result"]["findings"]["total"], 0)
        self.assertEqual(s["stages"][0]["state"], "done")
        self.assertEqual({x["state"] for x in s["stages"][1:]}, {"skipped"})
        root = self.ui.temp_root.name.encode("utf-8")  # the JSON of a document that was not checked keeps its path
        for f in s["result"]["files"]:
            self.assertNotIn(root, (self.out / f["name"]).read_bytes(), f["name"])
        data = json.loads((self.out / "empty.json").read_text(encoding="utf-8"))
        self.assertIn("empty.txt", json.dumps(data, ensure_ascii=False))


class StageLabelTests(unittest.TestCase):
    def test_offline_run_prints_exactly_nine_numbered_stages(self):
        self.assertEqual(len(webui.STAGES), pipeline.STEPS)
        with tempfile.TemporaryDirectory() as d:
            lines: list[str] = []
            code = cli.main(["--provider", "offline", "-i", str(FIXTURE), "-o", str(Path(d) / "r.md")],
                            env=dict(ENV), out=lines.append)
        self.assertEqual(code, 0)
        numbered = [x.split("]", 1)[0] + "]" for x in lines if x.startswith("[")]
        self.assertEqual(numbered, [f"[{k}/{len(webui.STAGES)}]" for k in range(1, len(webui.STAGES) + 1)])


class LineParserTests(unittest.TestCase):
    def job(self, provider="anthropic"):
        return webui.Job("j", doc_name="d.md", answers_name="", provider=provider, model="m", depth="full",
                         max_cost="", out_dir="o", report_stem="d")

    def test_steps_counters_and_stages(self):
        j = self.job()
        st = lambda: [x["state"] for x in j.status()["stages"]]  # noqa: E731
        self.assertEqual(st()[:2], ["running", "pending"])
        j.feed("Режим: anthropic / m, глубина full")
        j.feed("[1/9] Читаю документ… 10 строк, 4 блока")
        self.assertEqual(st()[:3], ["done", "running", "pending"])
        self.assertEqual(j.status()["stages"][0]["detail"], "10 строк, 4 блока")
        j.feed("  ✓ нарезка 1/1 · 4 с · $0.01")
        j.feed("[2/9] Нарезаю документ на требования (модель)… найдено 3, не требования: 1")
        j.feed("[3/9] Проверяю правилами… 2 замечания")
        self.assertEqual(st()[3:5], ["running", "pending"])
        j.feed("      Отправляю модели 5 запросов (одновременно до 4)…")
        self.assertEqual(st()[3:5], ["running", "running"])
        j.feed("  ✓ проверка требований 1/3 · 3 с · $0.02 · из кэша агента")
        j.feed("  ✗ проверка набора 1/4 · 30 с · $0.05")
        j.feed("  ✓ проверка требований 2/3 · 0 с · $0.06")
        s = j.status()
        self.assertEqual(s["calls"], {"model": 3, "cache": 1})
        self.assertEqual(s["step_text"], "проверка требований 2/3")
        self.assertEqual(s["spent_text"], "$0.06")
        j.feed("  … жду ответа модели: проверка набора, в работе запросов: 2, самый долгий — 20 с")
        self.assertEqual(j.status()["step_text"], "жду ответа модели: проверка набора, в работе запросов: 2, самый долгий — 20 с")
        j.feed("  ✓ тесты 1/1 · 2 с")  # unknown price: no money part
        self.assertEqual(j.status()["spent_text"], "$0.06")
        self.assertEqual(j.status()["calls"]["model"], 4)
        j.feed("[4/9] Проверяю каждое требование моделью (1 пакетов × 3 линзы)… подтверждено 2")
        j.feed("[5/9] Проверяю набор требований (4 шага)… подтверждено 1")
        j.feed("[6/9] Сверяю цитаты и объединяю повторы… подтверждено цитат 3, отброшено 0")
        j.feed("[7/9] Перепроверка замечаний… пропущено: быстрый режим")
        self.assertEqual(st()[:8], ["done"] * 6 + ["skipped", "running"])
        self.assertEqual(j.status()["stages"][6]["detail"], "пропущено: быстрый режим")
        j.feed("[8/9] Составляю вопросы автору (модель)… 3")
        j.feed("[9/9] Считаю индексы и вердикт… блокеры")
        self.assertEqual(st(), ["done"] * 6 + ["skipped"] + ["done"] * 2)
        self.assertEqual(j.status(since=2)["log"][0], "  ✓ нарезка 1/1 · 4 с · $0.01")
        j.finish("done", result={"x": 1})
        self.assertEqual(j.status()["state"], "done")


class ArgvTests(unittest.TestCase):
    def test_cap_and_flags_reach_execute(self):
        argv = webui.build_argv(doc_path="/t/d.md", out_path="reports/ui/d.md", depth="fast", model="claude-opus-5-5",
                                provider_arg="anthropic", answers_path="/t/a.md", max_cost="0.15", no_cache=True)
        self.assertEqual(argv, ["--input", "/t/d.md", "--output", "reports/ui/d.md", "--depth", "fast",
                                "--model", "claude-opus-5-5", "--provider", "anthropic", "--answers", "/t/a.md",
                                "--max-cost", "0.15", "--no-cache"])
        args = cli.build_parser().parse_args(argv)
        self.assertEqual((args.max_cost, args.depth, args.model, args.no_cache), ("0.15", "fast", "claude-opus-5-5", True))
        self.assertEqual(cli._cap_of(args, ENV), 0.15)
        off = webui.build_argv(doc_path="d.md", out_path="o.md", depth="full", model="offline", provider_arg="anthropic")
        self.assertEqual(off, ["--input", "d.md", "--output", "o.md", "--depth", "full", "--provider", "offline"])
        auto = webui.build_argv(doc_path="d.md", out_path="o.md", depth="full", model="m1")
        self.assertNotIn("--provider", auto)


class _Blocking:
    """A scripted model that waits for an event at its first call (holds a check «running»)."""

    def __init__(self) -> None:
        self.go = threading.Event()
        self.started = threading.Event()
        self.inner = make_script()

    def __call__(self, stage, system, user):
        self.started.set()
        self.go.wait(20)
        return self.inner(stage, system, user)


class FakeModelRunTests(_Server):
    env = KEY_ENV

    def setUp(self) -> None:
        self.script = _Blocking()
        self.backend = FakeBackend(self.script)
        super().setUp()

    def test_second_check_conflicts_and_counters(self):
        st, cfg = self.jget("/api/config")
        self.assertEqual(cfg["provider"], "anthropic")
        ids = [m["id"] for m in cfg["models"]]
        self.assertEqual(ids[0], "claude-haiku-5-5")
        self.assertIn("claude-opus-5-5", ids)
        self.assertNotIn("claude-fable-5-2", ids)
        self.assertNotIn("gpt-4o-mini", ids)
        self.assertEqual(ids[-1], "offline")
        self.assertEqual(cfg["default_model"], "claude-haiku-5-5")
        st, up = self.upload("doc.md", DOC.encode("utf-8"))
        body = {"doc": up["upload_id"], "answers": None, "model": "claude-haiku-5-5", "depth": "full",
                "max_cost": "5"}
        st, run = self.jpost("/api/run", body)
        self.assertEqual(st, 200)
        self.assertTrue(self.script.started.wait(20))
        st, again = self.jpost("/api/run", dict(body, model="offline"))
        self.assertEqual(st, 409)
        self.assertFalse(again["ok"])
        st, mid = self.jget("/api/status")
        self.assertEqual(mid["state"], "running")
        self.assertEqual(mid["stages"][0]["state"], "done")
        self.assertEqual(mid["stages"][1]["state"], "running")
        self.script.go.set()
        s = self.wait_done(run["job_id"])
        self.assertEqual(s["state"], "done", s["error"])
        self.assertEqual([x["state"] for x in s["stages"]], ["done"] * 9)
        self.assertEqual(s["max_cost"], "5")
        self.assertIn("потолок стоимости $5", s["log"][0])  # the cap reached execute
        self.assertGreater(s["calls"]["model"], 0)
        self.assertEqual(s["calls"]["cache"], 0)
        self.assertTrue(s["step_text"])
        # after the first check a second one may start
        st, run2 = self.jpost("/api/run", dict(body, model="offline"))
        self.assertEqual(st, 200)
        self.assertEqual(self.wait_done(run2["job_id"])["state"], "done")


class SecretAndEstimateTests(_Server):
    env = KEY_ENV

    def test_no_secret_in_answers_or_log_and_estimate_matches_cli(self):
        bodies = []
        st, _h, b = self.req("GET", "/")
        bodies.append(b)
        st, up = self.upload(FIXTURE.name, FIXTURE.read_bytes())
        for model in ("claude-opus-5-5", "claude-haiku-5-5"):
            st, _h, b = self.post("/api/estimate", {"doc": up["upload_id"], "model": model, "depth": "full"})
            self.assertEqual(st, 200)
            bodies.append(b)
            est = json.loads(b.decode("utf-8"))
            lines: list[str] = []
            code = cli.main(["--estimate", "--provider", "anthropic", "--model", model, "-i", str(FIXTURE),
                             "-o", str(self.tmp / "e.md")], env=dict(KEY_ENV), out=lines.append)
            self.assertEqual(code, 0)
            self.assertEqual(est["lines"], lines)
            time_line = next(x for x in lines if x.startswith("  Время: "))
            cost_line = next(x for x in lines if x.startswith("  Стоимость: "))
            self.assertTrue(time_line.startswith("  Время: " + est["time_text"] + " "), est["time_text"])
            self.assertTrue(cost_line.startswith("  Стоимость: " + est["cost_text"] + " "), est["cost_text"])
            self.assertTrue(est["time_text"].endswith("мин"))
            self.assertTrue(est["cost_text"].startswith("≈ $"))
            self.assertEqual(lines[-1], "  Чтобы ограничить расход: --max-cost " + est["suggested_cap"])
            self.assertEqual(est["calls"], int(next(x for x in lines if x.startswith("  Вызовов: ")).split()[1]))
            self.assertTrue(est["note"])
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "model": "offline", "depth": "fast"})
        self.wait_done(run["job_id"])
        for path in ("/api/config", "/api/status", f"/api/status?job={run['job_id']}"):
            st, _h, b = self.req("GET", path)
            self.assertEqual(st, 200)
            bodies.append(b)
        for b in bodies:
            self.assertNotIn(b"TEST-SECRET", b)
        self.assertTrue(self.logged)
        self.assertFalse([x for x in self.logged if "TEST-SECRET" in x])


class PreloadTests(_Server):
    server_kw = {"preload": str(FIXTURE)}

    def test_preloaded_document(self):
        st, cfg = self.jget("/api/config")
        pre = cfg["preloaded"]
        self.assertEqual((pre["name"], pre["size"]), (FIXTURE.name, FIXTURE.stat().st_size))
        st, est = self.jpost("/api/estimate", {"doc": pre["upload_id"], "model": "offline", "depth": "full"})
        self.assertEqual(st, 200)
        self.assertTrue(est["readable"])
        for i in range(webui.MAX_UPLOADS + 1):
            self.upload(f"d{i}.md", b"x")
        self.assertIn(pre["upload_id"], self.ui.uploads)  # the document from the command line is never dropped


class RealPageTests(unittest.TestCase):
    """The page that ships: the demo video is filmed by a script that finds elements by these ids."""
    IDS = ("doc-drop", "doc-input", "answers-input", "model-select", "depth-select", "cap-input", "estimate",
           "run-button", "progress", "log", "result", "verdict", "open-report", "open-questions", "open-jira",
           "new-check", "theme-toggle") + tuple(f"stage-{n}" for n in range(1, 10))

    def setUp(self) -> None:
        self.text = (AGENT / "revizor" / "webui_page.html").read_text(encoding="utf-8")

    def test_stable_ids_and_state(self):
        for name in self.IDS:
            static = self.text.count(f'id="{name}"')
            if name.startswith("stage-"):  # the nine rows are built from the server's list of stages
                self.assertTrue(static == 1 or '"stage-"+' in self.text or "'stage-'+" in self.text, name)
            else:
                self.assertEqual(static, 1, name)
        self.assertIn('data-state="idle"', self.text)
        for state in ("idle", "ready", "running", "done", "failed"):
            self.assertIn(f'"{state}"', self.text, state)

    def test_page_is_self_contained(self):
        low = self.text.lower()
        for needle in ("http://", "https://", "//cdn", "<link ", "@import", "src=\"", "eval(", "document.write"):
            self.assertNotIn(needle, low.replace("http://www.w3.org/2000/svg", ""), needle)
        self.assertIn("X-Revizor-UI", self.text)

    def test_default_server_serves_this_page(self):
        ui = webui.UIServer(dict(ENV), port=0, out_dir=str(Path(tempfile.gettempdir()) / "revizor-ui-unused"))
        try:
            self.assertEqual(Path(ui.page_path).resolve(), (AGENT / "revizor" / "webui_page.html").resolve())
        finally:
            ui.close()


class CommandLineTests(unittest.TestCase):
    def test_busy_port_exit_1(self):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            lines: list[str] = []
            code = cli.main(["--ui", "--no-browser", "--port", str(port)], env=dict(ENV), out=lines.append)
        finally:
            s.close()
        self.assertEqual(code, 1)
        self.assertIn("занят", lines[-1])
        self.assertIn("--port", lines[-1])

    def test_help_mentions_window_flags(self):
        text = cli.build_parser().format_help()
        for flag in ("--ui", "--port", "--no-browser"):
            self.assertIn(flag, text)
        self.assertIn("браузер", text)


if __name__ == "__main__":
    unittest.main()
