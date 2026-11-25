# Презентация «Ревизор ТЗ»: как пересобрать

Шаблон — `slides.html` (семь слайдов 16:9, числа только в виде `{{ключ}}`). Все числа берутся из файла `numbers.json`.

1. Сделайте картинки отчёта (нужен Google Chrome):
   `python tools/shots.py reports/report.md --out-dir /tmp/shots`
2. Числа сданных слайдов уже лежат в `docs/slides/numbers.json`. Для своих чисел скопируйте
   `docs/slides/numbers.example.json` в новый файл, уберите строку `"_example": true` и замените все значения
   на настоящие (в них не должно остаться слова «ПРИМЕР»); путь к новому файлу передайте в `--numbers`.
   Ключ `img_report_top` — имя картинки, например `report_top.png`.
3. Соберите PDF:
   `python tools/build_slides.py --numbers docs/slides/numbers.json --shots-dir /tmp/shots --out presentation.pdf`
4. Только заполненный HTML, без Chrome: добавьте `--html-only` (по умолчанию `presentation.html`).
5. Пробная сборка на примерных числах: добавьте `--allow-example`.

Сборка останавливается и печатает весь список проблем, если для места в шаблоне нет значения, значение лишнее,
картинка не найдена или файл ещё помечен как пример. Chrome ищется по `--chrome`, затем по `CHROME_BIN`,
затем по обычному пути macOS.
