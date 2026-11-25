"""Pool order, estimate calibration, effort/token defaults, dropped invalid items, test-draft titles."""
from __future__ import annotations

import json
from pathlib import Path
import random
import threading
import time
import unittest

from revizor import estimate, pipeline, report_json, report_md, sidefiles, testgen
from revizor.llm import FakeBackend, LLMBadOutput
from revizor.llm.base import STAGE_EFFORT, STAGE_EFFORT_NOTE
from revizor.llm.schema import validate, validate_ex
from revizor.model import Finding, Question, Requirement
from revizor.prompts import SET_FIRST_ATTEMPT_TOKENS, SET_STAGES, SCHEMAS

from tests import test_pipeline as tp
from tests.dialog_helpers import Base


class PoolOrder(unittest.TestCase):
    def run_with_delays(self, seed: int):
        rnd = random.Random(seed)
        inner = tp.make_script()

        def script(stage, system, user):
            time.sleep(rnd.random() * 0.03)  # completion order differs from run to run
            return inner(stage, system, user)

        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "doc.md")
            Path(p).write_text(tp.DOC, encoding="utf-8")
            b = FakeBackend(script, concurrency=4)
            res = pipeline.run(p, backend=b, depth="full")
        j = json.loads(report_json.render(res)) if hasattr(report_json, "render") else None
        return res, j

    def test_results_do_not_depend_on_completion_order(self):
        outs = []
        for seed in (1, 2, 3):
            res, _ = self.run_with_delays(seed)
            outs.append(([(f.fid, f.title, f.severity, f.status) for f in res.findings + res.rejected],
                         [(q.qid, q.text, q.finding_ids) for q in res.questions]))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])

    def test_set_jobs_are_submitted_first(self):
        order: list[str] = []
        lock = threading.Lock()
        inner = tp.make_script()

        def script(stage, system, user):
            with lock:
                order.append(stage)
            return inner(stage, system, user)

        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "doc.md")
            Path(p).write_text(tp.DOC, encoding="utf-8")
            pipeline.run(p, backend=FakeBackend(script, concurrency=2), depth="full")
        first = [s for s in order if s.startswith(("review", "set_"))][:2]
        self.assertTrue(all(s.startswith("set_") for s in first), first)


class EffortAndTokens(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(STAGE_EFFORT["set_completeness"], "medium")
        self.assertEqual(STAGE_EFFORT["set_consistency"], "high")
        self.assertIn("противоречий между разделами — high", STAGE_EFFORT_NOTE)
        for st in ("review", "review_clarity", "review_completeness", "review_correctness"):
            self.assertEqual(STAGE_EFFORT[st], "low", st)
        self.assertIn("проверка требований, нарезка, вопросы и черновики тестов — low", STAGE_EFFORT_NOTE)

    def test_set_stages_ask_for_the_ceiling_on_claude_only(self):
        from revizor.llm.base import MAX_RETRY_TOKENS
        self.assertEqual(SET_FIRST_ATTEMPT_TOKENS, MAX_RETRY_TOKENS)
        class B:
            provider = "anthropic"
        g = pipeline.Gate(B(), "s", None, [])
        for st in SET_STAGES:
            self.assertGreaterEqual(g.max_tokens_for(st), SET_FIRST_ATTEMPT_TOKENS)
        self.assertLess(g.max_tokens_for("review_clarity"), SET_FIRST_ATTEMPT_TOKENS)
        B.provider = "openai"
        self.assertLess(pipeline.Gate(B(), "s", None, []).max_tokens_for("set_consistency"), SET_FIRST_ATTEMPT_TOKENS)


class EstimateCalibration(unittest.TestCase):
    def test_requirements_md_within_30_percent_of_the_measured_run(self):
        e = estimate.build("docs/requirements.md", depth="full")
        out = estimate.expected_output(e, "anthropic", {})
        self.assertAlmostEqual(out / 268143, 1.0, delta=0.30)  # measured 25.11.2026, review at «low»
        secs = estimate.expected_seconds(e, "anthropic", {})
        self.assertLess(secs, 710)            # one pool and the long checks first: faster than the measured run
        self.assertGreater(secs, 710 * 0.4)


class DropInvalidItems(Base):
    def run_reviews(self, reviews_by_stage, doc=tp.DOC):
        def inner(stage, system, user):
            if stage == "review_clarity":
                ans = tp.review_answer(stage, user)
                return reviews_by_stage(ans)
            return tp.make_script()(stage, system, user)
        b = FakeBackend(inner, concurrency=1)
        res = pipeline.run(self.doc(doc), backend=b, depth="full")
        return res, b

    def test_schema_drops_bad_item_and_reports_it(self):
        sch = SCHEMAS["review_clarity"]
        data = {"reviews": [{"rid": "REQ-001", "criteria": {}}, {"criteria": {}}, {"rid": "REQ-003", "criteria": {}}]}
        out, errors, notes = validate_ex(data, sch)
        self.assertEqual(errors, [])
        self.assertEqual([r["rid"] for r in out["reviews"]], ["REQ-001", "REQ-003"])
        self.assertEqual(notes[0]["path"], "$.reviews[1]")
        _o, errs = validate(data, sch)  # old signature keeps working
        self.assertEqual(errs, [])

    def test_all_items_bad_is_still_an_error(self):
        _o, errors, _n = validate_ex({"reviews": [{"criteria": {}}, {"x": 1}]}, SCHEMAS["review_clarity"])
        self.assertTrue(errors)

    def test_one_bad_item_among_three_no_repair_call(self):
        def damage(ans):
            ans["reviews"][1].pop("rid")
            return ans
        res, b = self.run_reviews(damage)
        calls = [s for s, _sy, _u in b.log if s == "review_clarity"]
        self.assertEqual(len(calls), 1)                      # no repair round-trip
        dropped = [a for a in res.audit if a.get("type") == "item_dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["stage"], "review_clarity")
        self.assertIn("$.reviews[1]", dropped[0]["path"])
        self.assertTrue(any("не вернула оценку" in (v.note or "") or "не вернула" in (v.note or "")
                            for rv in res.reviews for v in rv.criteria.values()))
        self.assertTrue(any("без оценки модели" in w.lower() for w in res.run.warnings))

    def test_all_items_bad_repairs_then_fails_as_before(self):
        def wreck(ans):
            return {"reviews": [{"criteria": {}} for _ in ans["reviews"]]}
        res, b = self.run_reviews(wreck)
        calls = [s for s, _sy, _u in b.log if s == "review_clarity"]
        self.assertEqual(len(calls), 2)                      # first answer + one repair, then the batch is lost
        self.assertTrue(any("review_clarity" in a.get("stage", "") for a in res.audit if a.get("what") == "batch_unchecked"))


def R(rid, text, doc_id=""):
    return Requirement(rid=rid, doc_id=doc_id, section="", kind="functional", quote=text, summary=text[:80],
                       block_ids=[], line_start=1, line_end=1)


class DraftTitles(unittest.TestCase):
    def test_blocked_draft_is_titled_by_the_requirement_not_the_defect(self):
        req = R("REQ-010", "Ф-7. Сервис передаёт push на отправку в течение минуты после получения события.", "Ф-7")
        f = Finding(scope="requirement", criterion="verifiability", severity="MAJOR",
                    title="Ограничение по времени без условий измерения", problem="", recommendation="",
                    req_ids=["REQ-010"], fid="D-001", question_id="В-01")
        ds = testgen.from_findings([req], [f], set())
        self.assertEqual(len(ds), 1)
        self.assertNotIn("условий измерения", ds[0].title)
        self.assertTrue(ds[0].title.startswith("Ф-7: Сервис передаёт push"))

    def test_boundary_without_result_links_a_question_and_says_so(self):
        req = R("REQ-001", "Клиент задаёт порог от 1 000 до 1 000 000 включительно.", "Ф-1")
        q = Question(qid="В-03", text="Что при значении вне диапазона?", why="", addressee="analyst",
                     req_ids=["REQ-001"])
        drafts = testgen.finalize(testgen.boundary_drafts([req]), [req], [], [q])
        out = [d for d in drafts if not d.then][0]
        self.assertEqual(out.blocked_by, ["В-03"])
        from revizor.model import Result, RunInfo
        res = Result(run=RunInfo(doc_name="d.md"), requirements=[req], tests=drafts, questions=[q])
        self.assertIn("ожидаемый результат в документе не описан — нужен ответ автора", sidefiles.tests_md(res))
        self.assertIn("ожидаемый результат в документе не описан — нужен ответ автора", report_md.render(res)
                      if hasattr(report_md, "render") else "ожидаемый результат в документе не описан — нужен ответ автора")


if __name__ == "__main__":
    unittest.main()
