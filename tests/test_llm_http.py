"""Gemini and OpenAI-compatible backends against a local http.server on 127.0.0.1 (no internet)."""
from __future__ import annotations

import json
import socket
import threading
import traceback
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from revizor.llm import LLMBadOutput, LLMRefused, LLMTransient, LLMUnavailable, make_backend
from revizor.llm.gemini_backend import GeminiBackend, pick_model
from revizor.llm.openai_compat_backend import OpenAICompatBackend

GKEY = "AI" "zaSy-TEST-KEY-not-REAL-0123456789"
OKEY = "sk-proj-TESTKEYnotREAL0123456789abcdef"
SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


class LocalServer:
    """route(method, path, body) -> (status, headers, body_obj_or_bytes)."""

    def __init__(self, route):
        self.route = route
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                outer.requests.append({"method": self.command, "path": self.path,
                                       "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status, headers, payload = outer.route(self.command, self.path, body)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _handle

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def seq(*responses):
    """Route that answers in order, repeating the last answer."""
    state = {"i": 0}
    lock = threading.Lock()

    def route(method, path, body):
        with lock:
            i = state["i"]
            state["i"] += 1
        return responses[min(i, len(responses) - 1)]
    return route


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def assert_no_key(tc, e, key):
    for t in (str(e), repr(e), getattr(e, "user_message", ""), getattr(e, "detail", ""),
              "".join(traceback.format_exception(type(e), e, e.__traceback__))):
        tc.assertNotIn(key, t)
        tc.assertNotIn("TESTKEYnotREAL", t)


def gem_ok(text='{"ok": true}', finish="STOP", thought=True):
    parts = ([{"text": "думаю {не json", "thought": True}] if thought else []) + [{"text": text}]
    return 200, {}, {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
                     "usageMetadata": {"promptTokenCount": 1000, "cachedContentTokenCount": 400,
                                       "candidatesTokenCount": 50, "thoughtsTokenCount": 30}}


def gem_err(code, status, message, details=None):
    return code, {}, {"error": {"code": code, "message": message, "status": status, "details": details or []}}


class GeminiTests(unittest.TestCase):
    def serve(self, route):
        srv = LocalServer(route)
        self.addCleanup(srv.close)
        return srv

    def make(self, srv, model="gemini-flash-latest"):
        b = GeminiBackend(GKEY, model=model, base_url=srv.url, timeout=10)
        self.sleeps = []
        b.sleep = self.sleeps.append
        return b

    def test_request_shape_key_in_header_not_url(self):
        srv = self.serve(seq(gem_ok('```json\n{"ok": true}\n```')))
        b = self.make(srv)
        data, usage = b.complete_json("СИСТЕМА", "задача", schema=SCHEMA, max_tokens=16000)
        self.assertEqual(data, {"ok": True})
        r = srv.requests[0]
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["path"], "/v1beta/models/gemini-flash-latest:generateContent")
        self.assertNotIn("key", r["path"].lower().split(":")[0] + (r["path"].split("?")[1] if "?" in r["path"] else ""))
        self.assertNotIn(GKEY, r["path"])
        self.assertEqual(r["headers"]["x-goog-api-key"], GKEY)
        body = r["body"]
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "СИСТЕМА"}]})
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "задача"}]}])
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertGreaterEqual(body["generationConfig"]["maxOutputTokens"], 16000)
        self.assertEqual((usage.input_tokens, usage.cache_read, usage.output_tokens), (600, 400, 80))
        self.assertEqual(b.concurrency, 1)

    def test_404_model_discovery(self):
        models = {"models": [
            {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-flash-preview-tts", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]}

        def route(method, path, body):
            if method == "GET" and path.startswith("/v1beta/models"):
                return 200, {}, models
            if "gemini-1.5-flash" in path:
                return gem_err(404, "NOT_FOUND", "models/gemini-1.5-flash is not found for API version v1beta")
            return gem_ok()

        srv = self.serve(route)
        b = self.make(srv, model="gemini-1.5-flash")
        data, _ = b.complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(b.model, "gemini-2.5-flash")
        self.assertTrue(any("gemini-1.5-flash" in n and "gemini-2.5-flash" in n for n in b.notes))
        get = [r for r in srv.requests if r["method"] == "GET"][0]
        self.assertEqual(get["headers"]["x-goog-api-key"], GKEY)
        self.assertNotIn(GKEY, get["path"])
        self.assertEqual(srv.requests[-1]["path"], "/v1beta/models/gemini-2.5-flash:generateContent")

    def test_pick_model_ranking(self):
        def m(n):
            return {"name": "models/" + n, "supportedGenerationMethods": ["generateContent"]}
        self.assertEqual(pick_model([m("gemini-2.5-flash"), m("gemini-flash-latest"), m("gemini-3-flash-preview")]),
                         "gemini-flash-latest")
        self.assertEqual(pick_model([m("gemini-2.5-flash"), m("gemini-3-flash-preview"), m("gemini-3-flash-lite")]),
                         "gemini-3-flash-preview")
        self.assertEqual(pick_model([m("gemini-2.0-flash"), m("gemini-2.0-flash-exp")]), "gemini-2.0-flash")
        self.assertIsNone(pick_model([m("gemini-2.5-pro"), m("gemini-2.5-flash-image")]))

    def test_404_without_replacement_is_unavailable(self):
        def route(method, path, body):
            if method == "GET":
                return 200, {}, {"models": []}
            return gem_err(404, "NOT_FOUND", "not found")
        srv = self.serve(route)
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(srv, model="gemini-0-none").complete_json("s", "u")
        self.assertIn("GEMINI_MODEL", cm.exception.user_message)

    def test_429_retry_delay_then_success(self):
        srv = self.serve(seq(gem_err(429, "RESOURCE_EXHAUSTED", "quota", [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]), gem_ok()))
        b = self.make(srv)
        data, _ = b.complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreaterEqual(self.sleeps[0], 7.0)
        self.assertLess(self.sleeps[0], 8.0)

    def test_daily_quota_not_retried(self):
        srv = self.serve(seq(gem_err(429, "RESOURCE_EXHAUSTED", "quota", [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}])))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(srv).complete_json("s", "u")
        self.assertIn("Дневная квота", cm.exception.user_message)
        self.assertEqual(len(srv.requests), 1)

    def test_bad_key_400_is_unavailable_and_masked(self):
        srv = self.serve(seq(gem_err(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key. " + GKEY,
                                     [{"reason": "API_KEY_INVALID"}])))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(srv).complete_json("s", "u")
        self.assertIn("aistudio.google.com", cm.exception.user_message)
        assert_no_key(self, cm.exception, GKEY)

    def test_403_is_unavailable(self):
        srv = self.serve(seq(gem_err(403, "PERMISSION_DENIED", "denied")))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(srv).complete_json("s", "u")
        assert_no_key(self, cm.exception, GKEY)

    def test_5xx_exhausted(self):
        srv = self.serve(seq(gem_err(503, "UNAVAILABLE", "The model is overloaded")))
        b = self.make(srv)
        with self.assertRaises(LLMTransient):
            b.complete_json("s", "u")
        self.assertEqual(len(srv.requests), 4)
        self.assertEqual(b.calls, 4)

    def test_max_tokens_and_safety(self):
        srv = self.serve(seq(gem_ok('{"ok": tr', finish="MAX_TOKENS")))
        with self.assertRaises(LLMBadOutput):
            self.make(srv).complete_json("s", "u")
        self.assertEqual(len(srv.requests), 2)  # one retry with a doubled limit
        srv2 = self.serve(seq(gem_ok("", finish="SAFETY", thought=False)))
        with self.assertRaises(LLMRefused):
            self.make(srv2).complete_json("s", "u")
        srv3 = self.serve(seq((200, {}, {"promptFeedback": {"blockReason": "SAFETY"}})))
        with self.assertRaises(LLMRefused):
            self.make(srv3).complete_json("s", "u")

    def test_repair_round_trip(self):
        srv = self.serve(seq(gem_ok('{"ok": "maybe"}'), gem_ok('{"ok": false}')))
        data, _ = self.make(srv).complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data, {"ok": False})
        self.assertIn("Верни только исправленный JSON", srv.requests[1]["body"]["contents"][0]["parts"][0]["text"])

    def test_connection_refused(self):
        b = GeminiBackend(GKEY, base_url="http://127.0.0.1:%d" % free_port(), timeout=5)
        b.sleep = lambda s: None
        with self.assertRaises(LLMUnavailable) as cm:
            b.complete_json("s", "u")
        self.assertEqual(b.calls, 1)
        assert_no_key(self, cm.exception, GKEY)

    def test_make_backend_google_key(self):
        b = make_backend("auto", {"GEMINI_API_KEY": "your_gemini_api_key_here", "GOOGLE_API_KEY": GKEY,
                                  "GEMINI_MODEL": "gemini-1.5-flash"}, cache_dir="/nonexistent-never-written")
        self.assertEqual((b.provider, b.model, b.concurrency), ("gemini", "gemini-1.5-flash", 1))


def oa_ok(content='{"ok": true}', finish="stop", refusal=None):
    return 200, {}, {"choices": [{"index": 0, "message": {"role": "assistant", "content": content, "refusal": refusal},
                                  "finish_reason": finish}],
                     "usage": {"prompt_tokens": 500, "completion_tokens": 20,
                               "prompt_tokens_details": {"cached_tokens": 100}}}


def oa_err(status, message, etype="invalid_request_error", code=None, headers=None):
    return status, headers or {}, {"error": {"message": message, "type": etype, "code": code}}


class OpenAICompatTests(unittest.TestCase):
    def serve(self, route):
        srv = LocalServer(route)
        self.addCleanup(srv.close)
        return srv

    def make(self, srv, **kw):
        kw.setdefault("api_key", OKEY)
        kw.setdefault("model", "gpt-4o-mini")
        b = OpenAICompatBackend(base_url=srv.url + "/v1", timeout=10, **kw)
        self.sleeps = []
        b.sleep = self.sleeps.append
        return b

    def test_request_shape(self):
        srv = self.serve(seq(oa_ok("Вот ответ: {\"ok\": true}")))
        b = self.make(srv)
        data, usage = b.complete_json("СИСТЕМА", "задача", schema=SCHEMA, max_tokens=30000)
        self.assertEqual(data, {"ok": True})
        r = srv.requests[0]
        self.assertEqual(r["path"], "/v1/chat/completions")
        self.assertEqual(r["headers"]["authorization"], "Bearer " + OKEY)
        body = r["body"]
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["messages"], [{"role": "system", "content": "СИСТЕМА"},
                                            {"role": "user", "content": "задача"}])
        self.assertEqual(body["max_tokens"], 16384)  # gpt-4o cap; local base → max_tokens name
        self.assertNotIn("temperature", body)
        self.assertEqual((usage.input_tokens, usage.cache_read, usage.output_tokens), (400, 100, 20))

    def test_official_api_uses_max_completion_tokens(self):
        b = OpenAICompatBackend(api_key=OKEY, base_url="https://api.openai.com/v1")
        self.assertIn("max_completion_tokens", b.build_body("s", "u", 1000))

    def test_response_format_rejected_then_retried(self):
        srv = self.serve(seq(oa_err(400, "Invalid parameter: 'response_format' of type 'json_object' is not supported"),
                             oa_ok()))
        b = self.make(srv)
        data, _ = b.complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data, {"ok": True})
        self.assertIn("response_format", srv.requests[0]["body"])
        self.assertNotIn("response_format", srv.requests[1]["body"])
        self.assertTrue(b.notes)

    def test_401_masked(self):
        srv = self.serve(seq(oa_err(401, "Incorrect API key provided: " + OKEY, "invalid_request_error",
                                    "invalid_api_key")))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(srv).complete_json("s", "u")
        self.assertIn("OPENAI_API_KEY", cm.exception.user_message)
        assert_no_key(self, cm.exception, OKEY)

    def test_429_insufficient_quota_not_retried(self):
        srv = self.serve(seq(oa_err(429, "You exceeded your current quota", "insufficient_quota", "insufficient_quota")))
        with self.assertRaises(LLMUnavailable):
            self.make(srv).complete_json("s", "u")
        self.assertEqual(len(srv.requests), 1)

    def test_429_retry_after_then_success(self):
        srv = self.serve(seq(oa_err(429, "Rate limit reached", "requests", headers={"Retry-After": "2"}), oa_ok()))
        b = self.make(srv)
        data, _ = b.complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(len(self.sleeps), 1)
        self.assertGreaterEqual(self.sleeps[0], 2.0)

    def test_500_exhausted(self):
        srv = self.serve(seq(oa_err(500, "server error", "server_error")))
        b = self.make(srv)
        with self.assertRaises(LLMTransient):
            b.complete_json("s", "u")
        self.assertEqual(len(srv.requests), 4)
        self.assertEqual(len(self.sleeps), 3)

    def test_connection_refused(self):
        b = OpenAICompatBackend(api_key=OKEY, base_url="http://127.0.0.1:%d/v1" % free_port(), timeout=5)
        b.sleep = lambda s: None
        with self.assertRaises(LLMUnavailable) as cm:
            b.complete_json("s", "u")
        self.assertIn("соединение отклонено", cm.exception.user_message)
        self.assertEqual(b.calls, 1)
        assert_no_key(self, cm.exception, OKEY)

    def test_length_refusal_and_404(self):
        srv = self.serve(seq(oa_ok('{"ok": tr', finish="length")))
        with self.assertRaises(LLMBadOutput):
            self.make(srv).complete_json("s", "u")
        srv2 = self.serve(seq(oa_ok(None, refusal="I can't help with that")))
        with self.assertRaises(LLMRefused):
            self.make(srv2).complete_json("s", "u")
        srv3 = self.serve(seq(oa_err(404, "model 'qwen' not found", "not_found_error")))
        with self.assertRaises(LLMUnavailable) as cm:
            self.make(srv3, provider="ollama", api_key="", model="qwen").complete_json("s", "u")
        self.assertIn("ollama pull qwen", cm.exception.user_message)

    def test_ollama_model_autodetect_and_base_url(self):
        def route(method, path, body):
            if method == "GET" and path == "/v1/models":
                return 200, {}, {"object": "list", "data": [{"id": "nomic-embed-text"}, {"id": "qwen3:8b"}]}
            return oa_ok('<think>сначала подумаю {}</think>\n{"ok": true}')
        srv = self.serve(route)
        b = make_backend("auto", {"OLLAMA_BASE_URL": srv.url}, cache_dir="/nonexistent-never-written")
        self.assertEqual((b.provider, b.base_url, b.concurrency), ("ollama", srv.url + "/v1", 1))
        b.cache = None
        data, _ = b.complete_json("s", "u", schema=SCHEMA)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(b.model, "qwen3:8b")
        self.assertNotIn("authorization", srv.requests[-1]["headers"])
        self.assertEqual(srv.requests[-1]["body"]["model"], "qwen3:8b")

    def test_make_backend_openai_local_without_key(self):
        b = make_backend("openai", {"OPENAI_BASE_URL": "http://localhost:1234/v1", "OPENAI_MODEL": "local-model"},
                         cache_dir="/nonexistent-never-written")
        self.assertEqual((b.provider, b.model, b.local), ("openai", "local-model", True))
        with self.assertRaises(LLMUnavailable):
            make_backend("openai", {"OPENAI_API_KEY": "sk-..."})


if __name__ == "__main__":
    unittest.main()
