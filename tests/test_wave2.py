"""Wave 2: agent version with the commit, the document's own review checklist answered, prompt reminders, CSV export."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from revizor import checklist, cli, config, estimate, loader, pipeline, prompts, report_json, report_md, sidefiles
from revizor.llm import FakeBackend, LLMBadOutput
from revizor.llm.base import STAGE_EFFORT
from revizor.model import RunInfo

from tests import test_pipeline as tp

CHECKLIST_TABLE = """
## Ревью требований

| Проверка | Выполнено | Примечание |
| :--- | :--- | :--- |
| Требование можно протестировать | Да / Нет | |
| Есть критерии приёмки | Да / Нет / Не требуется | |
| Описаны сценарии ошибок | Да / Нет | |
"""
DOC_CL = tp.DOC + CHECKLIST_TABLE

CHECKBOX_DOC = """# Сервис уведомлений

Система должна отправлять клиенту уведомление о каждой операции по счёту не позднее 10 секунд после проведения.

## Чек-лист

- [ ] Требования согласованы с архитектором
- [ ] Описаны сценарии ошибок
- ☐ Есть критерии приёмки
"""

ORDINARY_DOC = """# Справочник полей

Система должна принимать платёж с полями, перечисленными в таблице ниже, и проверять каждое поле.

| Поле | Тип | Статус | Описание |
| :--- | :--- | :--- | :--- |
| amount | число | | Сумма платежа в тенге |
| currency | строка | | Код валюты |
| account | строка | | Номер счёта получателя |
| comment | строка | | Комментарий клиента |

- [x] Готово
- Пункт без отметки
"""


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("REVIZOR_NO_DOTENV")
        os.environ["REVIZOR_NO_DOTENV"] = "1"
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("REVIZOR_NO_DOTENV", None)
        else:
            os.environ["REVIZOR_NO_DOTENV"] = self._old
        self.tmp.cleanup()

    def doc(self, text: str, name: str = "doc.md") -> str:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return str(p)

    def load(self, text: str):
        return loader.load(self.doc(text))


# --- task 1 ---------------------------------------------------------------------------------------------------


class _Out:
    def __init__(self, stdout: str, rc: int = 0):
        self.stdout, self.returncode = stdout, rc


class VersionTests(_Tmp):
    def test_commit_from_runner(self):
        calls = []

        def runner(cmd, **kw):
            calls.append((cmd, kw))
            return _Out("abc1234\n")

        self.assertEqual(config.agent_commit(self.dir, runner=runner), "abc1234")
        cmd, kw = calls[0]
        self.assertEqual(cmd, ["git", "-C", str(self.dir), "rev-parse", "--short", "HEAD"])
        self.assertEqual(kw.get("timeout"), 2)
        self.assertTrue(kw.get("capture_output"))

    def test_git_absent_or_failing_gives_empty(self):
        def missing(cmd, **kw):
            raise FileNotFoundError("git")

        def timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 2)

        self.assertEqual(config.agent_commit(self.dir, runner=missing), "")
        self.assertEqual(config.agent_commit(self.dir, runner=timeout), "")
        self.assertEqual(config.agent_commit(self.dir, runner=lambda c, **k: _Out("", 128)), "")
        self.assertEqual(config.agent_commit(self.dir, runner=lambda c, **k: _Out("fatal: not a git repository")), "")

    def test_no_work_tree_starts_no_process(self):
        if config._inside_git(self.dir):
            self.skipTest("the temporary folder is inside a git work tree")
        self.assertEqual(config.agent_commit(self.dir), "")

    def test_label(self):
        self.assertEqual(config.version_label("1.0.0", "abc1234", "1.0"),
                         "v1.0.0 · коммит `abc1234` · набор правил 1.0")
        self.assertEqual(config.version_label("1.0.0", "", "1.0"),
                         "v1.0.0 · коммит не определён (папка вне git) · набор правил 1.0")
        self.assertEqual(config.version_label("1.0.0 (v1.0.0-3-gabc)", ""), "v1.0.0 · коммит не определён (папка вне git)")

    def test_passport_and_json(self):
        res = pipeline.run(self.doc(tp.DOC), backend=None)
        res.run.agent_commit = "abc1234"
        res.run.agent_tag, res.run.agent_dirty = "", False  # independent of the real checkout's tag and edits
        text = report_md.render(res)
        self.assertIn("| Версия агента | v" + res.run.agent_version.split(" ")[0]
                      + " · коммит `abc1234` · набор правил ", text)
        data = json.loads(report_json.render(res))
        self.assertEqual(data["run"]["agent_commit"], "abc1234")
        self.assertEqual(data["run"]["agent_version"], res.run.agent_version)
        res.run.agent_commit = ""
        self.assertIn("коммит не определён (папка вне git)",
                      report_md.render(res).split("| Версия агента |", 1)[1].split("\n", 1)[0])

    def test_default_field(self):
        self.assertEqual(RunInfo().agent_commit, "")


# --- task 2 ---------------------------------------------------------------------------------------------------


def checklist_answer(user: str, omit_last: bool = True) -> dict:
    nums = [json.loads(ln)["number"] for ln in user.split("ПУНКТЫ ЧЕК-ЛИСТА:\n", 1)[1].split("\n\nЗАМЕЧАНИЯ:", 1)[0]
            .splitlines() if ln.strip().startswith("{")]
    fids = tp._fids(user.split("ЗАМЕЧАНИЯ:\n", 1)[1])
    out = []
    for k, n in enumerate(nums):
        if omit_last and k == len(nums) - 1:
            continue
        out.append({"number": n, "verdict": "yes" if k == 0 else "no", "basis": f"Пункт {n}: так следует из текста.",
                    "findings": ([fids[0]] if fids else []) + ["D-999"]})
    return {"items": out}


class ChecklistDetectionTests(_Tmp):
    def test_table_form(self):
        items = checklist.detect(self.load(DOC_CL))
        self.assertEqual([i.text for i in items], ["Требование можно протестировать", "Есть критерии приёмки",
                                                   "Описаны сценарии ошибок"])
        self.assertEqual([i.number for i in items], [1, 2, 3])
        lines = self.load(DOC_CL).lines
        for it in items:
            self.assertIn(it.text, lines[it.line - 1])

    def test_checkbox_form(self):
        items = checklist.detect(self.load(CHECKBOX_DOC))
        self.assertEqual([i.text for i in items], ["Требования согласованы с архитектором", "Описаны сценарии ошибок",
                                                   "Есть критерии приёмки"])

    def test_empty_mark_column(self):
        text = tp.DOC + ("\n## Ревью\n\n| Пункт | Отметка |\n| :--- | :--- |\n| Сценарии описаны | |\n"
                         "| Ошибки описаны | |\n| Приёмка описана | |\n")
        self.assertEqual(len(checklist.detect(self.load(text))), 3)

    def test_ordinary_table_and_short_runs_are_silent(self):
        self.assertEqual(checklist.detect(self.load(ORDINARY_DOC)), [])
        self.assertEqual(checklist.detect(self.load(tp.DOC)), [])
        two = tp.DOC + "\n## Ревью\n\n- [ ] Первый пункт\n- [ ] Второй пункт\n"
        self.assertEqual(checklist.detect(self.load(two)), [])

    def test_chunks(self):
        rows = "".join(f"| Пункт проверки номер {k} | Да / Нет |\n" for k in range(1, 31))
        text = tp.DOC + "\n## Ревью\n\n| Проверка | Отметка |\n| :--- | :--- |\n" + rows
        items = checklist.detect(self.load(text))
        self.assertEqual(len(items), 30)
        self.assertEqual([len(c) for c in checklist.chunks(items)], [25, 5])


class ChecklistPipelineTests(_Tmp):
    def run_fake(self, text=DOC_CL, **over):
        b = FakeBackend(tp.make_script(**over), concurrency=1)
        return pipeline.run(self.doc(text), backend=b, depth="full"), b

    def test_model_answers_applied_and_unknown_ids_removed(self):
        res, b = self.run_fake(checklist=checklist_answer)
        self.assertEqual([s for s, _, _ in b.log].count("checklist"), 1)
        user = next(u for s, _, u in b.log if s == "checklist")
        self.assertIn("Требование можно протестировать", user)
        self.assertNotIn("<document-", user)  # the document itself sits in the shared system text
        cl = res.checklist
        self.assertEqual([i.verdict for i in cl], ["yes", "no", "unknown"])
        self.assertTrue(cl[0].by_model)
        self.assertEqual(cl[0].basis, "Пункт 1: так следует из текста.")
        known = {f.fid for f in res.findings}
        for it in cl:
            self.assertNotIn("D-999", it.findings)
            self.assertTrue(set(it.findings) <= known)
        self.assertTrue(cl[0].findings)
        self.assertEqual(cl[2].basis, checklist.MISSING_BASIS)
        self.assertTrue(any(a.get("what") == "checklist_unknown_ids_removed" for a in res.audit))

    def test_section_and_json(self):
        res, _ = self.run_fake(checklist=checklist_answer)
        text = report_md.render(res)
        self.assertIn("### Чек-лист ревью из документа: как на него отвечает проверка", text)
        self.assertIn("| № | Пункт (цитата, строка) | Ответ агента (Да / Нет / Нельзя определить по тексту) | Основание | "
                      "Замечания |", text)
        self.assertIn("подсказка для проверяющего, а не его подпись", text)
        sec4 = text.split('<a id="s4"></a>', 1)[1].split('<a id="s5"></a>', 1)[0]
        self.assertIn("Чек-лист ревью из документа", sec4)
        self.assertIn("| 1 | «Требование можно протестировать» (строка ", text)
        self.assertIn("| Нельзя определить по тексту |", text)
        fid = res.checklist[0].findings[0]
        self.assertIn(f"[{fid}](#", text.split("Чек-лист ревью из документа", 1)[1])
        data = json.loads(report_json.render(res))
        self.assertEqual([i["verdict"] for i in data["checklist"]], ["yes", "no", "unknown"])
        self.assertEqual(data["checklist"][0]["text"], "Требование можно протестировать")

    def test_verdict_and_findings_unchanged(self):
        answered, _ = self.run_fake(checklist=checklist_answer)
        failed, _ = self.run_fake(checklist=LLMBadOutput("нет ответа"))
        self.assertEqual(answered.verdict, failed.verdict)
        self.assertEqual(answered.run.score, failed.run.score)
        self.assertEqual([(f.fid, f.severity, f.title) for f in answered.findings],
                         [(f.fid, f.severity, f.title) for f in failed.findings])
        self.assertEqual([i.verdict for i in failed.checklist], ["unknown"] * 3)
        self.assertEqual({i.basis for i in failed.checklist}, {checklist.OFFLINE_BASIS})
        self.assertTrue(any("чек-лист" in w for w in failed.run.warnings))

    def test_offline_fallback(self):
        res = pipeline.run(self.doc(DOC_CL), backend=None)
        self.assertEqual(len(res.checklist), 3)
        self.assertEqual({i.verdict for i in res.checklist}, {"unknown"})
        self.assertEqual({i.basis for i in res.checklist}, {"без модели не оценивается"})
        text = report_md.render(res)
        self.assertIn("Чек-лист ревью из документа", text)
        self.assertIn("модель на пункты не отвечала", text)

    def test_silent_without_checklist(self):
        res, b = self.run_fake(text=tp.DOC, checklist=checklist_answer)
        self.assertNotIn("checklist", [s for s, _, _ in b.log])
        self.assertEqual(res.checklist, [])
        self.assertNotIn("Чек-лист ревью из документа", report_md.render(res))
        self.assertEqual(json.loads(report_json.render(res))["checklist"], [])

    def test_cost_cap_gives_unknown(self):
        b = tp.PricedFake(tp.make_script(checklist=checklist_answer), model="claude-opus-5-5", concurrency=1)
        res = pipeline.run(self.doc(DOC_CL), backend=b, max_cost_usd=0.000001)
        self.assertNotIn("checklist", [s for s, _, _ in b.log])
        self.assertEqual(len(res.checklist), 3)
        self.assertEqual({i.verdict for i in res.checklist}, {"unknown"})

    def test_wired_everywhere(self):
        self.assertEqual(STAGE_EFFORT["checklist"], "low")
        self.assertIn("checklist", prompts.SCHEMAS)
        self.assertIn("checklist", prompts.MAX_TOKENS)
        self.assertLessEqual(prompts.MAX_TOKENS["checklist"], 8000)
        self.assertIn("checklist", pipeline.STAGE_GROUP)
        self.assertIn("checklist", report_md.STAGE_RU)
        self.assertIn("checklist", estimate.STAGE_MULT)
        est = estimate.build(self.doc(DOC_CL), depth="full")
        self.assertEqual([c.stage for c in est.calls].count("checklist"), 1)
        est2 = estimate.build(self.doc(tp.DOC), depth="full")
        self.assertNotIn("checklist", [c.stage for c in est2.calls])
        arch = (Path(config.AGENT_DIR) / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        self.assertIn("`checklist`", arch)

    def test_schema_tolerates_bad_items(self):
        from revizor.llm.schema import validate_ex
        out, errors, _n = validate_ex({"items": [{"number": "2", "verdict": "Да"}, {"verdict": "yes"}]},
                                      prompts.CHECKLIST_SCHEMA)
        self.assertEqual(errors, [])
        self.assertEqual(out["items"], [{"number": 2, "verdict": "unknown", "basis": "", "findings": []}])


# --- task 3 ---------------------------------------------------------------------------------------------------


class ReminderTests(unittest.TestCase):
    def test_set_completeness_reminder(self):
        head = prompts.SET_HEADS["set_completeness"]
        for part in ("шаг (кратность) задаваемых сумм и значений",
                     "несколько однотипных объектов у одного клиента (карты, счета, устройства)",
                     "операция, которая относится к уже закрытому периоду (возврат, отмена, корректировка задним числом)",
                     "синхронность взаимодействия (вызывающая сторона ждёт результат или получает его позже) и что "
                     "она видит до завершения",
                     "только если сам документ делает его необходимым"):
            self.assertIn(part, head)

    def test_completeness_lens(self):
        lens = prompts.LENS_BY_STAGE["review_completeness"]
        self.assertIn("граница диапазона (включена ли она), шаг значения, значение по умолчанию", lens.instruction)


# --- task 4 ---------------------------------------------------------------------------------------------------


class CsvTests(_Tmp):
    def rows(self, text: str) -> list[dict]:
        self.assertTrue(text.startswith("﻿"))
        return list(csv.DictReader(io.StringIO(text[1:])))

    def test_columns_rows_and_no_observations(self):
        res, _ = ChecklistPipelineTests.run_fake(self, text=tp.DOC)
        text = sidefiles.findings_csv(res)
        self.assertIn("\r\n", text)
        rows = self.rows(text)
        self.assertEqual(list(rows[0].keys()), list(sidefiles.CSV_COLUMNS))
        counted = [f for f in res.findings if f.status == "open" and not f.advisory]
        self.assertEqual([r["id"] for r in rows], [f.fid for f in counted])
        adv = {f.fid for f in res.findings if f.advisory}
        self.assertFalse(adv & {r["id"] for r in rows})
        first = next(r for r in rows if r["requirement_ids"] == "REQ-001")
        self.assertEqual(first["doc_ids"], "Ф-1")
        self.assertTrue(first["lines"])
        self.assertTrue(first["quote"])

    def test_formula_cells_are_neutralised(self):
        self.assertEqual(sidefiles._csv_safe("=1+1"), "'=1+1")
        self.assertEqual(sidefiles._csv_safe("@cmd"), "'@cmd")
        self.assertEqual(sidefiles._csv_safe("Обычный текст"), "Обычный текст")

    def test_cli_writes_csv_next_to_report(self):
        out = self.dir / "r" / "report.md"
        lines: list[str] = []
        code = cli.main(["-i", self.doc(tp.DOC), "-o", str(out), "--provider", "offline"], env={"REVIZOR_NO_DOTENV": "1"},
                        out=lines.append)
        self.assertEqual(code, 0)
        p = out.with_name("report.findings.csv")
        self.assertTrue(p.is_file())
        raw = p.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(str(p), "\n".join(lines))

    def test_write_failure_is_a_note(self):
        out = self.dir / "report.md"
        (self.dir / "report.findings.csv").mkdir()  # a folder where the file should go
        lines: list[str] = []
        code = cli.main(["-i", self.doc(tp.DOC), "-o", str(out), "--provider", "offline"], env={"REVIZOR_NO_DOTENV": "1"},
                        out=lines.append)
        self.assertEqual(code, 0)
        self.assertTrue(out.is_file())
        self.assertTrue(any("report.findings.csv" in ln and "Не удалось записать" in ln for ln in lines))

    def test_render_failure_is_a_note_in_the_report(self):
        out = self.dir / "report.md"
        old = sidefiles.findings_csv
        sidefiles.findings_csv = lambda _r: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            code = cli.main(["-i", self.doc(tp.DOC), "-o", str(out), "--provider", "offline"],
                            env={"REVIZOR_NO_DOTENV": "1"}, out=lambda _s: None)
        finally:
            sidefiles.findings_csv = old
        self.assertEqual(code, 0)
        self.assertIn("Таблица замечаний в CSV не записана", out.read_text(encoding="utf-8"))

    def test_diagnostic_has_no_csv(self):
        out = self.dir / "report.md"
        cli.main(["-i", str(self.dir / "missing.md"), "-o", str(out), "--provider", "offline"],
                 env={"REVIZOR_NO_DOTENV": "1"}, out=lambda _s: None)
        self.assertTrue(out.is_file())
        self.assertFalse(out.with_name("report.findings.csv").exists())


if __name__ == "__main__":
    unittest.main()
