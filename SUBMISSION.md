# Ревизор ТЗ: страница сдачи

ИИ-агент проверяет документ с требованиями до начала разработки и пишет отчёт на русском языке. Он находит дефекты с точной цитатой и номером строки, задаёт автору конкретные вопросы, перепроверяет документ по его ответам и показывает, что изменилось.

## Запуск одной командой

```bash
pip install -r requirements.txt        # Python 3.10 или новее
python main.py --input docs/requirements.md --output reports/my_report.md --provider offline   # без ключа, бесплатно
python main.py --input docs/requirements.md --output reports/my_report.md                      # с моделью, ключ в .env
python main.py --ui                                                                             # то же в окне браузера
```

Ключ кладётся в `.env` (образец — `.env.example`): Gemini бесплатно, Anthropic, OpenAI-совместимый сервер или локальная Ollama. В окне на первом экране кнопка «Попробовать на примере» проверяет встроенный пример сразу, без ключа. Подробно — [`README.md`](README.md#запуск-одной-командой).

## Видео и слайды

- Слайды: [`presentation.pdf`](presentation.pdf); исходник и сборка — [`docs/slides/`](docs/slides/README.md).
- Видео: **ссылка на видео: будет добавлена** (для владельца: в репозитории видеофайла нет, ссылка вписывается здесь).

## Что смотреть первым

1. [Уточнение в README](README.md#уточнение-вопросы-автору-и-перепроверка) и пример круга «вопросы — ответы — перепроверка»: анкета с ответами [`docs/demo/answers.md`](docs/demo/answers.md) и результат [`reports/report_answered.md`](reports/report_answered.md).
2. Первый экран сданного отчёта [`reports/report.md`](reports/report.md) и одностраничная сводка [`reports/report.summary.md`](reports/report.summary.md): вердикт, блокеры, первые вопросы автору.
3. Схема этапов и гарантий кода в [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Четыре критерия и где каждый показан

| Критерий | Где смотреть |
| :--- | :--- |
| Уточнение | вопросы с причиной, вариантами и допущением — [`reports/report.questions.md`](reports/report.questions.md); письмо автору по адресатам `<отчёт>.letter.md`; круг в окне (`python main.py --ui`: «Перепроверить с ответами», «было → стало», «Что вписать в документ»); пример — [`reports/report_answered.md`](reports/report_answered.md); описание — [`README.md`](README.md#уточнение-вопросы-автору-и-перепроверка) |
| Архитектура | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): девять этапов, что делает код и что модель, гарантии кода, контур уточнения, провайдеры и режим без модели; тесты — `python -m unittest discover -s tests -t .` |
| Бизнес-ценность | [`docs/VALUE.md`](docs/VALUE.md): кто пользуется, цена прогона, порог окупаемости, источники; [`docs/PILOT.md`](docs/PILOT.md): пилот на две недели; [`docs/INTEGRATION.md`](docs/INTEGRATION.md): CI, Jira, Confluence; точность — [`eval/ACCURACY.md`](eval/ACCURACY.md) |
| Презентация и защита | [`presentation.pdf`](presentation.pdf), [`docs/slides/`](docs/slides/README.md), видео (ссылка выше), этот файл и [`README.md`](README.md) |
