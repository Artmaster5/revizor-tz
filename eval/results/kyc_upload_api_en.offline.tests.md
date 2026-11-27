# Черновики приёмочных тестов для «kyc\_upload\_api\_en.md»

Ожидаемый результат взят только из документа. Черновик печатается, только если в нём есть конкретное действие и конкретный ожидаемый результат; придумывать поведение за автора агент не вправе. Это черновики для тестировщика, не готовые тест-кейсы.

Готовы к прогону: 5. Ждут ответа автора: 0. Нельзя проверить, пока нет ответа: 5 (список в конце файла).

### Т-01 · позитивный · REQ-040

**REQ-040: Upload a 2 MB JPEG ID\_CARD FRONT** — готов

- **Дано:** Session in DRAFT
- **Когда:** Upload a 2 MB JPEG ID\_CARD FRONT
- **Тогда:** 201, status RECEIVED

### Т-02 · позитивный · REQ-041

**REQ-041: Upload a GIF file** — готов

- **Дано:** Session in DRAFT
- **Когда:** Upload a GIF file
- **Тогда:** 415

### Т-03 · позитивный · REQ-042

**REQ-042: Repeat POST with the same Idempotency-Key within 24 hours** — готов

- **Дано:** Any session
- **Когда:** Repeat POST with the same Idempotency-Key within 24 hours
- **Тогда:** Original response, no new document

### Т-04 · позитивный · REQ-043

**REQ-043: Delete a document** — готов

- **Дано:** Session in SUBMITTED
- **Когда:** Delete a document
- **Тогда:** 400

### Т-05 · позитивный · REQ-044

**REQ-044: Upload a 4th selfie** — готов

- **Дано:** Session with 3 rejected selfies
- **Когда:** Upload a 4th selfie
- **Тогда:** 423 Locked for 24 hours

## Что нельзя проверить, пока нет ответа

Для этих требований в документе нет конкретного действия или ожидаемого результата, поэтому черновик теста не составлен.

- REQ-014: Example: curl -X POST "\$BASE/v1/onboarding/ /documents?iin=000000000000" \\ -H "Authorization… — ответ: В-02; замечание D-002
- REQ-021: If a document of the same docType and side has already been uploaded in the session, the server… — ответ: В-03; замечание D-003
- REQ-022: The upload endpoint responds within 3 seconds — ответ: В-06; замечание D-004
- REQ-027: Submits all uploaded documents for verification — ответ: В-04; замечание D-006
- REQ-028: The verification provider is called asynchronously — ответ: В-07; замечание D-007
