"""Shared helpers for the dialog, test-draft and baseline tests (FakeBackend, no network)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from revizor import answers as ans_mod, cli, pipeline
from revizor.llm import FakeBackend, LLMBadOutput

from tests import test_pipeline as tp

AGENT = Path(__file__).resolve().parents[1]
REQ_DOC = str(AGENT / "docs" / "requirements.md")
ENV = {"REVIZOR_NO_DOTENV": "1", "PATH": os.environ.get("PATH", "")}


def quiet_script(**over):
    """A model that finds nothing by itself: the findings come from the rules, the rest is overridable."""
    base = {"review": {"reviews": []}, "review_clarity": {"reviews": []}, "review_completeness": {"reviews": []},
            "review_correctness": {"reviews": []}, "set_consistency": {"findings": []},
            "set_completeness": {"findings": []}, "set_standards": {"findings": []},
            "skeptic": lambda user: tp.skeptic_answer(user, reject_titles=(), reject_all=False),
            "questions": LLMBadOutput("нет сценария questions"), "tests": LLMBadOutput("нет сценария tests"),
            "answers": LLMBadOutput("нет сценария answers")}
    base.update(over)
    return tp.make_script(**base)


def provider_from_text(text: str):
    return lambda questions: ans_mod.parse(text, questions)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("REVIZOR_NO_DOTENV")
        os.environ["REVIZOR_NO_DOTENV"] = "1"
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.lines: list[str] = []

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("REVIZOR_NO_DOTENV", None)
        else:
            os.environ["REVIZOR_NO_DOTENV"] = self._old
        self.tmp.cleanup()

    def doc(self, text: str = tp.DOC, name: str = "doc.md") -> str:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def cli(self, *argv: str, backend=None, input_fn=None) -> int:
        return cli.main(list(argv), env=dict(ENV), out=self.lines.append, backend=backend, input_fn=input_fn)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def run_offline(self, path: str = REQ_DOC, **kw):
        return pipeline.run(path, backend=None, depth="full", **kw)

    def run_fake(self, script=None, path: str = REQ_DOC, depth: str = "full", **kw):
        b = FakeBackend(script or quiet_script(), concurrency=1)
        return pipeline.run(path, backend=b, depth=depth, **kw), b
