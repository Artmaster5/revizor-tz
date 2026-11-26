"""Wave 9 J: after the author's answers the top of the result tells the with-answers story (readiness, verdict card,
report first screen), a run of seconds shows no «time by stage» line."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from revizor import consistency, pipeline, readiness, report_json, report_md, webui
from tests import test_pipeline as tp
from tests.dialog_helpers import provider_from_text

AGENT = Path(__file__).resolve().parent.parent
PAGE = (AGENT / "revizor" / "webui_page.html").read_text(encoding="utf-8")
_PINNED: list = []


def setUpModule() -> None:
    _PINNED.extend(tp.pin_policy())


def tearDownModule() -> None:
    tp.unpin_policy(_PINNED)


def _answered():
    text = (AGENT / "reports" / "sample_answers.md").read_text(encoding="utf-8")
    return pipeline.run(str(AGENT / "docs" / "requirements.md"), backend=None, answers=provider_from_text(text))


def _all_blockers_closed(res):
    """The same result as if the answers settled every blocker (the real engine fields, set by hand)."""
    for f in res.findings:
        if f.severity == "CRITICAL" and f.status == "open" and not f.advisory:
            f.closed_by_answer = True
    consistency.settle(res)
    return res


class EngineAfterAnswers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _answered()

    def test_no_answers_no_block(self):
        res = pipeline.run(str(AGENT / "docs" / "requirements.md"), backend=None)
        self.assertNotIn("after_answers", res.readiness)
        self.assertNotIn("after_answers", json.loads(report_json.render(res))["readiness"])

    def test_blockers_stay_when_answers_do_not_close_them(self):
        res = self.res
        left = [f.fid for f in res.findings if f.status == "open" and not f.advisory
                and f.severity == "CRITICAL" and not f.closed_by_answer]
        self.assertTrue(left)
        aa = res.readiness["after_answers"]
        self.assertEqual(aa["dev"]["blockers"], left)
        self.assertFalse(aa["dev"]["ok"])
        self.assertIn(f"{len(left)} ", aa["dev"]["text"])
        self.assertEqual(consistency.check_consistency(res), [])

    def test_closed_blockers_do_not_count(self):
        res = _all_blockers_closed(_answered())
        self.assertTrue(res.readiness["dev"]["blockers"], "as written the blockers stay")
        aa = res.readiness["after_answers"]
        self.assertEqual(aa["dev"]["blockers"], [])
        self.assertTrue(aa["dev"]["ok"])
        self.assertTrue(aa["dev"]["text"].startswith("Можно отдавать в разработку"))
        self.assertEqual(consistency.check_consistency(res), [])

    def test_remains_after_fix_check_stays_open(self):
        res = _answered()
        crit = next(f for f in res.findings if f.severity == "CRITICAL")
        crit.closed_by_answer = False  # what the fix check does when the rule still fires
        crit.fix_check = {"status": "remains", "note": "правило срабатывает"}
        consistency.settle(res)
        self.assertIn(crit.fid, res.readiness["after_answers"]["dev"]["blockers"])

    def test_old_json_without_it_renders(self):
        res = _answered()
        res.readiness.pop("after_answers")
        md = report_md.render(res)
        self.assertIn("Нельзя отдавать в разработку", md)
        self.assertEqual(webui.clean_readiness(res.readiness).get("after_answers"), None)

    def test_json_carries_it(self):
        data = json.loads(report_json.render(self.res))
        self.assertEqual(data["readiness"]["after_answers"]["dev"]["blockers"],
                         self.res.readiness["after_answers"]["dev"]["blockers"])


class ReportFirstScreen(unittest.TestCase):
    def head(self, res):
        return report_md.render(res).split("**Разделы:**", 1)[0]

    def test_lines_tell_both_states_and_stay_short(self):
        res = _all_blockers_closed(_answered())
        head = self.head(res)
        self.assertRegex(head, r"> ✅ Можно отдавать в разработку: блокеров нет — с учётом ответов; в тексте документа пока: \d+ блок")
        for x in head.splitlines():
            if "в разработку" in x or "в тестирование" in x:
                self.assertLessEqual(len(x.split()), 40, x)

    def test_blockers_left_line_is_red(self):
        res = _answered()
        head = self.head(res)
        dev = next(x for x in head.splitlines() if "в разработку" in x)
        self.assertTrue(dev.startswith("> ⛔"))
        self.assertRegex(dev, r"Нельзя отдавать в разработку: \d+ блок\S* — с учётом ответов; в тексте документа пока: \d+ блок")
        test = next(x for x in head.splitlines() if "в тестирование" in x)
        self.assertIn("с учётом ответов то же", test)
        self.assertLessEqual(len(dev.split()), 40)


class WindowSummary(unittest.TestCase):
    def summary(self, res):
        out, _files = webui.result_summary(res, [], "j1", "stem", "out", with_answers=True)
        return out

    def test_card_is_green_when_answers_close_every_blocker(self):
        res = _all_blockers_closed(_answered())
        s = self.summary(res)
        a = s["answers"]["after"]
        self.assertNotEqual(a["written_verdict"], a["verdict"])
        self.assertEqual(a["written_verdict"], "BLOCKED")
        self.assertIn(a["verdict_level"], ("ok", "warn"))
        self.assertTrue(a["written_verdict_text"])
        self.assertNotIn("остаётся", a["verdict_text"])
        self.assertTrue(s["readiness"]["after_answers"]["dev"]["ok"])
        self.assertFalse(s["readiness"]["dev"]["ok"])

    def test_card_stays_red_and_counts_what_is_left(self):
        res = _answered()
        left = [f for f in res.findings if f.status == "open" and not f.advisory
                and f.severity == "CRITICAL" and not f.closed_by_answer]
        a = self.summary(res)["answers"]["after"]
        self.assertEqual(a["verdict"], "BLOCKED")
        self.assertEqual(a["verdict_level"], "stop")
        self.assertRegex(a["verdict_text"], rf"с учётом ответов остаётся {len(left)} блокер")

    def test_run_without_answers_has_no_after(self):
        res = pipeline.run(str(AGENT / "docs" / "requirements.md"), backend=None)
        out, _f = webui.result_summary(res, [], "j1", "stem", "out")
        self.assertIsNone(out["answers"])
        self.assertNotIn("after_answers", out["readiness"])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PageScript(unittest.TestCase):
    """The page's own functions run in node with a tiny stand-in for the document."""

    PRELUDE = r"""
var els={};
function El(){this.children=[];this.hidden=false;this.className="";this.textContent="";this.classList={toggle(){}};}
El.prototype.appendChild=function(c){this.children.push(c);return c;};
function mk(t,c,x){var e=new El();e.className=c||"";e.textContent=x||"";return e;}
function $(id){return els[id]||(els[id]=new El());}
function clear(p){p.children=[];}
function num(v){return Number(v)||0;}
"""

    def run_js(self, code: str):
        def grab(name):
            m = re.search(r"^function " + name + r"\(.*?^}\n", PAGE, re.S | re.M)
            assert m, name
            return m.group(0)
        sm = re.search(r"^var SHORT_STAGE=.*?;\n", PAGE, re.M).group(0)
        src = self.PRELUDE + grab("fmtSec") + sm + grab("renderStageTimes") + grab("renderReadiness") + code
        r = subprocess.run(["node", "-e", src], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def stage_line(self, secs):
        return self.run_js("renderStageTimes(%s);console.log(JSON.stringify({hidden:$('res-stage-times').hidden,"
                           "text:$('res-stage-times').textContent}));" % json.dumps(secs))

    def test_instant_run_has_no_time_line(self):
        self.assertTrue(self.stage_line([0.0, 0.0, 0.1, 0.4, 0.0, 0.0, 0.0, 0.0, 0.2])["hidden"])

    def test_slow_run_lists_only_stages_of_a_tenth_of_a_second(self):
        r = self.stage_line([0.0, 0.05, 0.1, 2.5, 0.0, 0.0, 0.0, 0.0, 0.2])
        self.assertFalse(r["hidden"])
        self.assertIn("правила 0,1 с", r["text"])
        self.assertIn("требования 2,5 с", r["text"])
        self.assertIn("индексы 0,2 с", r["text"])
        self.assertNotIn("чтение", r["text"])
        self.assertNotIn("нарезка", r["text"])

    def test_readiness_lines_show_with_answers_state_and_was(self):
        rd = {"dev": {"ok": False, "text": "Нельзя отдавать в разработку: 3 блокера"},
              "test": {"ok": False, "text": "Нельзя отдавать в тестирование: нет критериев приёмки"},
              "after_answers": {"dev": {"ok": True, "text": "Можно отдавать в разработку: блокеров нет"},
                                "test": {"ok": False, "text": "Нельзя отдавать в тестирование: нет критериев приёмки"}}}
        r = self.run_js("renderReadiness(%s);function t(id){return $(id).children.map(function(c){return c.textContent}).join('');}"
                        "console.log(JSON.stringify({dev:t('ready-dev'),test:t('ready-test')}));" % json.dumps(rd))
        self.assertEqual(r["dev"], "В разработку — с учётом ответов: Можно отдавать в разработку: блокеров нет (было: 3 блокера)")
        self.assertNotIn("было", r["test"])
        self.assertTrue(r["test"].startswith("В тестирование — с учётом ответов: "))

    def test_readiness_without_after_answers_is_as_before(self):
        rd = {"dev": {"ok": False, "text": "Нельзя отдавать в разработку: 3 блокера"}}
        r = self.run_js("renderReadiness(%s);console.log(JSON.stringify({dev:$('ready-dev').children.map(function(c){return c.textContent}).join('')}));" % json.dumps(rd))
        self.assertEqual(r["dev"], "В разработку: Нельзя отдавать в разработку: 3 блокера")


if __name__ == "__main__":
    unittest.main()
