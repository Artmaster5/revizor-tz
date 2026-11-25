"""Stage-usage table, progress lines, unchecked-pairs warning, effort label, estimate with thinking."""
from __future__ import annotations

import json
import re
import unittest

from revizor import estimate, pipeline, report_json, report_md
from revizor.llm import FakeBackend, LLMBadOutput

from tests import test_pipeline as tp
from tests.dialog_helpers import Base


class StageUsageTests(Base):
    def test_passport_table_and_json(self):
        res, _ = self.run_fake(tp.make_script(), path=self.doc())
        md = report_md.render_run_details(res)  # the stage table lives in the diagnostic side file
        self.assertIn("**Расход по этапам**", md)
        self.assertIn("рассуждения модели — они оплачиваются как выход", md)
        self.assertRegex(md, r"\| нарезка \| 1 \| [\d\s]+ \| [\d\s]+ \|")
        self.assertIn("| линза «ясность» |", md)
        rows = json.loads(report_json.render(res))["run"]["stage_usage"]
        stages = [r["stage"] for r in rows]
        self.assertEqual(stages[0], "slice")
        self.assertIn("review_clarity", stages)
        self.assertTrue(all({"calls", "input_tokens", "output_tokens", "seconds"} <= set(r) for r in rows))

    def test_offline_has_no_table(self):
        res = self.run_offline(path=self.doc())
        self.assertNotIn("Расход по этапам", report_md.render(res))


class ProgressTests(Base):
    def test_progress_lines_and_quiet(self):
        lines: list[str] = []
        self.run_fake(tp.make_script(), path=self.doc(), progress=lines.append)
        done = [ln for ln in lines if ln.lstrip().startswith("✓")]
        self.assertTrue(done)
        self.assertTrue(all(re.match(r"\s+✓ .+ \d+/\d+ · \d+ с(?: · \$\d+\.\d\d)?", ln) for ln in done), done[:3])
        self.assertTrue(any("нарезка 1/1" in ln for ln in done))
        self.assertTrue(any("проверка требований 3/3" in ln for ln in done))
        self.assertTrue(any(ln.lstrip().startswith("✗") for ln in lines))   # the `tests` stage has no script: shown as failed

    def test_cli_quiet_hides_progress_but_not_the_result(self):
        d = self.doc()
        self.cli("-i", d, "-o", str(self.dir / "a.md"), backend=FakeBackend(tp.make_script(), concurrency=1))
        self.assertIn("✓ нарезка", self.text)
        self.lines.clear()
        self.cli("-q", "-i", d, "-o", str(self.dir / "b.md"), backend=FakeBackend(tp.make_script(), concurrency=1))
        self.assertNotIn("✓", self.text)
        self.assertNotIn("[1/9]", self.text)
        self.assertIn("Итог:", self.text)


class UncheckedPairsTests(Base):
    def test_warning_counts_pairs(self):
        script = tp.make_script(review_clarity=LLMBadOutput("упало"))
        res, _ = self.run_fake(script, path=self.doc())
        w = next(x for x in res.run.warnings if x.startswith("Без оценки модели остались"))
        m = re.search(r"остались (\d+) из (\d+) пар «требование × критерий» \(затронуто (\d+) ", w)
        self.assertIsNotNone(m, w)
        n_req = len(res.requirements)
        self.assertEqual(int(m.group(2)), n_req * 7)
        base, _ = self.run_fake(tp.make_script(), path=self.doc())
        w0 = next(x for x in base.run.warnings if x.startswith("Без оценки модели остались"))
        n0 = int(re.search(r"остались (\d+) из", w0).group(1))
        self.assertGreater(int(m.group(1)), n0)            # the failed lens leaves more pairs unchecked than a healthy run
        self.assertIn("не проверены моделью (линза «ясность»)", " ".join(res.run.warnings))
        self.assertIn("❔", report_md.render(res))


class EffortLabelTests(Base):
    def test_passport_uses_effort_label(self):
        class Staged(FakeBackend):
            provider = "anthropic"

        b = Staged(tp.make_script(), concurrency=1)
        b.effort, b.effort_locked = "high", False       # «per stage» mode of the Anthropic backend
        res = pipeline.run(self.doc(), backend=b, depth="fast")
        self.assertEqual(res.run.effort, "по этапам")
        self.assertIn("effort по этапам", report_md.render(res))
        b2 = Staged(tp.make_script(), concurrency=1)
        b2.effort = "low"
        self.assertEqual(pipeline.run(self.doc(), backend=b2, depth="fast").run.effort, "low")


class EstimateThinkingTests(Base):
    def est(self, env):
        e = estimate.build(self.doc(), depth="full")
        return e, estimate.output_range(e, "anthropic", env)

    def test_effort_levels_change_the_range(self):
        _e, (lo_low, hi_low) = self.est({"ANTHROPIC_EFFORT": "low"})
        _e, (lo_high, hi_high) = self.est({"ANTHROPIC_EFFORT": "high"})
        self.assertGreater(lo_high, lo_low * 3)
        self.assertLess(lo_low, hi_low)

    def test_per_stage_defaults_and_overrides(self):
        _e, default = self.est({})
        _e, forced_high = self.est({"ANTHROPIC_EFFORT": "high"})
        _e, over = self.est({"ANTHROPIC_EFFORT_STAGES": "review=high,set=high,slice=high,skeptic=high,questions=high,tests=high"})
        self.assertLess(default[1], forced_high[1])
        self.assertEqual(over, forced_high)
        locked, o = estimate.stage_efforts({"ANTHROPIC_EFFORT": "medium", "ANTHROPIC_EFFORT_STAGES": "review=low"})
        self.assertEqual(estimate.effort_of("review_clarity", locked, o), "medium")   # a locked level wins
        locked, o = estimate.stage_efforts({"ANTHROPIC_EFFORT_STAGES": "review=low"})
        self.assertEqual(estimate.effort_of("review_clarity", locked, o), "low")
        self.assertEqual(estimate.effort_of("set_consistency", locked, o), "high")

    def test_report_lines(self):
        e = estimate.build(self.doc(), depth="full")
        text = "\n".join(estimate.report(e, provider="anthropic", model="claude-opus-5-5", depth="full", env={}))
        self.assertIn("рассуждения оплачиваются как выход", text)
        self.assertRegex(text, r"Выход: ≈ [\d\s]+ – [\d\s]+ токенов")
        self.assertRegex(text, r"Время: ≈ \d+ – \d+ мин")


if __name__ == "__main__":
    unittest.main()
