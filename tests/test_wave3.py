"""Wave 3: requirement links after merges, code-owned counts in the verdict text, clarity bands, version label,
document title in the passport, masked values in Markdown, small fixes."""
from __future__ import annotations

import unittest

from pathlib import Path

from revizor import cli, config, merge, pipeline, prompts, report_json, report_md, score, structure, textfmt, verdict
from revizor.model import CriterionVerdict, Finding, Requirement, RequirementReview, Quote, RunInfo
from revizor.report_md import card
from tests import test_cli, test_pipeline, test_wave2

GOAL = "Клиент хочет узнать о достижении цели в момент оплаты"  # line 5, REQ-001
OTHER = "Настройка хранится в профиле клиента"  # line 9, REQ-005
ROW = "Адрес | тест: `/v1/saveItem` · боевой: —"  # line 50, REQ-020


def q(text: str, line: int, rid: str) -> Quote:
    return Quote(text=text, line_start=line, line_end=line, rid=rid)


def req(rid: str, line: int, text: str) -> Requirement:
    return Requirement(rid=rid, doc_id="", section="", kind="functional", quote=text, summary="", block_ids=[],
                       line_start=line, line_end=line)


def placeholder_finding() -> Finding:
    """Requirement-level clarity finding about an empty value in one table row."""
    return Finding(scope="requirement", criterion="unambiguity", severity="MAJOR",
                   title="Не ясно, есть ли адрес в боевом контуре",
                   problem="Прочерк можно понять как отсутствие метода или как незаданный адрес.",
                   recommendation="Указать адрес или написать, что метода нет.",
                   quotes=[q(ROW, 50, "REQ-020")], req_ids=["REQ-020"], stage="review_clarity")


def terminology_finding() -> Finding:
    """Set-level finding about a different defect that happens to quote a piece of the same row."""
    return Finding(scope="set", criterion="comprehensibility", severity="MINOR",
                   title="Одна настройка названа тремя разными словами",
                   problem="В целях, в интерфейсе и в имени метода одна сущность называется по-разному.",
                   recommendation="Выбрать один термин.",
                   quotes=[q("узнать о достижении цели", 5, "REQ-001"), q("тест: `/v1/saveItem`", 50, "REQ-020")],
                   req_ids=["REQ-001", "REQ-005", "REQ-020"], stage="set_consistency")


class RequirementLinksAfterMerge(unittest.TestCase):
    def test_other_topic_sharing_a_row_is_not_merged(self):
        audit: list[dict] = []
        out = merge.dedupe_model([placeholder_finding(), terminology_finding()], audit)
        self.assertEqual(len(out), 2)
        ph = next(f for f in out if f.scope == "requirement")
        self.assertEqual(ph.req_ids, ["REQ-020"])
        self.assertEqual([x.line_start for x in ph.quotes], [50])

    def test_same_topic_set_finding_still_merges(self):
        a = placeholder_finding()
        b = terminology_finding()
        b.title = "Не ясно, есть ли адрес в боевом контуре метода"
        out = merge.dedupe_model([a, b], [])
        self.assertEqual(len(out), 1)

    def test_merged_away_place_goes_to_see_also_not_to_links(self):
        target, other = placeholder_finding(), terminology_finding()
        merge.absorb(target, other)
        self.assertEqual(target.req_ids, ["REQ-020"])
        self.assertNotIn(5, [x.line_start for x in target.quotes])
        self.assertIn(5, [x.line_start for x in target.see_also])
        # the piece in the same row is a place the survivor already rests on
        self.assertIn("тест: `/v1/saveItem`", [x.text for x in target.quotes])

    def test_skeptic_merge_keeps_links_clean(self):
        a, b = placeholder_finding(), terminology_finding()
        a.fid, b.fid = "D-001", "D-002"
        merge.apply_skeptic([a, b], [{"fid": "D-002", "decision": "merge", "merge_into": "D-001"}],
                            injection=False, audit=[])
        self.assertEqual(b.status, "merged")
        self.assertEqual(a.req_ids, ["REQ-020"])

    def test_no_false_mark_on_a_clean_requirement(self):
        target, other = placeholder_finding(), terminology_finding()
        merge.absorb(target, other)
        reqs = [req("REQ-001", 5, GOAL), req("REQ-005", 9, OTHER), req("REQ-020", 50, ROW)]
        reviews = {r.rid: RequirementReview(rid=r.rid, criteria={"unambiguity": CriterionVerdict("ok")}, by_model=True)
                   for r in reqs}
        pipeline._finish_reviews(reviews, reqs, [target], True)
        self.assertEqual(reviews["REQ-001"].criteria["unambiguity"].status, "ok")
        self.assertEqual(reviews["REQ-005"].criteria["unambiguity"].status, "ok")
        self.assertEqual(reviews["REQ-020"].criteria["unambiguity"].status, "issue")
        self.assertEqual(score.clarity_of("REQ-001", [target]), 10.0)

    def test_rule_folded_keeps_all_its_places(self):
        model = placeholder_finding()
        rule = Finding(scope="requirement", criterion="completeness", severity="MINOR", title="Заглушка",
                       problem="", recommendation="", quotes=[q(ROW, 50, "REQ-020"), q(OTHER, 9, "REQ-005")],
                       req_ids=["REQ-020", "REQ-005"], source="rule:placeholder", rule_ids=["placeholder"])
        model.criterion = "completeness"
        merge.fold_rules([rule], [model], [])
        self.assertIn("REQ-005", model.req_ids)
        self.assertEqual(model.see_also, [])

    def test_card_lists_see_also(self):
        target, other = placeholder_finding(), terminology_finding()
        target.fid = "D-001"
        merge.absorb(target, other)
        text = card(target, show_req_links=True)
        self.assertIn("См. также", text)
        self.assertIn("строка 5", text)


# --- wave 4: merges decided by topic, the most telling quote first, rules folded into the same-topic finding ------

STEP = "Заменить настройку и вернуть результат сохранения"  # line 120, REQ-025
METHOD_ROW = "**REST type** | GET"  # line 104, REQ-017
CURL = 'curl -G "$BASE/alerts/saveLimit" --data \'cardId=demo-42&amount=50000\''  # line 109, REQ-021
DESC_ROW = "**Description** | Сохранение порога"  # line 103, REQ-016


def get_step_finding() -> Finding:
    """Requirement-level finding about the step that saves a setting through GET."""
    return Finding(scope="requirement", criterion="feasibility", severity="CRITICAL", blocker_reason="protocol",
                   title="Сохранение настройки выполняется методом GET",
                   problem="Шаг заменяет сохранённую настройку, а метод объявлен как GET: прокси и браузеры могут "
                           "повторить такой запрос.",
                   recommendation="Перевести метод на PUT.", quotes=[q(STEP, 120, "REQ-025")], req_ids=["REQ-025"],
                   stage="review_correctness")


def get_set_finding() -> Finding:
    """The same defect seen from the set level: the method row, the example request and the step."""
    return Finding(scope="set", criterion="set_feasibility", severity="MAJOR",
                   title="Сохранение порога выполняется методом GET",
                   problem="Метод сохранения объявлен как GET, хотя он меняет данные; прокси могут повторить запрос.",
                   recommendation="PUT вместо GET.",
                   quotes=[q(METHOD_ROW, 104, "REQ-017"), q(CURL, 109, "REQ-021"), q(STEP, 120, "REQ-025")],
                   req_ids=["REQ-017", "REQ-021", "REQ-025"], stage="set_standards")


def card_in_query_finding(quote_line: int = 109) -> Finding:
    """A neighbour at the same place about something else: the card id in the address."""
    return Finding(scope="set", criterion="set_feasibility", severity="MINOR",
                   title="Идентификатор карты передаётся в строке запроса",
                   problem="Номер карты виден в журналах серверов и в истории.",
                   recommendation="Передавать в теле.", quotes=[q(CURL, quote_line, "REQ-021")], req_ids=["REQ-021"],
                   stage="set_standards")


def get_rule_finding() -> Finding:
    return Finding(scope="set", criterion="set_feasibility", severity="CRITICAL", blocker_reason="protocol",
                   title="Метод GET используется для изменения данных",
                   problem="Запрос описан как GET, но он сохраняет данные. GET — безопасный метод, прокси вправе "
                           "повторять такие запросы.",
                   recommendation="PUT или PATCH.",
                   quotes=[q(METHOD_ROW, 104, "REQ-017"), q(DESC_ROW, 103, "REQ-016"), q(CURL, 109, "REQ-021")],
                   req_ids=["REQ-017", "REQ-016", "REQ-021"], source="rule:get-mutation", rule_ids=["get-mutation"],
                   stage="rules")


class MergeByTopic(unittest.TestCase):
    def test_same_topic_duplicates_keep_all_quotes(self):
        audit: list[dict] = []
        out = merge.dedupe_model([get_step_finding(), get_set_finding()], audit)
        self.assertEqual(len(out), 1)
        f = out[0]
        # the survivor's own quote first, the rest in document order, no repeats
        self.assertEqual([x.line_start for x in f.quotes], [120, 104, 109])
        self.assertEqual(f.see_also, [])
        self.assertIn("GET", " ".join(x.text for x in f.quotes))
        self.assertEqual(f.req_ids, ["REQ-025", "REQ-017", "REQ-021"])
        self.assertTrue(any(a.get("what") == "duplicate_merged" for a in audit))

    def test_near_identical_titles_are_one_topic(self):
        a, b = get_step_finding(), get_set_finding()
        a.problem, b.problem = "первое описание", "совсем другое изложение"
        b.quotes = [q(METHOD_ROW, 104, "REQ-017")]
        b.title = "Сохранение настройки выполняется методом GET."
        self.assertTrue(merge.same_topic(a, b))
        b.title = "Сохранение настройки выполняется методом GET в примере запроса"
        self.assertTrue(merge.same_topic(a, b))

    def test_set_survivor_links_only_what_the_absorbed_finding_named(self):
        target = get_set_finding()
        other = get_set_finding()
        other.title = "Сохранение порога выполняется методом GET в примере"
        other.quotes = [q(DESC_ROW, 103, "REQ-016")]
        other.req_ids = []  # the quote lies in REQ-016, but nobody named it
        merge.absorb(target, other)
        self.assertIn(103, [x.line_start for x in target.quotes])
        self.assertNotIn("REQ-016", target.req_ids)

    def test_different_topic_skeptic_merge_moves_places_to_see_also(self):
        a, b = placeholder_finding(), terminology_finding()
        a.fid, b.fid = "D-001", "D-002"
        self.assertFalse(merge.same_topic(a, b))
        merge.apply_skeptic([a, b], [{"fid": "D-002", "decision": "merge", "merge_into": "D-001"}],
                            injection=False, audit=[])
        self.assertEqual([x.line_start for x in a.quotes][0], 50)
        self.assertNotIn(5, [x.line_start for x in a.quotes])
        self.assertIn(5, [x.line_start for x in a.see_also])
        self.assertEqual(a.req_ids, ["REQ-020"])
        reqs = [req("REQ-001", 5, GOAL), req("REQ-005", 9, OTHER), req("REQ-020", 50, ROW)]
        reviews = {r.rid: RequirementReview(rid=r.rid, criteria={"unambiguity": CriterionVerdict("ok")}, by_model=True)
                   for r in reqs}
        pipeline._finish_reviews(reviews, reqs, [a], True)
        self.assertEqual(reviews["REQ-001"].criteria["unambiguity"].status, "ok")
        self.assertEqual(reviews["REQ-005"].criteria["unambiguity"].status, "ok")

    def test_rule_quote_comes_first(self):
        model = get_step_finding()
        model.quotes.append(q(CURL, 109, "REQ-021"))
        merge.fold_rules([get_rule_finding()], [model], [])
        self.assertEqual(model.source, "both")
        self.assertEqual([x.line_start for x in model.quotes], [104, 103, 109, 120])
        self.assertEqual(model.quotes[0].text, METHOD_ROW)

    def test_rule_folded_into_same_topic_finding_not_neighbour(self):
        neighbour = card_in_query_finding()
        get_step = get_step_finding()
        get_step.quotes.append(q(CURL, 109, "REQ-021"))
        audit: list[dict] = []
        out = merge.fold_rules([get_rule_finding()], [neighbour, get_step], audit)
        self.assertEqual(len(out), 2)
        self.assertEqual(get_step.rule_ids, ["get-mutation"])
        self.assertEqual(neighbour.rule_ids, [])
        self.assertEqual(neighbour.source, "model")
        folded = [a for a in audit if a.get("what") == "rule_folded"]
        self.assertEqual(folded[0]["into_title"], get_step.title)

    def test_rule_left_alone_without_same_topic_candidate(self):
        neighbour = card_in_query_finding()
        rule = get_rule_finding()
        out = merge.fold_rules([rule], [neighbour], [])
        self.assertEqual(len(out), 2)
        self.assertIn(rule, out)
        self.assertEqual(neighbour.rule_ids, [])
        self.assertEqual([x.line_start for x in neighbour.quotes], [109])



# --- task 2: counts in the model's verdict paragraph ------------------------------------------------------------


def sev_findings(c: int, m: int, n: int) -> list[Finding]:
    out = []
    for sev, k in (("CRITICAL", c), ("MAJOR", m), ("MINOR", n)):
        out += [Finding(scope="requirement", criterion="completeness", severity=sev, title="x", problem="",
                        recommendation="") for _ in range(k)]
    return out


class VerdictCounts(unittest.TestCase):
    def test_has_count(self):
        self.assertTrue(verdict.has_count("Начать нельзя: есть семь блокеров, включая противоречие."))
        self.assertTrue(verdict.has_count("Найдено 12 существенных замечаний."))
        self.assertTrue(verdict.has_count("Три требования не проверяемы."))
        self.assertTrue(verdict.has_count("Осталось два открытых вопроса."))
        self.assertFalse(verdict.has_count("Тестирование не поставить, пока не заполнен раздел тестирования."))
        self.assertFalse(verdict.has_count("Порог от 1 000 до 5 000 задан явно."))
        self.assertFalse(verdict.has_count("Ошибка под кодом 200 мешает клиенту."))

    def test_guard_replaces_model_counts(self):
        text = ("Начать разработку нельзя: есть семь блокеров, включая противоречие в правиле месяца. "
                "Тестирование тоже не поставить, пока не заполнен раздел тестирования.")
        out = verdict.guard_reason(text, sev_findings(6, 9, 8))
        self.assertTrue(out.startswith("По подсчёту программы: 6 блокеров, 9 существенных замечаний, 8 мелких замечаний."))
        self.assertNotIn("семь", out)
        self.assertIn("Тестирование тоже не поставить", out)

    def test_guard_nothing_left(self):
        out = verdict.guard_reason("Есть 3 блокера. Найдено 5 замечаний.", sev_findings(0, 1, 0))
        self.assertEqual(out, "По подсчёту программы: блокеров нет, 1 существенное замечание, 0 мелких замечаний.")

    def test_prompt_forbids_counts(self):
        self.assertIn("числа программа печатает сама", prompts.QUESTIONS_HEAD)

    def test_pipeline_applies_guard(self):
        def questions(user):
            ans = test_pipeline.questions_answer(user)
            ans["verdict_reason"] = "Есть сорок два блокера. Документ почти готов."
            return ans
        with _TmpDoc() as d:
            res = pipeline.run(d.path, backend=test_pipeline.FakeBackend(test_pipeline.make_script(questions=questions),
                                                                         concurrency=1))
        self.assertTrue(res.verdict_reason.startswith("По подсчёту программы:"))
        self.assertNotIn("сорок", res.verdict_reason)
        self.assertIn("Документ почти готов.", res.verdict_reason)


class _TmpDoc:
    def __init__(self, text: str = test_pipeline.DOC):
        self.text = text

    def __enter__(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name) / "doc.md"
        p.write_text(self.text, encoding="utf-8")
        self.path = str(p)
        return self

    def __exit__(self, *a):
        self.tmp.cleanup()


# --- task 3: clarity scale --------------------------------------------------------------------------------------


class ClarityScale(unittest.TestCase):
    def linked(self, sev: str, k: int = 1, scope: str = "requirement") -> list[Finding]:
        return [Finding(scope=scope, criterion="completeness", severity=sev, title="x", problem="", recommendation="",
                        req_ids=["REQ-001"]) for _ in range(k)]

    def test_one_blocker_is_lowest_band(self):
        v = score.clarity_of("REQ-001", self.linked("CRITICAL"))
        self.assertEqual(v, 3.5)
        self.assertEqual(score.clarity_band(v), "в таком виде использовать нельзя")

    def test_no_blocker_never_below_four(self):
        v = score.clarity_of("REQ-001", self.linked("MAJOR", 3))
        self.assertEqual(v, 4.0)
        self.assertEqual(score.clarity_band(v), "нужна доработка")
        self.assertEqual(score.clarity_of("REQ-001", self.linked("MAJOR", 2) + self.linked("MINOR", 6)), 4.0)

    def test_blocker_with_others_goes_to_one(self):
        self.assertEqual(score.clarity_of("REQ-001", self.linked("CRITICAL") + self.linked("MAJOR", 2)), 1.0)

    def test_set_level_blocker_counts_half(self):
        self.assertEqual(score.clarity_of("REQ-001", self.linked("CRITICAL", scope="set")), 6.8)

    def test_report_explains_scale(self):
        with _TmpDoc() as d:
            res = pipeline.run(d.path, backend=None)
        text = report_md.render(res)
        self.assertIn("Ясность — 10 минус вес замечаний, которые называют требование: блокер 6,5", text)
        self.assertIn("не ниже 1, а без блокера не ниже 4", text)


# --- task 4: version label -----------------------------------------------------------------------------------------


class _Git:
    """A fake git runner: answers by sub-command."""

    def __init__(self, sha: str = "abc1234", tag: str | None = None, status: str = ""):
        self.sha, self.tag, self.status = sha, tag, status

    def __call__(self, cmd, **kw):
        if "rev-parse" in cmd:
            return test_wave2._Out(self.sha + "\n")
        if "describe" in cmd:
            return test_wave2._Out(self.tag + "\n") if self.tag else test_wave2._Out("", 128)
        if "status" in cmd:
            return test_wave2._Out(self.status)
        return test_wave2._Out("", 1)


def label_with(runner, folder=".") -> str:
    commit = config.agent_commit(folder, runner=runner)
    tag = config.agent_tag(folder, runner=runner) if commit else ""
    dirty = config.agent_dirty(folder, runner=runner) if commit else False
    return config.version_label("1.0.0", commit, "1.0", tag=tag, dirty=dirty)


class VersionLabel(unittest.TestCase):
    def test_tagged_commit(self):
        self.assertEqual(label_with(_Git(tag="v1.0.0")), "v1.0.0 (тег `v1.0.0`) · коммит `abc1234` · набор правил 1.0")

    def test_commit_without_tag(self):
        self.assertEqual(label_with(_Git()), "v1.0.0 · коммит `abc1234` · набор правил 1.0")

    def test_uncommitted_changes(self):
        self.assertEqual(label_with(_Git(status=" M revizor/cli.py\n")),
                         "v1.0.0 · коммит `abc1234` + незакоммиченные правки · набор правил 1.0")
        self.assertEqual(label_with(_Git(tag="v1.0.0", status=" M x\n")),
                         "v1.0.0 (тег `v1.0.0`) · коммит `abc1234` + незакоммиченные правки · набор правил 1.0")

    def test_no_git(self):
        def missing(cmd, **kw):
            raise FileNotFoundError("git")
        self.assertEqual(label_with(missing), "v1.0.0 · коммит не определён (папка вне git) · набор правил 1.0")
        self.assertEqual(config.agent_tag(".", runner=missing), "")
        self.assertFalse(config.agent_dirty(".", runner=missing))

    def test_commands(self):
        calls = []

        def runner(cmd, **kw):
            calls.append((cmd, kw))
            return test_wave2._Out("")
        config.agent_tag("/x", runner=runner)
        config.agent_dirty("/x", runner=runner)
        self.assertEqual(calls[0][0], ["git", "-C", "/x", "describe", "--tags", "--exact-match", "HEAD"])
        self.assertEqual(calls[1][0], ["git", "-C", "/x", "--no-optional-locks", "status", "--porcelain",
                                       "--untracked-files=no"])
        self.assertTrue(all(kw.get("timeout") == 2 for _c, kw in calls))

    def test_json_fields(self):
        self.assertEqual(RunInfo().agent_tag, "")
        self.assertFalse(RunInfo().agent_dirty)
        with _TmpDoc() as d:
            res = pipeline.run(d.path, backend=None)
        res.run.agent_commit, res.run.agent_tag, res.run.agent_dirty = "abc1234", "v1.0.0", True
        import json
        data = json.loads(report_json.render(res))
        self.assertEqual(data["run"]["agent_tag"], "v1.0.0")
        self.assertTrue(data["run"]["agent_dirty"])
        self.assertIn("(тег `v1.0.0`) · коммит `abc1234` + незакоммиченные правки", report_md.render(res))


# --- task 5: document title -----------------------------------------------------------------------------------------


class DocTitle(unittest.TestCase):
    def title_of(self, text: str, name: str = "doc.md") -> str:
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / name
            p.write_text(text, encoding="utf-8")
            res = pipeline.run(str(p), backend=None)
        return res

    def test_first_heading(self):
        res = self.title_of(test_pipeline.DOC)
        self.assertEqual(res.run.doc_title, "Уведомление о порогах")
        text = report_md.render(res)
        self.assertIn("| Документ | ", text)
        row = text.split("| Документ | ", 1)[1].split("\n", 1)[0]
        self.assertTrue(row.endswith("· «Уведомление о порогах» |"), row)
        import json
        self.assertEqual(json.loads(report_json.render(res))["run"]["doc_title"], "Уведомление о порогах")

    def test_first_line_without_headings_is_cut(self):
        long_first = "Очень длинная первая строка " * 10
        body = "\n".join(f"{k}. Система должна сохранять настройку номер {k} клиента в течение 2 секунд."
                         for k in range(1, 8))
        res = self.title_of("\n" + long_first.strip() + "\n\n" + body + "\n", name="doc.txt")
        self.assertLessEqual(len(res.run.doc_title), 120)
        self.assertTrue(res.run.doc_title.startswith("Очень длинная первая строка"))

    def test_default_and_empty(self):
        self.assertEqual(RunInfo().doc_title, "")
        res = self.title_of(test_pipeline.DOC)
        res.run.doc_title = ""
        row = report_md.render(res).split("| Документ | ", 1)[1].split("\n", 1)[0]
        self.assertNotIn("«", row)


# --- task 6: masked values keep their asterisks -------------------------------------------------------------------


class MaskedValues(unittest.TestCase):
    def test_clean_inline_keeps_mask(self):
        self.assertEqual(structure.clean_inline("номер 4539 14** **** 6467 в маске"), "номер 4539 14** **** 6467 в маске")
        self.assertEqual(structure.clean_inline("ИИН 123456****** указан"), "ИИН 123456****** указан")
        # ordinary emphasis is still removed
        self.assertEqual(structure.clean_inline("**Важно:** код **200** и *курсив*"), "Важно: код 200 и курсив")
        self.assertEqual(textfmt.plain_quote("14** **** 6467"), "14** **** 6467")
        self.assertEqual(textfmt.plain_quote("**Request URL**"), "Request URL")

    def test_report_shows_mask(self):
        doc = test_pipeline.DOC + "\n| Поле | Значение |\n| --- | --- |\n| Номер карты | 4539 1488 0343 6467 |\n"
        with _TmpDoc(doc) as d:
            res = pipeline.run(d.path, backend=None)
        text = report_md.render(res)
        self.assertNotIn("1488 0343", text)
        self.assertIn("4539 14\\*\\* \\*\\*\\*\\* 6467", text)
        self.assertNotIn("4539 14 6467", text)


# --- task 7: cost line ---------------------------------------------------------------------------------------------


class CostLine(test_cli._Base):
    def test_missing_file_cost_is_zero(self):
        out = self.dir / "m.md"
        self.main("--provider", "offline", "-i", str(self.dir / "нет.md"), "-o", str(out))
        self.assertIn("Стоимость: $0 (модель не вызывалась)", self.text)
        self.assertNotIn("неизвестна", self.text)

    def test_offline_cost_is_zero(self):
        out = self.dir / "r.md"
        self.main("--provider", "offline", "-i", self.doc(), "-o", str(out))
        self.assertIn("Стоимость: $0 (модель не вызывалась)", self.text)


if __name__ == "__main__":
    unittest.main()
