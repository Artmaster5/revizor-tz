"""«Вставить свой ключ» in the window: the key lives in the memory of the window process only. The provider check is a
callable of the app that every test replaces: nothing here touches the network (beyond 127.0.0.1)."""
from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from revizor import webui
from revizor.llm import FakeBackend
from revizor.llm.base import LLMUnavailable
from revizor.llm._http import HttpResult

from tests.test_pipeline import DOC, make_script
from tests.test_webui import AGENT, ENV, KEY_ENV, _Blocking, _Server

KEY = "fake-key-for-tests-0001"
OK = lambda provider, key, env: ("ok", "")  # noqa: E731 - the provider accepts the key


def _all_text_under(folder: Path) -> str:
    out = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            out.append(p.read_bytes().decode("utf-8", "replace"))
    return "\n".join(out)


class _KeyServer(_Server):
    env = ENV

    def setUp(self) -> None:
        self.backend = FakeBackend(make_script())
        super().setUp()
        self.ui.key_checker = OK
        self.calls: list[tuple[str, str]] = []

    def apply(self, provider: str = "gemini", key: str = KEY):
        return self.jpost("/api/key", {"provider": provider, "key": key})

    def run_check(self, model: str | None = None) -> dict:
        st, up = self.upload("doc.md", DOC.encode("utf-8"))
        _s, cfg = self.jget("/api/config")
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "model": model or cfg["default_model"],
                                          "depth": "full", "max_cost": ""})
        self.assertEqual(st, 200, run)
        return self.wait_done(run["job_id"])


class ApplyAndConfigTests(_KeyServer):
    def test_apply_shows_in_config_and_the_key_is_in_no_answer(self):
        _s, before = self.jget("/api/config")
        self.assertEqual(before["user_key"], {"set": False, "provider": "", "label": ""})
        self.assertEqual(before["provider"], "offline")
        self.assertEqual([p["id"] for p in before["key_providers"]], ["anthropic", "gemini", "openai"])
        bodies = []
        st, h, b = self.post("/api/key", {"provider": "gemini", "key": KEY})
        self.assertEqual(st, 200)
        bodies.append(b)
        ans = json.loads(b.decode("utf-8"))
        self.assertEqual(ans, {"ok": True, "provider": "gemini", "label": "Google Gemini", "note": ""})
        _s, cfg = self.jget("/api/config")
        self.assertEqual(cfg["user_key"], {"set": True, "provider": "gemini", "label": "Google Gemini"})
        self.assertEqual(cfg["provider"], "gemini")
        self.assertEqual(cfg["provider_note"], "")
        self.assertTrue(cfg["default_model"].startswith("gemini"))
        self.assertNotIn("offline", [m["id"] for m in cfg["models"]][:1])
        st, up = self.upload("doc.md", DOC.encode("utf-8"))
        st, _h, b = self.post("/api/estimate", {"doc": up["upload_id"], "model": cfg["default_model"], "depth": "full"})
        self.assertEqual(st, 200)
        bodies.append(b)
        job = self.run_check()
        self.assertEqual(job["state"], "done", job["error"])
        for path in ("/api/config", "/api/status", f"/api/status?job={job['job_id']}", f"/api/details?job={job['job_id']}",
                     "/"):
            st, _h, b = self.req("GET", path)
            bodies.append(b)
        for b in bodies:
            self.assertNotIn(KEY.encode(), b)
        self.assertEqual(set(ans), {"ok", "provider", "label", "note"})  # no length, no part of the key

    def test_apply_other_providers_and_unknown_provider(self):
        for prov, label in (("anthropic", "Anthropic (Claude)"), ("openai", "OpenAI-совместимый")):
            st, ans = self.apply(prov)
            self.assertEqual((st, ans["label"]), (200, label))
            _s, cfg = self.jget("/api/config")
            self.assertEqual(cfg["provider"], prov)
        st, ans = self.apply("ollama")
        self.assertEqual(st, 400)
        self.assertNotIn(KEY, json.dumps(ans, ensure_ascii=False))
        st, ans = self.jpost("/api/key", {"provider": ["x"], "key": KEY})
        self.assertEqual(st, 400)

    def test_argv_of_a_check_has_no_key(self):
        self.apply()
        seen: list[list[str]] = []
        real = webui.build_argv

        def spy(**kw):
            argv = real(**kw)
            seen.append(list(argv))
            return argv

        with mock.patch.object(webui, "build_argv", spy):
            job = self.run_check()
        self.assertEqual(job["state"], "done", job["error"])
        self.assertTrue(seen)
        for argv in seen:
            self.assertFalse([a for a in argv if KEY in a], argv)
            self.assertEqual(argv[argv.index("--provider") + 1], "gemini")

    def test_key_reaches_no_file_log_or_console_line(self):
        self.apply()
        job = self.run_check()
        self.assertEqual(job["state"], "done", job["error"])
        self.assertGreater(job["calls"]["model"], 0)  # the check really went through the model path
        text = _all_text_under(self.out)
        self.assertTrue(text)
        self.assertNotIn(KEY, text)
        self.assertNotIn(KEY, "\n".join(self.ui.jobs[job["job_id"]].log))
        self.assertNotIn(KEY, "\n".join(job["log"]))
        self.assertFalse([x for x in self.logged if KEY in x])
        # nothing of the window wrote the key under its own temporary folder either
        self.assertNotIn(KEY, _all_text_under(self.ui.temp_root))

    def test_check_uses_the_key_in_its_env_but_the_original_env_stays_clean(self):
        self.apply()
        self.assertEqual(self.ui.env["GEMINI_API_KEY"], KEY)
        self.assertNotIn("GEMINI_API_KEY", self.ui._base_env)
        self.assertNotIn("GEMINI_API_KEY", ENV)


class RejectedAndUnreachableTests(_KeyServer):
    def test_provider_says_401(self):
        self.ui.key_checker = lambda p, k, e: ("rejected", "Incorrect key: " + k)
        st, ans = self.apply()
        self.assertEqual(st, 400)
        self.assertTrue(ans["error"].startswith("Ключ не принят провайдером"), ans)
        self.assertNotIn(KEY, ans["error"])
        self.assertLessEqual(len(ans["error"]), 260)
        _s, cfg = self.jget("/api/config")
        self.assertFalse(cfg["user_key"]["set"])
        self.assertEqual(cfg["provider"], "offline")

    def test_bad_shape_is_refused_without_echo(self):
        bad = ["", "   ", "has space inside", "tab\tinside", "line\nbreak", "x" * 600, "your_api_key_here", "<ваш ключ>",
               "ключ-кириллицей-0001", None, 12345]
        for value in bad:
            with self.subTest(value=str(value)[:12]):
                st, ans = self.apply(key=value)
                self.assertEqual(st, 400)
                self.assertTrue(ans["error"].startswith("Ключ не принят: "), ans)
                if isinstance(value, str) and value.strip():
                    self.assertNotIn(value.strip(), ans["error"])
        st, ans = self.apply(key="has space inside")
        self.assertNotIn("has", ans["error"].replace("есть", ""))
        _s, cfg = self.jget("/api/config")
        self.assertFalse(cfg["user_key"]["set"])

    def test_provider_does_not_answer(self):
        self.ui.key_checker = lambda p, k, e: ("unknown", "")
        st, ans = self.apply()
        self.assertEqual(st, 200)
        self.assertTrue(ans["note"].startswith("Проверить ключ сейчас не удалось"), ans)
        self.assertNotIn(KEY, json.dumps(ans, ensure_ascii=False))
        _s, cfg = self.jget("/api/config")
        self.assertTrue(cfg["user_key"]["set"])

    def test_checker_gets_the_key_but_not_through_the_address(self):
        got = []
        self.ui.key_checker = lambda p, k, e: (got.append((p, k, dict(e))) or ("ok", ""))
        self.apply("openai")
        self.assertEqual(got[0][:2], ("openai", KEY))
        self.assertNotIn("OPENAI_API_KEY", got[0][2])  # the checker gets the original env plus the key as an argument


class ForgetTests(_KeyServer):
    env = ENV

    def test_forget_returns_to_rules_when_there_was_no_key(self):
        self.apply()
        st, ans = self.jpost("/api/key/forget", {})
        self.assertEqual((st, ans), (200, {"ok": True}))
        _s, cfg = self.jget("/api/config")
        self.assertEqual(cfg["user_key"], {"set": False, "provider": "", "label": ""})
        self.assertEqual(cfg["provider"], "offline")
        self.assertTrue(cfg["provider_note"])
        self.assertEqual([m["id"] for m in cfg["models"]], ["offline"])
        self.assertNotIn("GEMINI_API_KEY", self.ui.env)


class ForgetWithOriginalKeyTests(_KeyServer):
    env = KEY_ENV

    def test_forget_returns_to_the_original_provider(self):
        _s, orig = self.jget("/api/config")
        self.assertEqual(orig["provider"], "anthropic")
        self.apply("gemini")
        self.assertEqual(self.jget("/api/config")[1]["provider"], "gemini")
        self.jpost("/api/key/forget", {})
        _s, back = self.jget("/api/config")
        self.assertEqual(back["provider"], "anthropic")
        self.assertEqual(back["default_model"], orig["default_model"])
        self.assertEqual(back["models"], orig["models"])


class RunningTests(_KeyServer):
    def setUp(self) -> None:
        self.script = _Blocking()
        super().setUp()
        self.ui.backend = FakeBackend(self.script)

    def test_apply_and_forget_wait_for_the_end_of_a_check(self):
        self.apply()
        st, up = self.upload("doc.md", DOC.encode("utf-8"))
        _s, cfg = self.jget("/api/config")
        st, run = self.jpost("/api/run", {"doc": up["upload_id"], "model": cfg["default_model"], "depth": "full"})
        self.assertEqual(st, 200)
        self.assertTrue(self.script.started.wait(20))
        st, ans = self.apply("anthropic", KEY + "-b")
        self.assertEqual((st, ans["error"]), (409, "Проверка уже идёт: дождитесь её окончания."))
        st, ans = self.jpost("/api/key/forget", {})
        self.assertEqual((st, ans["error"]), (409, "Проверка уже идёт: дождитесь её окончания."))
        _s, cfg2 = self.jget("/api/config")
        self.assertEqual(cfg2["user_key"]["provider"], "gemini")
        self.script.go.set()
        self.assertEqual(self.wait_done(run["job_id"])["state"], "done")
        st, _ans = self.jpost("/api/key/forget", {})
        self.assertEqual(st, 200)


class NotInTheAddressTests(_KeyServer):
    def test_query_string_and_get_are_refused(self):
        st, ans = self.jpost(f"/api/key?key={KEY}&provider=gemini", {"provider": "gemini", "key": KEY})
        self.assertEqual(st, 400)
        self.assertNotIn(KEY, json.dumps(ans, ensure_ascii=False))
        st, _h, b = self.req("GET", f"/api/key?key={KEY}&provider=gemini")
        self.assertEqual(st, 404)
        self.assertNotIn(KEY.encode(), b)
        st, _h, b = self.req("GET", f"/api/config?key={KEY}&provider=gemini")
        self.assertEqual(st, 200)
        self.assertFalse(json.loads(b.decode("utf-8"))["user_key"]["set"])
        st, _h, _b = self.req("GET", "/api/key/forget")
        self.assertEqual(st, 404)
        self.assertFalse(self.ui._user_key)
        self.assertFalse([x for x in self.logged if KEY in x])

    def test_same_guards_as_other_posts(self):
        body = json.dumps({"provider": "gemini", "key": KEY}).encode()
        st, _h, _b = self.req("POST", "/api/key", body, {"Content-Type": "application/json"})
        self.assertEqual(st, 403)
        st, _h, _b = self.post("/api/key", {"provider": "gemini", "key": KEY}, headers={"Origin": "http://evil.example"})
        self.assertEqual(st, 403)
        st, _h, _b = self.req("POST", "/api/key/forget", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(st, 403)
        self.assertEqual(self.req("POST", "/api/key", body, {"X-Revizor-UI": "1"}, host="evil.example")[0], 403)
        self.assertFalse(self.ui._user_key)


class ErrorTextTests(_KeyServer):
    def test_error_text_with_the_key_reaches_the_page_hidden(self):
        self.apply()
        job = self.ui.jobs.get("none")
        self.assertIsNone(job)
        j = webui.Job("j1", doc_name="d.md", answers_name="", provider="gemini", model="m", depth="full", max_cost="",
                      out_dir="x", report_stem="d")
        j.feed(f"Ошибка: провайдер ответил 401 для ключа {KEY}")
        j.finish("failed", error=f"Не удалось: ключ {KEY} не принят")
        with self.ui.lock:
            self.ui.jobs["j1"] = j
            self.ui.latest = j
        st, status = self.jget("/api/status?job=j1")
        text = json.dumps(status, ensure_ascii=False)
        self.assertNotIn(KEY, text)
        self.assertIn("***", text)

    def test_a_wrong_key_found_at_run_time_ends_the_check_without_the_key(self):
        def script(stage, system, user):
            raise LLMUnavailable(f"Ключ {KEY} не принят (401).", detail=f"HTTP 401 key={KEY}")

        self.apply()
        self.ui.backend = FakeBackend(script)
        job = self.run_check()
        self.assertIn(job["state"], ("done", "failed"))
        self.assertNotIn(KEY, json.dumps(job, ensure_ascii=False))
        self.assertNotIn(KEY, _all_text_under(self.out))
        self.assertFalse([x for x in self.logged if KEY in x])
        st, _cfg = self.jget("/api/config")  # the window is alive
        self.assertEqual(st, 200)


class ProviderCheckTests(unittest.TestCase):
    """check_key_with_provider with a replaced transport: what is asked and how the answers are read."""

    def run_check(self, provider, result, env=None):
        calls = []

        def fake(method, url, *, headers, body=None, timeout=300.0):
            calls.append((method, url, dict(headers), timeout))
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(webui, "request_json", fake):
            verdict = webui.check_key_with_provider(provider, KEY, env or {})
        return verdict, calls

    @staticmethod
    def res(status, text="", data=None):
        return HttpResult(status, data, text, {})

    def test_requests_are_the_free_model_lists(self):
        _v, c = self.run_check("anthropic", self.res(200))
        self.assertEqual(c[0][0], "GET")
        self.assertEqual(c[0][1], "https://api.anthropic.com/v1/models?limit=1")
        self.assertEqual(c[0][2]["x-api-key"], KEY)
        self.assertNotIn(KEY, c[0][1])
        _v, c = self.run_check("gemini", self.res(200))
        self.assertEqual(c[0][1], "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1")
        self.assertEqual(c[0][2]["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, c[0][1])
        _v, c = self.run_check("openai", self.res(200))
        self.assertEqual(c[0][1], "https://api.openai.com/v1/models")
        self.assertEqual(c[0][2]["Authorization"], "Bearer " + KEY)
        _v, c = self.run_check("openai", self.res(200), {"OPENAI_BASE_URL": "http://localhost:1234/v1/"})
        self.assertEqual(c[0][1], "http://localhost:1234/v1/models")
        for call in c:
            self.assertLessEqual(call[3], 8.0)

    def test_answers(self):
        self.assertEqual(self.run_check("anthropic", self.res(200))[0], ("ok", ""))
        for status in (401, 403):
            v, _c = self.run_check("openai", self.res(status, data={"error": {"message": "bad " + KEY}}))
            self.assertEqual(v[0], "rejected")
            self.assertNotIn(KEY, v[1])
        for status in (429, 500, 503, 404):
            self.assertEqual(self.run_check("openai", self.res(status))[0][0], "unknown")
        self.assertEqual(self.run_check("gemini", LLMUnavailable("нет связи"))[0], ("unknown", ""))
        self.assertEqual(self.run_check("gemini", TimeoutError())[0], ("unknown", ""))
        v, _c = self.run_check("gemini", self.res(400, text='{"error":{"status":"INVALID_ARGUMENT","message":"API key not valid."}}',
                                                   data={"error": {"message": "API key not valid."}}))
        self.assertEqual(v[0], "rejected")
        self.assertEqual(self.run_check("anthropic", self.res(400, text="bad request"))[0][0], "unknown")


class PageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (AGENT / "revizor" / "webui_page.html").read_text(encoding="utf-8")

    def test_field_is_a_password_field_with_labels(self):
        m = re.search(r'<input[^>]*id="key-input"[^>]*>', self.text)
        self.assertIsNotNone(m)
        tag = m.group(0)
        self.assertIn('type="password"', tag)
        self.assertIn('autocomplete="off"', tag)
        self.assertIn('spellcheck="false"', tag)
        self.assertIn('for="key-input"', self.text)
        self.assertIn('for="key-provider"', self.text)
        self.assertIn("Вставить свой ключ", self.text)
        self.assertIn("Применить", self.text)
        self.assertIn("Забыть ключ", self.text)
        self.assertIn("Ключ хранится только в памяти окна до его закрытия и никуда не записывается.", self.text)

    def test_no_storage_next_to_the_key_field(self):
        block = self.text.split("/* key:begin", 1)[1].split("/* key:end */", 1)[0]
        for needle in ("localStorage", "sessionStorage", "cookie", "history.", "location", "indexedDB", "URLSearchParams"):
            self.assertNotIn(needle, block, needle)
        # the value of the field is read at one place only and goes straight into the request body
        self.assertEqual(block.count("field.value"), 2)
        self.assertNotRegex(block, r"(var|let|const)\s+\w+\s*=\s*[^;]*key-input[^;]*\.value")
        # nothing outside of the block reads the field
        self.assertEqual(self.text.count('$("key-input")'), self.text.split("/* key:begin", 1)[1].split("/* key:end */", 1)[0].count('$("key-input")'))
        self.assertNotIn('name="key"', self.text)


if __name__ == "__main__":
    unittest.main()
