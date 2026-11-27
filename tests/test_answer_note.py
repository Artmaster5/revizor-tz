"""Слова автора после буквы варианта — часть ответа: их видит модель и читатель отчёта."""
import unittest

from revizor import answers as ans_mod
from revizor import sidefiles
from revizor.model import Question

OPTIONS = ["Второй push нужен", "Второй push не нужен"]


def question(answer: str = "") -> Question:
    return Question(qid="В-02", text="Нужен ли второй push после смены порога?", why="от ответа зависит Ф-4",
                    addressee="analyst", options=list(OPTIONS), answer=answer)


class AnswerNote(unittest.TestCase):
    def test_words_after_letter_are_kept(self):
        a = ans_mod.interpret("В-02", "а. Только если порог изменён в этом же месяце", OPTIONS)
        self.assertEqual((a.kind, a.letter, a.text), ("option", "а", OPTIONS[0]))
        self.assertEqual(a.note, "Только если порог изменён в этом же месяце")
        self.assertEqual(ans_mod.shown(a), "Второй push нужен — Только если порог изменён в этом же месяце")

    def test_letter_alone_gives_option_text(self):
        a = ans_mod.interpret("В-02", "б", OPTIONS)
        self.assertEqual(ans_mod.shown(a), OPTIONS[1])

    def test_copied_option_text_is_not_doubled(self):
        a = ans_mod.interpret("В-02", "а) второй push нужен.", OPTIONS)
        self.assertEqual(a.note, "")
        self.assertEqual(ans_mod.shown(a), OPTIONS[0])

    def test_free_answer_is_unchanged(self):
        a = ans_mod.interpret("В-02", "Решит владелец продукта до 1 декабря", OPTIONS)
        self.assertEqual((a.kind, a.note), ("free", ""))
        self.assertEqual(ans_mod.shown(a), "Решит владелец продукта до 1 декабря")

    def test_model_sees_the_note(self):
        q = question()
        q.answer = ans_mod.shown(ans_mod.interpret("В-02", "а. Только если порог изменён", OPTIONS))
        block = ans_mod.question_block(q, {}, {})
        self.assertIn("ответ автора: Второй push нужен — Только если порог изменён", block)

    def test_offline_keeps_note_and_still_closes_by_option(self):
        q = question()
        parsed = ans_mod.Parsed(answers={"В-02": ans_mod.interpret("В-02", "а. Только если порог изменён", OPTIONS)})
        applied = ans_mod.Applied(total=1)
        ans_mod.apply_offline([q], [], parsed, applied)
        self.assertEqual(q.answer, "Второй push нужен — Только если порог изменён")
        self.assertIn("Выбран вариант «Второй push нужен»", q.answer_effect)

    def test_questionnaire_round_trip(self):
        for typed in ("а. Только если порог изменён", "б", "Решит владелец продукта"):
            q = question(ans_mod.shown(ans_mod.interpret("В-02", typed, OPTIONS)))
            cell = sidefiles._answer_cell(q)
            again = ans_mod.interpret("В-02", cell, OPTIONS)
            self.assertEqual(ans_mod.shown(again), q.answer, typed)


if __name__ == "__main__":
    unittest.main()
