"""MCP-сервер: обработчик запросов напрямую и один сквозной запуск процессом."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from revizor import mcp_server

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "requirements.md"


def rpc(method: str, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return mcp_server.handle_line(json.dumps(msg))


class HandlerTests(unittest.TestCase):
    def test_initialize_echoes_known_version(self):
        r = rpc("initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(r["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(r["result"]["capabilities"], {"tools": {}})
        self.assertTrue(r["result"]["serverInfo"]["version"])

    def test_initialize_unknown_version_falls_back(self):
        r = rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")

    def test_notification_has_no_reply(self):
        msg = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(mcp_server.handle_line(msg))

    def test_check_rejects_unknown_provider_and_depth(self):
        for args in ({"path": str(DOC), "provider": "claude"}, {"path": str(DOC), "depth": "deep"}):
            r = rpc("tools/call", {"name": "check_requirements", "arguments": args})
            self.assertEqual(r["error"]["code"], -32602)

    def test_check_fast_offline(self):
        with tempfile.TemporaryDirectory() as d:
            r = rpc("tools/call", {"name": "check_requirements", "arguments": {
                "path": str(DOC), "provider": "offline", "depth": "fast", "output_dir": d}})
            self.assertFalse(r["result"]["isError"], r["result"]["content"][0]["text"])
            self.assertIn("Вердикт", r["result"]["content"][0]["text"])

    def test_system_exit_inside_tool_keeps_server(self):
        old = mcp_server._HANDLERS["list_checks"]
        mcp_server._HANDLERS["list_checks"] = lambda args: (_ for _ in ()).throw(SystemExit(3))
        try:
            r = rpc("tools/call", {"name": "list_checks", "arguments": {}})
        finally:
            mcp_server._HANDLERS["list_checks"] = old
        self.assertTrue(r["result"]["isError"])

    def test_ping_and_tools_list(self):
        self.assertEqual(rpc("ping")["result"], {})
        names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["check_requirements", "verify_quote", "list_checks", "estimate_run"])

    def test_unknown_method(self):
        self.assertEqual(rpc("no/such")["error"]["code"], -32601)

    def test_bad_json(self):
        self.assertEqual(mcp_server.handle_line("{не json")["error"]["code"], -32700)

    def test_bad_params(self):
        self.assertEqual(rpc("tools/call", {"arguments": {}})["error"]["code"], -32602)
        self.assertEqual(rpc("tools/call", {"name": "verify_quote", "arguments": {"text": "abc"}})["error"]["code"], -32602)

    def test_verify_quote_found_and_missing(self):
        text = "# ТЗ\n\n" + "".join(f"ФТ-{i}. Система должна показывать остаток по счёту клиента после каждой операции.\n" for i in range(1, 3)) \
            + "ФТ-3. Система должна отвечать за 2 секунды при нагрузке до 100 запросов в секунду.\n" \
            + "ФТ-4. Система должна хранить историю операций клиента не менее пяти лет подряд.\n"
        ok = rpc("tools/call", {"name": "verify_quote", "arguments": {"text": text, "quote": "отвечать за 2 секунды"}})
        self.assertFalse(ok["result"]["isError"])
        self.assertIn("строка 5", ok["result"]["content"][0]["text"])
        bad = rpc("tools/call", {"name": "verify_quote", "arguments": {"text": text, "quote": "совсем другая фраза про банан"}})
        self.assertIn("нет", bad["result"]["content"][0]["text"])

    def test_tool_failure_is_error_result(self):
        r = rpc("tools/call", {"name": "check_requirements", "arguments": {"path": "/нет/такого/файла.md"}})
        self.assertTrue(r["result"]["isError"])
        self.assertIn("не найден", r["result"]["content"][0]["text"])

    def test_list_checks_and_estimate(self):
        r = rpc("tools/call", {"name": "list_checks"})
        self.assertIn("vague-word", r["result"]["content"][0]["text"])
        e = rpc("tools/call", {"name": "estimate_run", "arguments": {"path": str(DOC), "provider": "offline"}})
        self.assertFalse(e["result"]["isError"])
        self.assertIn("Оценка прогона", e["result"]["content"][0]["text"])


class SubprocessTest(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            reqs = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "check_requirements",
                            "arguments": {"path": str(DOC), "provider": "offline", "output_dir": tmp}}},
            ]
            data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in reqs)
            p = subprocess.run([sys.executable, str(ROOT / "mcp_server.py")], input=data, capture_output=True,
                               text=True, encoding="utf-8", timeout=300, cwd=tmp)
            lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
            replies = [json.loads(ln) for ln in lines]  # every stdout line must be protocol JSON
            self.assertEqual([r["id"] for r in replies], [1, 2, 3], p.stderr)
            self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
            self.assertEqual(len(replies[1]["result"]["tools"]), 4)
            res = replies[2]["result"]
            self.assertFalse(res["isError"], res)
            self.assertIn("Вердикт", res["content"][0]["text"])
            self.assertTrue((Path(tmp) / "requirements.report.md").is_file())
            self.assertTrue((Path(tmp) / "requirements.report.json").is_file())


if __name__ == "__main__":
    unittest.main()
