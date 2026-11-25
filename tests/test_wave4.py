"""Wave 4: the critic pass «чего не хватает», four rules, the impact of each question, the requirement matrix.
Offline only: FakeBackend, invented small samples."""
from __future__ import annotations

import json
import re
from pathlib import Path

from revizor import gapcheck, impact, loader, pipeline, prompts, report_json, report_md, rules, sidefiles, slicer
from revizor.llm import FakeBackend, LLMBadOutput
from revizor.model import Finding, Question, Quote, Requirement

from tests import test_pipeline as tp

AGENT = Path(__file__).resolve().parents[1]


def sent_ids(user: str) -> list[str]:
    tail = user.split("КОНТРОЛЬНЫЕ ВОПРОСЫ:\n", 1)[1].split("\n\nСПИСОК ТРЕБОВАНИЙ", 1)[0]
    return re.findall(r"^([A-Z]{2}\d) ·", tail, re.M)


def all_na(user: str, **special) -> dict:
    out = []
    for i in sent_ids(user):
        out.append(special.get(i) or {"id": i, "status": "na", "checked": ["—"], "note": "нет предмета"})
    return {"items": out}


def gap_item(iid: str, title: str, quote: str, severity: str = "MAJOR") -> dict:
    return {"id": iid, "status": "gap", "checked": ["шаг 1", "шаг 2"], "quote": "", "note": "не описано",
            "findings": [{"severity": severity, "title": title, "problem": "Не описано, что делать при сбое.",
                          "quotes": [quote], "req_ids": [], "recommendation": "Опишите.",
                          "question": {"text": "Что делать при сбое?", "options": ["Повтор", "Отказ"]}}]}


class Base(tp._Base):
    def run_gaps(self, gaps, text=tp.DOC, depth="full", **over):
        script = tp.make_script(set_gaps=gaps, **over)
        return self.run_fake(script=script, text=text, depth=depth)

    def cell(self, res, iid):
        return next(c for c in res.gap_map if c.id == iid)


class Checklist(Base):
    def test_35_items_in_groups_generic(self):
        self.assertEqual(len(gapcheck.ITEMS), 35)
        self.assertEqual({it.group for it in gapcheck.ITEMS}, set(gapcheck.GROUPS))
        self.assertEqual([it.id for it in gapcheck.ITEMS if it.needs == "api"], ["IF1", "IF2", "IF3"])
        self.assertEqual(gapcheck.GROUPS.index("Данные и экран"), gapcheck.GROUPS.index("Интерфейсы между системами") + 1)
        for it in gapcheck.ITEMS:
            self.assertIn(it.max_severity, ("MAJOR", "MINOR"))
            for r in it.rule_ids:
                self.assertIn(r, rules.RULES)
        self.assertIn("КАЖДУЮ недостающую", prompts.GAPS_HEAD)
        self.assertIn("CRITICAL в этом шаге не ставь", prompts.GAPS_HEAD)


class CriticPass(Base):
    def test_all_cells_returned_no_follow_up(self):
        res, b = self.run_gaps(lambda u: all_na(u))
        self.assertEqual(sum(1 for s, _, _ in b.log if s == "set_gaps"), 1)
        self.assertEqual(len(res.gap_map), 35)
        self.assertTrue(all(c.status == "na" for c in res.gap_map))
        self.assertEqual(self.cell(res, "IF1").note, gapcheck.NO_API_NOTE)

    def test_api_items_not_sent_without_interface(self):
        _res, b = self.run_gaps(lambda u: all_na(u))
        user = next(u for s, _, u in b.log if s == "set_gaps")
        self.assertNotIn("IF1", sent_ids(user))
        self.assertIn("IF4", sent_ids(user))
        self.assertEqual(len(sent_ids(user)), 32)

    def test_missing_ids_one_follow_up_then_unchecked(self):
        calls = []

        def gaps(user):
            ids = sent_ids(user)
            calls.append(ids)
            if len(calls) == 1:
                return {"items": [{"id": i, "status": "na"} for i in ids if i not in ("ST1", "ST2")]}
            return {"items": [{"id": "ST1", "status": "na"}]}
        res, b = self.run_gaps(gaps)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], ["ST1", "ST2"])
        self.assertEqual(self.cell(res, "ST1").status, "na")
        self.assertEqual(self.cell(res, "ST2").status, "unchecked")

    def test_unknown_id_dropped_and_unconfirmed_quote_unverified(self):
        extra = {"ST1": {"id": "ST1", "status": "covered", "quote": "Этой фразы в документе нет вовсе", "note": "x"},
                 "ST2": {"id": "ST2", "status": "covered", "quote": "Клиент может выключить уведомления"}}

        def gaps(user):
            a = all_na(user, **extra)
            a["items"].append({"id": "ZZ9", "status": "gap"})
            return a
        res, _ = self.run_gaps(gaps)
        self.assertNotIn("ZZ9", [c.id for c in res.gap_map])
        self.assertTrue(any(a.get("what") == "gap_unknown_id" for a in res.audit))
        self.assertEqual(self.cell(res, "ST1").status, "unverified")
        st2 = self.cell(res, "ST2")
        self.assertEqual(st2.status, "covered")
        self.assertGreater(st2.line, 0)
        md = report_md.render(res)
        self.assertIn(f"✅ описано — строка {st2.line}", md)
        self.assertIn("? цитата не подтверждена", md)

    def test_severity_capped_never_critical(self):
        extra = {"CN2": gap_item("CN2", "Перечни систем расходятся", "Сервис должен отправлять push клиенту", "CRITICAL"),
                 "NP4": gap_item("NP4", "Нет текстов уведомления", "Клиент задаёт порог в тенге", "CRITICAL")}
        res, _ = self.run_gaps(lambda u: all_na(u, **extra))
        by_gap = {g: f for f in res.findings for g in f.gap_ids}
        self.assertEqual(by_gap["CN2"].severity, "MINOR")
        self.assertEqual(by_gap["CN2"].criterion, "set_consistency")
        self.assertEqual(by_gap["NP4"].severity, "MAJOR")
        self.assertEqual(by_gap["NP4"].blocker_reason, "")
        self.assertFalse(any(f.severity == "CRITICAL" for f in res.findings))
        self.assertEqual(self.cell(res, "CN2").status, "gap")
        self.assertIn(by_gap["CN2"].fid, self.cell(res, "CN2").finding_ids)

    def test_gap_merged_into_other_stage_finding_stays_linked(self):
        extra = {"NP3": gap_item("NP3", "Нет сценария отказа сервиса push", "клиент получает один push")}
        res, _ = self.run_gaps(lambda u: all_na(u, **extra))
        cell = self.cell(res, "NP3")
        self.assertEqual(cell.status, "gap")
        self.assertEqual(len(cell.finding_ids), 1)
        f = next(x for x in res.findings if x.fid == cell.finding_ids[0])
        self.assertEqual(f.stage, "set_completeness")  # the other stage's finding survived the merge
        self.assertIn("NP3", f.gap_ids)

    def test_rejected_by_skeptic(self):
        extra = {"VL3": gap_item("VL3", "Слабое замечание о часовом поясе", "Клиент задаёт порог в тенге")}
        res, _ = self.run_gaps(lambda u: all_na(u, **extra))
        cell = self.cell(res, "VL3")
        self.assertEqual(cell.status, "rejected")
        self.assertIn("✅ пробел не подтвердился при перепроверке", report_md.render(res))

    def test_gap_with_lost_quotes_is_unverified(self):
        extra = {"VL1": gap_item("VL1", "Нет шага", "этого текста нет в документе совсем")}
        res, _ = self.run_gaps(lambda u: all_na(u, **extra))
        self.assertEqual(self.cell(res, "VL1").status, "unverified")

    def test_stage_failure_does_not_break_the_run(self):
        res, _ = self.run_gaps(LLMBadOutput("сломалось"))
        self.assertIsNone(res.diagnostic)
        self.assertEqual(len(res.gap_map), 35)
        self.assertTrue(all(c.status in ("unchecked", "na") for c in res.gap_map))
        self.assertTrue(any("Проход-критик" in w for w in res.run.warnings))
        self.assertIn("Карта полноты", report_md.render(res))

    def test_fast_mode_runs_the_stage(self):
        _res, b = self.run_gaps(lambda u: all_na(u), depth="fast")
        self.assertIn("set_gaps", [s for s, _, _ in b.log])

    def test_section_summary_and_json(self):
        extra = {"NP4": gap_item("NP4", "Нет текстов уведомления", "клиент получает один push")}
        res, _ = self.run_gaps(lambda u: all_na(u, **extra))
        md = report_md.render(res)
        self.assertIn("### Карта полноты: контрольные вопросы к документу", md)
        self.assertIn("Агент обязан ответить на каждый из 35 вопросов", md)
        self.assertLess(md.index("Карта полноты"), md.index('<a id="s4-obs">'))
        self.assertIn("Вопросы для службы безопасности и комплаенса", md)
        self.assertRegex(md, r"\| Контрольных вопросов к документу \| 35, из них пробелов 1 \|")
        self.assertIn("⚠️ пробел — [D-", md)
        data = json.loads(report_json.render(res))
        self.assertEqual(len(data["gap_map"]), 35)
        self.assertEqual(set(data["gap_map"][0]), {"id", "group", "title", "status", "line", "finding_ids", "checked", "note"})
        # the map itself never changes the verdict: same findings, same verdict
        self.assertIn(res.verdict, ("CONDITIONAL", "BLOCKED", "READY"))


FLOW_DOC = """# Уведомления о покупках

## Требования

- Ф-1. Система отправляет уведомление после покупки.
- Ф-2. Клиент выключает уведомления в настройках.
- Ф-3. Система хранит настройку клиента.

## Схема процесса

Покупка → Сервис расходов → Проверка порога → Уведомление
"""


class OfflineMap(Base):
    def test_offline_rule_answered_cells(self):
        res = pipeline.run(self.doc(FLOW_DOC))
        cell = self.cell(res, "NP2")
        self.assertEqual(cell.status, "gap")
        self.assertEqual(cell.note, gapcheck.RULE_NOTE)
        self.assertEqual(self.cell(res, "ST1").status, "unchecked")
        self.assertEqual(self.cell(res, "ST1").note, "без модели не оценивается")
        md = report_md.render(res)
        self.assertIn("Карта полноты", md)
        self.assertIn("| NP2. Ветки ошибок на схемах | ⚠️ пробел — [D-", md)

    def test_rule_cell_overrides_model_covered(self):
        res, _ = self.run_gaps(lambda u: all_na(u, NP2={"id": "NP2", "status": "covered",
                                                        "quote": "Покупка → Сервис расходов"}), text=FLOW_DOC)
        self.assertEqual(self.cell(res, "NP2").status, "gap")


PAD = ("\n## Пояснения\n\nДокумент описывает небольшую доработку мобильного приложения для клиентов банка. "
       "Сроки и ответственные согласуются отдельно.\n")


def _run_rules(text: str, tmpdir: Path):
    p = tmpdir / "r.md"
    p.write_text(text + PAD, encoding="utf-8")
    d = loader.load(str(p))
    reqs, skipped = slicer.heuristic_slice(d)
    return rules.run_rules(d, reqs, skipped)


class NewRules(tp._Base):
    def ids(self, text):
        return [r for f in _run_rules(text, self.dir) for r in f.rule_ids]

    def test_registry_and_version(self):
        self.assertEqual(rules.RULESET_VERSION, "1.1")
        self.assertEqual(rules.rule_counts(), (27, 8))
        for path in ("docs/ARCHITECTURE.md", "README.md"):
            t = (AGENT / path).read_text(encoding="utf-8")
            self.assertIn("27 основн", t, path)

    def test_flow_happy_path(self):
        self.assertIn("flow-happy-path", self.ids(FLOW_DOC))
        mermaid = FLOW_DOC.replace("Покупка → Сервис расходов → Проверка порога → Уведомление",
                                   "```mermaid\nsequenceDiagram\n  App->>API: POST /x\n  API-->>App: 200\n```")
        self.assertIn("flow-happy-path", self.ids(mermaid))
        with_err = FLOW_DOC + "Если сервис недоступен → повтор через 1 минуту.\n"
        self.assertNotIn("flow-happy-path", self.ids(with_err))
        alt = mermaid.replace("  API-->>App: 200\n", "  alt ошибка\n  API-->>App: 500\n  end\n")
        self.assertNotIn("flow-happy-path", self.ids(alt))
        data = FLOW_DOC.replace("## Схема процесса", "## Схема данных")
        self.assertNotIn("flow-happy-path", self.ids(data))
        json_head = FLOW_DOC.replace("## Схема процесса", "## Схема процесса: JSON-схема ответа")
        self.assertNotIn("flow-happy-path", self.ids(json_head))
        plain = FLOW_DOC.replace("Покупка → Сервис расходов → Проверка порога → Уведомление",
                                 "Процесс описан в разделе требований.")
        self.assertNotIn("flow-happy-path", self.ids(plain))

    def test_flow_image_only_is_minor(self):
        img = FLOW_DOC.replace("Покупка → Сервис расходов → Проверка порога → Уведомление", "![схема](flow.png)")
        found = [f for f in _run_rules(img, self.dir) if "flow-happy-path" in f.rule_ids]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "MINOR")
        self.assertIn("картинкой", found[0].title)
        mixed = img + "\nПокупка → Уведомление\n"
        self.assertFalse(any("картинкой" in f.title for f in _run_rules(mixed, self.dir)))

    API = """# Сервис лимитов

## Требования

- Ф-1. Клиент сохраняет лимит.
- Ф-2. Система показывает лимит.
- Ф-3. Система хранит лимит.

## API

`PUT /limits/{id}` сохраняет лимит. Ответ: код 200.
"""

    def test_api_reliability(self):
        found = [f for f in _run_rules(self.API, self.dir) if "api-reliability" in f.rule_ids]
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual(f.severity, "MINOR")
        self.assertIn("идемпотентность", f.problem)
        self.assertIn("PUT /limits", f.quotes[0].text)
        self.assertEqual(f.question.addressee, "architect")
        full = self.API + ("\nВерсия API: /v1/. Лимит запросов: 10 в секунду, сверх лимита — код 429. Тайм-аут 2 с. "
                           "Запрос идемпотентен. Одновременные изменения отклоняются кодом 409.\n")
        self.assertNotIn("api-reliability", self.ids(full))
        one_missing = self.API + "\nВерсия API: /v1/. Код 429. Тайм-аут 2 с. Запрос идемпотентен.\n"
        self.assertNotIn("api-reliability", self.ids(one_missing))
        read_only = self.API.replace("`PUT /limits/{id}` сохраняет", "`GET /limits/{id}` возвращает")
        self.assertNotIn("api-reliability", self.ids(read_only))
        self.assertNotIn("api-reliability", self.ids(FLOW_DOC))

    def test_title_mismatch(self):
        doc = ("# Лимиты расходов по карте\n\n| Параметр | Значение |\n| --- | --- |\n"
               "| Наименование задачи | Кэшбэк на заправках партнёров |\n\n## Требования\n\n- Ф-1. Клиент задаёт лимит.\n")
        found = [f for f in _run_rules(doc, self.dir) if "title-mismatch" in f.rule_ids]
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0].quotes), 2)
        same = doc.replace("Кэшбэк на заправках партнёров", "Лимиты расходов по банковской карте")
        self.assertNotIn("title-mismatch", self.ids(same))
        generic = doc.replace("# Лимиты расходов по карте", "# Техническое задание")
        self.assertNotIn("title-mismatch", self.ids(generic))
        inline = ("# Лимиты расходов по карте\n\nНазвание проекта: Кэшбэк на заправках партнёров\n\n## Требования\n\n"
                  "- Ф-1. Клиент задаёт лимит.\n")
        self.assertIn("title-mismatch", self.ids(inline))
        self.assertNotIn("title-mismatch", self.ids(FLOW_DOC))

    def test_missing_ids_quotes_headings(self):
        doc = ("# Док\n\n## Раздел А\n\n- Клиент задаёт лимит.\n- Система хранит лимит.\n\n## Раздел Б\n\n"
               "- Система показывает лимит.\n- Система удаляет лимит.\n\n## Раздел В\n\n- Система пишет журнал.\n")
        f = next(f for f in _run_rules(doc, self.dir) if "missing-ids" in f.rule_ids)
        texts = [q.text for q in f.quotes]
        for h in ("Раздел А", "Раздел Б", "Раздел В"):
            self.assertTrue(any(h in t for t in texts), (h, texts))


def _f(fid, sev, rid, **kw):
    q = Quote(text="x", line_start=1, line_end=1)
    return Finding(scope="requirement", criterion="completeness", severity=sev, title=fid, problem="", recommendation="",
                   quotes=[q], req_ids=[rid], fid=fid, **kw)


class Impact(tp._Base):
    def setUp(self):
        super().setUp()
        self.reqs = [Requirement(rid=f"REQ-00{k}", doc_id="", section="", kind="functional", quote="q", summary="",
                                 block_ids=[], line_start=k, line_end=k) for k in (1, 2)]
        self.fs = [_f("D-001", "CRITICAL", "REQ-001"), _f("D-002", "MAJOR", "REQ-002"), _f("D-003", "CRITICAL", "REQ-002")]

    def test_compute_and_order(self):
        qs = [Question(qid="В-01", text="a", why="", addressee="analyst", priority="important", finding_ids=["D-002"]),
              Question(qid="В-02", text="b", why="", addressee="analyst", priority="important", finding_ids=["D-001", "D-003"])]
        self.fs[1].question_id = "В-01"
        impact.apply(qs, self.fs, self.reqs, reorder=True, findings=self.fs)
        self.assertEqual([q.text for q in qs], ["b", "a"])  # more blockers closed first
        self.assertEqual(qs[0].qid, "В-01")
        self.assertEqual(self.fs[1].question_id, "В-02")  # the finding follows the renumbering
        im = qs[0].impact
        self.assertEqual(im["closes"], ["D-001", "D-003"])
        self.assertEqual(im["blockers_closed"], 2)
        self.assertEqual(im["verdict_after"], "CONDITIONAL")
        self.assertGreater(im["index_delta"], 0)
        self.assertEqual(set(im), {"closes", "blockers_closed", "verdict_after", "index_after", "index_delta"})
        self.assertEqual(qs[1].impact["verdict_after"], "BLOCKED")
        need, none = impact.blocker_questions(qs, self.fs)
        self.assertEqual(need, ["В-01"])
        self.assertEqual(none, [])

    def test_report_lines_and_head(self):
        res = pipeline.run(str(AGENT / "docs" / "requirements.md"))
        md = report_md.render(res)
        self.assertRegex(md, r"Что изменит ответ в итоге: закроет 1 из \d+ блокер\w*")
        # a line that changes nothing is not printed; an unchanged index is not printed as «54 → 54»
        self.assertNotIn("на вердикт не влияет\n", md)
        for a, b in re.findall(r"индекс готовности (\d+) → (\d+)", md):
            self.assertNotEqual(a, b)
        self.assertIn("Чтобы снять все блокеры, нужны ответы на вопросы: [В-01]", md)
        for q in res.questions:
            self.assertIn("blockers_closed", q.impact)
        data = json.loads(report_json.render(res))
        self.assertIn("impact", data["questions"][0])
        side = sidefiles.questions_md(res, input_path="i", output_path="o", questions_path="q")
        self.assertIn("Что изменит ответ в итоге:", side)
        # inside each priority group: more blockers first, then the bigger index gain
        for prio in ("blocker", "important", "nice"):
            keys = [(-q.impact["blockers_closed"], -q.impact["index_delta"]) for q in res.questions if q.priority == prio]
            self.assertEqual(keys, sorted(keys))

    def test_blockers_without_question(self):
        qs = [Question(qid="В-01", text="a", why="", addressee="analyst", priority="blocker", finding_ids=["D-001"])]
        need, none = impact.blocker_questions(qs, self.fs)
        self.assertEqual((need, none), (["В-01"], ["D-003"]))


class LensAndMatrix(tp._Base):
    def test_prompt_additions(self):
        lens = prompts.LENS_BY_STAGE["review_completeness"].instruction
        self.assertIn("через то, что получит или увидит внешний участник", lens)
        self.assertIn("оформи каждый отдельным замечанием, не останавливайся на первом", prompts.REVIEW_TAIL)

    def test_matrix_in_section_3(self):
        res, _ = self.run_fake()
        md = report_md.render(res)
        s3 = md.split('<a id="s3">', 1)[1].split('<a id="s4">', 1)[0]
        self.assertIn("Матрица «требование × критерий»", s3)
        self.assertLess(s3.index("Матрица"), s3.index('<a id="req-001">'))
        self.assertIn("| [REQ-001](#req-001) Ф-1 |", s3)
        self.assertRegex(s3, r"\[⚠️\]\(#d-\d{3}\)")
        self.assertRegex(s3, r"Пустых клеток \(не проверено\): \d+\.")
