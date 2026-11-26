# Что лежит в этой папке

Сдаваемый отчёт — [`report.md`](report.md) и файлы с тем же именем рядом. Всё остальное — образцы других режимов на том же документе организаторов
[`docs/requirements.md`](../docs/requirements.md).

| Файлы | Что это | Как получено |
| :--- | :--- | :--- |
| `report.*` | **сдаваемый отчёт**: глубокий режим, Opus (максимум), модель `claude-opus-5-5`; собран на прежней версии (тег `v1.1.1`) | `--model claude-opus-5-5 --no-cache` |
| [`report.pdf`](report.pdf) | сдаваемый отчёт вместе с подробностями в PDF, 62 страницы | печать страницы [`report.html`](report.html) в браузере; сам агент PDF не пишет |
| `report_haiku.*` | тот же документ на модели Haiku (дешевле), `claude-haiku-5-5`; прежняя версия (тег `v1.1.1`) | `--model claude-haiku-5-5` |
| `report_offline.*` | тот же документ без модели, только правила | `--provider offline` |
| `report_offline_pdf.*` | то же без модели, но на вход подан PDF [`examples/requirements.pdf`](../examples/requirements.pdf) | `--provider offline` |
| `report_answered.*` | повторная проверка после ответов автора, модель `claude-opus-5-5` | `--answers docs/demo/answers.md` |
| `report_offline_answered.*` | повторная проверка после ответов автора без модели | `--provider offline --answers reports/sample_answers.md` |
| [`sample_answers.md`](sample_answers.md) | заполненная анкета для примера без модели | анкета `report_offline.questions.md` с вписанными ответами |
| [`diagnostics/`](diagnostics/) | отчёты «Документ не проверен» на битых и пустых файлах из [`examples/broken/`](../examples/broken/) | запуск на каждом файле |

С чего начать: одна страница [`report.summary.md`](report.summary.md), затем первый экран [`report.md`](report.md).
Что означает каждый файл рядом с отчётом — в [`docs/USAGE.md`](../docs/USAGE.md#что-лежит-рядом-с-отчётом).
