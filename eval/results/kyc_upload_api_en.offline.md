# Отчёт о статической проверке требований

**Документ:** `kyc_upload_api_en.md` · проверен 25.11.2026 18:01 (UTC+05:00) · без модели — только автоматические правила

> **Итог:** ⛔ Начинать разработку и тестирование нельзя: сначала устранить блокеры (модель не использовалась: проверены только автоматические правила, итог предварительный)
> Требований: 44 · Дефектов: 8 (🔴 CRITICAL 1 · 🟡 MAJOR 6 · 🟢 MINOR 1) · Вопросов автору: 8
> Индекс готовности документа: **77 из 100** — готов после правок, предварительно. Вердикт определяют блокеры, а не индекс.
> ⚠️ Смысловые противоречия, пропущенные сценарии и пограничные случаи без модели не искались: чистый результат здесь не означает, что документ готов.

**Блокеры — исправить до начала работы (1):**

1. [D-005](#d-005) Метод GET используется для изменения данных — строка 72

Ещё 9 наблюдений низкой точности вынесено отдельно и на итог не влияет ([раздел 4](#s4-obs)).

**Разделы:** [1. Паспорт прогона](#s1) · [2. Как агент нарезал требования](#s2) · [3. Проверка каждого требования](#s3) · [4. Проверка всего набора требований](#s4) · [5. Сводка](#s5) · [6. Итог](#s6) · [7. Уточняющие вопросы к автору ТЗ](#s7) · [8. Черновики приёмочных тестов](#s8) · [Приложение. Как получен отчёт](#app)

<a id="s1"></a>
## 1. Паспорт прогона

| Поле | Значение |
| :--- | :--- |
| Документ | `kyc_upload_api_en.md` · Markdown · 5,4 КБ · 121 строка · 817 слов · SHA-256 `4cf9a9852dac` |
| Дата и время прогона | 25.11.2026 18:01 (UTC+05:00) |
| Модель | без модели |
| Версия агента | 1.0.0; набор правил 1.0 |
| Воспроизводимость | правила, номера, проверка цитат, индексы и вердикт детерминированы; замечания модели могут отличаться от прогона к прогону |
| Режим | без модели: только автоматические правила |
| Время анализа | 0,0 с |
| Обращения к модели | 0 |
| Стоимость прогона | $0 (модель не вызывалась) |
| Проверка цитат | подтверждено 20, отброшено 0 |
| Перепроверка | не выполнялась (без модели) |
| Защита данных | значений, похожих на номера карт или ИИН, не найдено |

<a id="s2"></a>
## 2. Как агент нарезал требования

Требования выделены эвристикой (без модели); номера REQ-… присвоены программой по порядку документа. Найдено 44 требования. Столбец «Ясность» — индекс от 1 до 10: 10 — замечаний нет, ниже 4 — в таком виде использовать нельзя.

| ID в отчёте | ID в документе | Раздел | Тип | Формулировка (цитата) | Где | Ясность |
| :--- | :--- | :--- | :--- | :--- | :--- | ---: |
| [REQ-001](#req-001) | — | 1\. Purpose | интерфейс (API) | The API lets the mobile app upload images of the applicant's identity document (ID card of the Republic of Kazakhstan or passport) and a selfie during remote… | строка 13 | 9,6 |
| [REQ-002](#req-002) | 2.1 | 2\. General rules | интерфейс (API) | 2.1. All endpoints are served over HTTPS only, TLS 1.2 or higher; plain HTTP requests are rejected without redirect. | строка 17 | 10,0 |
| [REQ-003](#req-003) | 2.2 | 2\. General rules | интерфейс (API) | 2.2. Every request carries Authorization: Bearer \<access\_token\> issued to the onboarding session. The access token lifetime is 15 minutes. | строка 18 | 10,0 |
| [REQ-004](#req-004) | 2.3 | 2\. General rules | интерфейс (API) | 2.3. All timestamps are ISO 8601 strings in UTC, e.g. 2026-11-12T09:30:00Z. | строка 19 | 10,0 |
| [REQ-005](#req-005) | 2.4 | 2\. General rules | интерфейс (API) | 2.4. Every POST request must carry an Idempotency-Key header (UUID v4). A repeated request with the same key within 24 hours returns the original response and… | строка 20 | 10,0 |
| [REQ-006](#req-006) | 2.5 | 2\. General rules | интерфейс (API) | 2.5. The applicant is identified by IIN (12 digits). Test IIN for examples: 000000000000. | строка 21 | 10,0 |
| [REQ-007](#req-007) | 2.6 | 2\. General rules | интерфейс (API) | 2.6. The client app compresses images, strips EXIF location data, shows a progress bar and retries failed uploads up to 3 times. | строка 22 | 10,0 |
| [REQ-008](#req-008) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Uploads one document image. Request body: multipart/form-data. | строка 28 | 9,6 |
| [REQ-009](#req-009) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Parameter: sessionId \| In: path \| Type: string (UUID) \| Required: yes \| Rules: Onboarding session id | строка 32 | 10,0 |
| [REQ-010](#req-010) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Parameter: iin \| In: query \| Type: string \| Required: yes \| Rules: Applicant IIN, 12 digits | строка 33 | 10,0 |
| [REQ-011](#req-011) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Parameter: docType \| In: form \| Type: string \| Required: yes \| Rules: ID\_CARD, PASSPORT, SELFIE | строка 34 | 10,0 |
| [REQ-012](#req-012) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Parameter: side \| In: form \| Type: string \| Required: for ID\_CARD only \| Rules: FRONT or BACK | строка 35 | 10,0 |
| [REQ-013](#req-013) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Parameter: file \| In: form \| Type: binary \| Required: yes \| Rules: JPEG or PNG, max 10 MB | строка 36 | 10,0 |
| [REQ-014](#req-014) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Example: curl -X POST "\$BASE/v1/onboarding/3f1c9a52-0000-4000-8000-000000000001/documents?iin=000000000000" \\ -H "Authorization: Bearer \$TOKEN" -H… | строки 38–44 | 8,8 |
| [REQ-015](#req-015) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | Responses: | строка 46 | 10,0 |
| [REQ-016](#req-016) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | 201 Created — upload accepted: \{"documentId": "d-001", "status": "RECEIVED"\} | строка 48 | 10,0 |
| [REQ-017](#req-017) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | 400 Bad Request — a parameter is missing or invalid | строка 49 | 10,0 |
| [REQ-018](#req-018) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | 413 Payload Too Large — the file exceeds the size limit | строка 50 | 10,0 |
| [REQ-019](#req-019) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | 415 Unsupported Media Type — the file is not JPEG or PNG | строка 51 | 10,0 |
| [REQ-020](#req-020) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | 422 Unprocessable Entity — image quality is insufficient, see 4.3 | строка 52 | 10,0 |
| [REQ-021](#req-021) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | If a document of the same docType and side has already been uploaded in the session, the server returns 409. | строка 54 | 8,8 |
| [REQ-022](#req-022) | — | 3.1. POST /v1/onboarding/\{sessionId\}/documents | интерфейс (API) | The upload endpoint responds within 3 seconds. | строка 56 | 7,5 |
| [REQ-023](#req-023) | — | 3.2. GET /v1/onboarding/\{sessionId\}/status | интерфейс (API) | Returns the verification status of the session. Authorization is not required for this endpoint so that the web landing page can poll it. | строка 60 | 9,6 |
| [REQ-024](#req-024) | — | 3.2. GET /v1/onboarding/\{sessionId\}/status | интерфейс (API) | Response 200 OK: \{"sessionId": "3f1c9a52-0000-4000-8000-000000000001", "status": "PROCESSING", "fullName": "TEST APPLICANT", "iin": "000000000000"… | строки 62–68 | 10,0 |
| [REQ-025](#req-025) | — | 3.2. GET /v1/onboarding/\{sessionId\}/status | интерфейс (API) | Session status values: DRAFT, SUBMITTED, PROCESSING, VERIFIED, REJECTED. | строка 70 | 10,0 |
| [REQ-026](#req-026) | — | 3.3. GET /v1/onboarding/\{sessionId\}/documents/\{documentId\}/… | интерфейс (API) | Removes a previously uploaded document. A document can be removed only while the session status is DRAFT; otherwise the server returns 400. | строка 74 | 10,0 |
| [REQ-027](#req-027) | — | 3.4. POST /v1/onboarding/\{sessionId\}/submit | интерфейс (API) | Submits all uploaded documents for verification. The session must contain an ID card (both sides) or a passport, and one selfie; otherwise 400 with \{"error"… | строка 78 | 8,8 |
| [REQ-028](#req-028) | — | 3.4. POST /v1/onboarding/\{sessionId\}/submit | интерфейс (API) | The verification provider is called asynchronously. When it responds, the status changes to PROCESSING and then to APPROVED or REJECTED. If the verification… | строка 80 | 7,5 |
| [REQ-029](#req-029) | 4.1 | 4\. Image quality | интерфейс (API) | 4.1. Minimum resolution of a document image is 1280×720 pixels; a selfie is at least 640×480 pixels. | строка 88 | 10,0 |
| [REQ-030](#req-030) | 4.2 | 4\. Image quality | интерфейс (API) | 4.2. Glare and blur on the document image must be within acceptable limits. | строка 89 | 10,0 |
| [REQ-031](#req-031) | — | 4\. Image quality | интерфейс (API) | Liveness of the selfie is checked by the FaceCheck SDK 4.2 library on the server in a dedicated thread pool of 8 workers. | строка 91 | 10,0 |
| [REQ-032](#req-032) | — | 5\. Limits | интерфейс (API) | Limit: Maximum file size \| Value: 8 000 KB | строка 97 | 10,0 |
| [REQ-033](#req-033) | — | 5\. Limits | интерфейс (API) | Limit: Supported formats \| Value: JPEG, PNG, HEIC | строка 98 | 10,0 |
| [REQ-034](#req-034) | — | 5\. Limits | интерфейс (API) | Limit: Uploads per session \| Value: at most 20 | строка 99 | 10,0 |
| [REQ-035](#req-035) | — | 5\. Limits | интерфейс (API) | Limit: Rate limit \| Value: 30 requests per applicant | строка 100 | 10,0 |
| [REQ-036](#req-036) | 6.1 | 6\. Data retention | данные | 6.1. Document images and selfies are stored for 90 days after the onboarding session ends and are then permanently deleted. | строка 104 | 10,0 |
| [REQ-037](#req-037) | 6.2 | 6\. Data retention | данные | 6.2. Images are stored encrypted with AES-256; keys are managed by the bank's HSM. | строка 105 | 10,0 |
| [REQ-038](#req-038) | 7.1 | 7\. Audit | интерфейс (API) | 7.1. Every upload, deletion and status change is written to the audit log with sessionId, documentId, operation and timestamp. | строка 109 | 10,0 |
| [REQ-039](#req-039) | 7.2 | 7\. Audit | интерфейс (API) | 7.2. Original document images are retained for the whole term of the business relationship and for 5 years after it ends, as required by AML regulations. | строка 110 | 10,0 |
| [REQ-040](#req-040) | AC-1 | 8\. Acceptance criteria | критерий приёмки | \#: AC-1 \| Given: Session in DRAFT \| When: Upload a 2 MB JPEG ID\_CARD FRONT \| Then: 201, status RECEIVED | строка 116 | 10,0 |
| [REQ-041](#req-041) | AC-2 | 8\. Acceptance criteria | критерий приёмки | \#: AC-2 \| Given: Session in DRAFT \| When: Upload a GIF file \| Then: 415 | строка 117 | 10,0 |
| [REQ-042](#req-042) | AC-3 | 8\. Acceptance criteria | критерий приёмки | \#: AC-3 \| Given: Any session \| When: Repeat POST with the same Idempotency-Key within 24 hours \| Then: Original response, no new document | строка 118 | 10,0 |
| [REQ-043](#req-043) | AC-4 | 8\. Acceptance criteria | критерий приёмки | \#: AC-4 \| Given: Session in SUBMITTED \| When: Delete a document \| Then: 400 | строка 119 | 10,0 |
| [REQ-044](#req-044) | AC-5 | 8\. Acceptance criteria | критерий приёмки | \#: AC-5 \| Given: Session with 3 rejected selfies \| When: Upload a 4th selfie \| Then: 423 Locked for 24 hours | строка 120 | 10,0 |

**Не считал требованиями: 20 блоков.**

- заголовок: заголовок раздела · строки 1, 11, 15, 24, 26, 58, 72, 76, 82, 86, 93, 102, 107, 112
- пояснение: вступление под названием документа, до первого раздела · строки 5, 6, 7, 8, 9
- заглушка: заглушка вместо содержания · строка 84 — «TBD.»

<a id="s3"></a>
## 3. Проверка каждого требования

Без модели каждое требование проверено только автоматическими правилами (размытые слова, заглушки, сроки без условий, незакрытые перечни, несколько требований в одном пункте и др.). Смысловые критерии без модели не проверялись.

<a id="req-001"></a>
### REQ-001 · 1\. Purpose · строка 13

> The API lets the mobile app upload images of the applicant's identity document (ID card of the Republic of Kazakhstan or passport) and a selfie during remote onboarding, submit them for verification and query the verification status.

Тип: интерфейс (API) · Ясность: 9,6 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 1. Purpose

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись. Но требование входит в замечания, охватывающие несколько мест документа, — они снижают его ясность (см. ниже).

См. также: [D-001](#d-001) · 🟢 MINOR — У большинства требований нет номеров

<a id="req-002"></a>
### REQ-002 · 2.1 · 2\. General rules · строка 17

> 2.1. All endpoints are served over HTTPS only, TLS 1.2 or higher; plain HTTP requests are rejected without redirect.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 2. General rules

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-003"></a>
### REQ-003 · 2.2 · 2\. General rules · строка 18

> 2.2. Every request carries Authorization: Bearer \<access\_token\> issued to the onboarding session. The access token lifetime is 15 minutes.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 2. General rules

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-004"></a>
### REQ-004 · 2.3 · 2\. General rules · строка 19

> 2.3. All timestamps are ISO 8601 strings in UTC, e.g. 2026-11-12T09:30:00Z.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 2. General rules

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-005"></a>
### REQ-005 · 2.4 · 2\. General rules · строка 20

> 2.4. Every POST request must carry an Idempotency-Key header (UUID v4). A repeated request with the same key within 24 hours returns the original response and does not create a new upload.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 2. General rules

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-006"></a>
### REQ-006 · 2.5 · 2\. General rules · строка 21

> 2.5. The applicant is identified by IIN (12 digits). Test IIN for examples: 000000000000.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 2. General rules

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-007"></a>
### REQ-007 · 2.6 · 2\. General rules · строка 22

> 2.6. The client app compresses images, strips EXIF location data, shows a progress bar and retries failed uploads up to 3 times.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 2. General rules

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-008"></a>
### REQ-008 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 28

> Uploads one document image. Request body: multipart/form-data.

Тип: интерфейс (API) · Ясность: 9,6 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись. Но требование входит в замечания, охватывающие несколько мест документа, — они снижают его ясность (см. ниже).

См. также: [D-001](#d-001) · 🟢 MINOR — У большинства требований нет номеров

<a id="req-009"></a>
### REQ-009 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 32

> Parameter: sessionId \| In: path \| Type: string (UUID) \| Required: yes \| Rules: Onboarding session id

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-010"></a>
### REQ-010 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 33

> Parameter: iin \| In: query \| Type: string \| Required: yes \| Rules: Applicant IIN, 12 digits

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-011"></a>
### REQ-011 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 34

> Parameter: docType \| In: form \| Type: string \| Required: yes \| Rules: ID\_CARD, PASSPORT, SELFIE

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-012"></a>
### REQ-012 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 35

> Parameter: side \| In: form \| Type: string \| Required: for ID\_CARD only \| Rules: FRONT or BACK

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-013"></a>
### REQ-013 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 36

> Parameter: file \| In: form \| Type: binary \| Required: yes \| Rules: JPEG or PNG, max 10 MB

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-014"></a>
### REQ-014 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строки 38–44

> Example:
> curl -X POST "\$BASE/v1/onboarding/3f1c9a52-0000-4000-8000-000000000001/documents?iin=000000000000" \\
> -H "Authorization: Bearer \$TOKEN" -H "Idempotency-Key: 7d4e0c1a-0000-4000-8000-000000000002" \\
> -F docType=PHOTO -F file=\@selfie.jpg

Тип: интерфейс (API) · Ясность: 8,8 из 10 (есть замечания) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись. Но требование входит в замечания, охватывающие несколько мест документа, — они снижают его ясность (см. ниже).

См. также: [D-002](#d-002) · 🟡 MAJOR — Чувствительные данные передаются в адресной строке запроса

<a id="req-015"></a>
### REQ-015 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 46

> Responses:

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-016"></a>
### REQ-016 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 48

> 201 Created — upload accepted: \{"documentId": "d-001", "status": "RECEIVED"\}

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-017"></a>
### REQ-017 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 49

> 400 Bad Request — a parameter is missing or invalid

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-018"></a>
### REQ-018 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 50

> 413 Payload Too Large — the file exceeds the size limit

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-019"></a>
### REQ-019 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 51

> 415 Unsupported Media Type — the file is not JPEG or PNG

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-020"></a>
### REQ-020 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 52

> 422 Unprocessable Entity — image quality is insufficient, see 4.3

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-021"></a>
### REQ-021 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 54

> If a document of the same docType and side has already been uploaded in the session, the server returns 409.

Тип: интерфейс (API) · Ясность: 8,8 из 10 (есть замечания) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись. Но требование входит в замечания, охватывающие несколько мест документа, — они снижают его ясность (см. ниже).

См. также: [D-003](#d-003) · 🟡 MAJOR — Код ответа 409 возвращается, но не описан

<a id="req-022"></a>
### REQ-022 · 3.1. POST /v1/onboarding/\{sessionId\}/documents · строка 56

> The upload endpoint responds within 3 seconds.

Тип: интерфейс (API) · Ясность: 7,5 из 10 (есть замечания) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.1. POST /v1/onboarding/\{sessionId\}/documents

Критерии с замечаниями правил: Проверяемость. Остальные критерии без модели не проверялись.

<a id="d-004"></a>
**D-004 · 🟡 MAJOR · Проверяемость.** Ограничение по времени без условий измерения

Задан срок «within 3 seconds», но не сказано, для какой доли случаев он должен выполняться (например, 95% или 99%), при какой нагрузке и от какого до какого момента его считать. Если хотя бы один случай окажется дольше, разработчик скажет, что требование выполнено, а тестировщик — что нет.

- Цитата (строка 56): «The upload endpoint responds within 3 seconds»
- Как исправить: Допишите условия измерения: «within 3 seconds для 95% событий при нагрузке до N событий в секунду; время считается от \<момента начала\> до \<момента окончания\>», и что делать, если срок не выдержан.
- Вопрос автору: [В-04](#v-04)
- Источник: автоматическое правило `time-without-conditions`
- Основание: ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Verifiable), IEEE 830-1998 п. 4.3.6

<a id="req-023"></a>
### REQ-023 · 3.2. GET /v1/onboarding/\{sessionId\}/status · строка 60

> Returns the verification status of the session. Authorization is not required for this endpoint so that the web landing page can poll it.

Тип: интерфейс (API) · Ясность: 9,6 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.2. GET /v1/onboarding/\{sessionId\}/status

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись. Но требование входит в замечания, охватывающие несколько мест документа, — они снижают его ясность (см. ниже).

См. также: [D-001](#d-001) · 🟢 MINOR — У большинства требований нет номеров

<a id="req-024"></a>
### REQ-024 · 3.2. GET /v1/onboarding/\{sessionId\}/status · строки 62–68

> Response 200 OK:
> \{"sessionId": "3f1c9a52-0000-4000-8000-000000000001", "status": "PROCESSING",
> "fullName": "TEST APPLICANT", "iin": "000000000000", "birthDate": "1990-01-01",
> "documentNumber": "000000000"\}

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.2. GET /v1/onboarding/\{sessionId\}/status

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-025"></a>
### REQ-025 · 3.2. GET /v1/onboarding/\{sessionId\}/status · строка 70

> Session status values: DRAFT, SUBMITTED, PROCESSING, VERIFIED, REJECTED.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.2. GET /v1/onboarding/\{sessionId\}/status

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-026"></a>
### REQ-026 · 3.3. GET /v1/onboarding/\{sessionId\}/documents/\{documentId\}/delete · строка 74

> Removes a previously uploaded document. A document can be removed only while the session status is DRAFT; otherwise the server returns 400.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.3. GET /v1/onboarding/\{sessionId\}/documents/\{documentId\}/delete

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-027"></a>
### REQ-027 · 3.4. POST /v1/onboarding/\{sessionId\}/submit · строка 78

> Submits all uploaded documents for verification. The session must contain an ID card (both sides) or a passport, and one selfie; otherwise 400 with \{"error": "INCOMPLETE\_SET"\}. On success the status becomes SUBMITTED and the server returns 202 Accepted.

Тип: интерфейс (API) · Ясность: 8,8 из 10 (есть замечания) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.4. POST /v1/onboarding/\{sessionId\}/submit

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись. Но требование входит в замечания, охватывающие несколько мест документа, — они снижают его ясность (см. ниже).

См. также: [D-006](#d-006) · 🟡 MAJOR — Код ответа 202 возвращается, но не описан

<a id="req-028"></a>
### REQ-028 · 3.4. POST /v1/onboarding/\{sessionId\}/submit · строка 80

> The verification provider is called asynchronously. When it responds, the status changes to PROCESSING and then to APPROVED or REJECTED. If the verification provider fails, the session is handled accordingly.

Тип: интерфейс (API) · Ясность: 7,5 из 10 (есть замечания) · Раздел: Remote Onboarding — Identity Document Upload API › 3. Endpoints › 3.4. POST /v1/onboarding/\{sessionId\}/submit

Критерии с замечаниями правил: Однозначность. Остальные критерии без модели не проверялись.

<a id="d-007"></a>
**D-007 · 🟡 MAJOR · Однозначность.** Размытая формулировка: «accordingly»

Слово «accordingly» каждый понимает по-своему: заказчик, разработчик и тестировщик представят разное. По такой формулировке нельзя написать тест с однозначным ожидаемым результатом.

- Цитата (строка 80): «If the verification provider fails, the session is handled accordingly»
- Как исправить: Замените «accordingly» проверяемым условием: опишите, что именно должно быть сделано: конкретное действие, его результат и как это проверить.
- Вопрос автору: [В-06](#v-06)
- Источник: автоматическое правило `vague-word`
- Основание: ISO/IEC/IEEE 29148:2018 п. 5.2.5 (Unambiguous), IEEE 830-1998 п. 4.3.2

<a id="req-029"></a>
### REQ-029 · 4.1 · 4\. Image quality · строка 88

> 4.1. Minimum resolution of a document image is 1280×720 pixels; a selfie is at least 640×480 pixels.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 4. Image quality

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-030"></a>
### REQ-030 · 4.2 · 4\. Image quality · строка 89

> 4.2. Glare and blur on the document image must be within acceptable limits.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 4. Image quality

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-031"></a>
### REQ-031 · 4\. Image quality · строка 91

> Liveness of the selfie is checked by the FaceCheck SDK 4.2 library on the server in a dedicated thread pool of 8 workers.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 4. Image quality

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-032"></a>
### REQ-032 · 5\. Limits · строка 97

> Limit: Maximum file size \| Value: 8 000 KB

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 5. Limits

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-033"></a>
### REQ-033 · 5\. Limits · строка 98

> Limit: Supported formats \| Value: JPEG, PNG, HEIC

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 5. Limits

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-034"></a>
### REQ-034 · 5\. Limits · строка 99

> Limit: Uploads per session \| Value: at most 20

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 5. Limits

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-035"></a>
### REQ-035 · 5\. Limits · строка 100

> Limit: Rate limit \| Value: 30 requests per applicant

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 5. Limits

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-036"></a>
### REQ-036 · 6.1 · 6\. Data retention · строка 104

> 6.1. Document images and selfies are stored for 90 days after the onboarding session ends and are then permanently deleted.

Тип: данные · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 6. Data retention

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-037"></a>
### REQ-037 · 6.2 · 6\. Data retention · строка 105

> 6.2. Images are stored encrypted with AES-256; keys are managed by the bank's HSM.

Тип: данные · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 6. Data retention

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-038"></a>
### REQ-038 · 7.1 · 7\. Audit · строка 109

> 7.1. Every upload, deletion and status change is written to the audit log with sessionId, documentId, operation and timestamp.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 7. Audit

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-039"></a>
### REQ-039 · 7.2 · 7\. Audit · строка 110

> 7.2. Original document images are retained for the whole term of the business relationship and for 5 years after it ends, as required by AML regulations.

Тип: интерфейс (API) · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 7. Audit

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-040"></a>
### REQ-040 · AC-1 · 8\. Acceptance criteria · строка 116

> \#: AC-1 \| Given: Session in DRAFT \| When: Upload a 2 MB JPEG ID\_CARD FRONT \| Then: 201, status RECEIVED

Тип: критерий приёмки · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 8. Acceptance criteria

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-041"></a>
### REQ-041 · AC-2 · 8\. Acceptance criteria · строка 117

> \#: AC-2 \| Given: Session in DRAFT \| When: Upload a GIF file \| Then: 415

Тип: критерий приёмки · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 8. Acceptance criteria

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-042"></a>
### REQ-042 · AC-3 · 8\. Acceptance criteria · строка 118

> \#: AC-3 \| Given: Any session \| When: Repeat POST with the same Idempotency-Key within 24 hours \| Then: Original response, no new document

Тип: критерий приёмки · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 8. Acceptance criteria

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-043"></a>
### REQ-043 · AC-4 · 8\. Acceptance criteria · строка 119

> \#: AC-4 \| Given: Session in SUBMITTED \| When: Delete a document \| Then: 400

Тип: критерий приёмки · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 8. Acceptance criteria

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="req-044"></a>
### REQ-044 · AC-5 · 8\. Acceptance criteria · строка 120

> \#: AC-5 \| Given: Session with 3 rejected selfies \| When: Upload a 4th selfie \| Then: 423 Locked for 24 hours

Тип: критерий приёмки · Ясность: 10,0 из 10 (ясное) · Раздел: Remote Onboarding — Identity Document Upload API › 8. Acceptance criteria

В самой формулировке правила нарушений не нашли. Смысловые критерии без модели не проверялись.

<a id="s4"></a>
## 4. Проверка всего набора требований

Здесь замечания к документу в целом: противоречия между разделами, пропуски, стандарты, термины, нумерация. Без модели — только то, что находят автоматические правила.

### 4.1 Полнота набора

<a id="d-008"></a>
**D-008 · 🟡 MAJOR · Полнота набора.** Раздел «3.5. Callback to the core banking system» не заполнен: вместо содержания заглушка

Вместо содержания раздела стоит заглушка «TBD.». Значит, эта часть требований не готова: по ней нельзя начать работу и нельзя проверить результат, а пробел легко пропустить, потому что раздел формально есть.

- Цитаты:
  - строка 82: «3.5. Callback to the core banking system»
  - строка 84: «TBD.»
- Как исправить: Заполните раздел до начала разработки: для раздела о тестировании — что проверяется, на каких данных, кто и когда проводит проверку, какие критерии приёмки. Если раздел заполняет другая команда — назовите ответственного и срок.
- Вопрос автору: [В-07](#v-07)
- Источник: автоматическое правило `empty-section`
- Основание: ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Complete), IEEE 830-1998 п. 4.3.3

### 4.2 Взаимная непротиворечивость

<a id="d-003"></a>
**D-003 · 🟡 MAJOR · Взаимная непротиворечивость.** Код ответа 409 возвращается, но не описан

В тексте код 409 возвращается, но в описании ответов для него нет ни условия, ни примера тела ответа. Разработчик не знает, когда его возвращать, а тестировщик — что проверять.

- Цитата (строка 54, [REQ-021](#req-021)): «If a document of the same docType and side has already been uploaded in the session, the server returns 409»
- Как исправить: Добавьте описание кода 409: при каком условии он возвращается и пример тела ответа; или уберите упоминание.
- Вопрос автору: [В-03](#v-03)
- Источник: автоматическое правило `status-mismatch`
- Основание: ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Consistent), IEEE 830-1998 п. 4.3.4

<a id="d-006"></a>
**D-006 · 🟡 MAJOR · Взаимная непротиворечивость.** Код ответа 202 возвращается, но не описан

В тексте код 202 возвращается, но в описании ответов для него нет ни условия, ни примера тела ответа. Разработчик не знает, когда его возвращать, а тестировщик — что проверять.

- Цитата (строка 78, [REQ-027](#req-027)): «On success the status becomes SUBMITTED and the server returns 202 Accepted»
- Как исправить: Добавьте описание кода 202: при каком условии он возвращается и пример тела ответа; или уберите упоминание.
- Вопрос автору: [В-05](#v-05)
- Источник: автоматическое правило `status-mismatch`
- Основание: ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Consistent), IEEE 830-1998 п. 4.3.4

### 4.3 Реализуемость, стандарты и безопасность

<a id="d-002"></a>
**D-002 · 🟡 MAJOR · Реализуемость, стандарты и безопасность.** Чувствительные данные передаются в адресной строке запроса

Параметры «iin» передаются в строке запроса (query). Адрес запроса целиком попадает в журналы веб-серверов и прокси, историю браузера и системы мониторинга — идентификаторы карт, счетов, телефонов и токены окажутся там в открытом виде. Это противоречит требованиям к защите платёжных и персональных данных (PCI DSS).

- Цитата (строки 41–43, [REQ-014](#req-014)): «curl -X POST "\$BASE/v1/onboarding/3f1c9a52-0000-4000-8000-000000000001/documents?iin=000000000000" \\ -H "Authorization: Bearer \$TOKEN" -H "Idempotency-Key: 7d4e0c1a-0000-4000-8000-000000000002" \\ -F docType=PHOTO -F file=\@selfie.jpg»
- Как исправить: Передавайте такие значения в теле запроса (POST/PUT/PATCH), а токены — только в заголовке Authorization; вместо номера карты используйте внутренний непрозрачный идентификатор и запретите запись этих значений в журналы.
- Вопрос автору: [В-02](#v-02)
- Источник: автоматическое правило `sensitive-in-query`
- Основание: ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Feasible)

<a id="d-005"></a>
**D-005 · 🔴 CRITICAL · Реализуемость, стандарты и безопасность.** Метод GET используется для изменения данных

Запрос описан как GET, но по пути или описанию он сохраняет или меняет данные («/v1/onboarding/\{sessionId\}/documents/\{d…»). По стандарту HTTP (RFC 9110, п. 9.2.1) GET — безопасный метод, он только читает. Браузеры, прокси и кэши вправе повторять и кэшировать такие запросы, а параметры попадают в адресную строку и журналы: изменение может выполниться повторно или без ведома клиента.

- Цитата (строка 72): «3.3. GET /v1/onboarding/\{sessionId\}/documents/\{documentId\}/delete»
- Как исправить: Используйте PUT или PATCH для изменения настройки (POST — для создания новой записи) и передавайте параметры в теле запроса в формате JSON; GET оставьте только для чтения.
- Вопрос автору: [В-01](#v-01)
- Источник: автоматическое правило `get-mutation`
- Основание: ISO/IEC/IEEE 29148:2018 п. 5.2.6 (Feasible)

### 4.4 Понятность и термины

Правилами нарушений не найдено; без модели этот критерий проверен лишь частично.

### 4.5 Прослеживаемость

<a id="d-001"></a>
**D-001 · 🟢 MINOR · Прослеживаемость.** У большинства требований нет номеров

Номер из документа есть только у 17 из 44 требований. Без номера на требование нельзя сослаться в задаче, тест-кейсе или замечании, и трудно проверить, что каждое требование покрыто тестом. Разделы без номеров: «1. Purpose», «3.1. POST /v1/onboarding/\{sessionId\}/documents», «3.2. GET /v1/onboarding/\{sessionId\}/status», «3.3. GET /v1/onboarding/\{sessionId\}/documents/\{documentId\}/delete», «3.4. POST /v1/onboarding/\{sessionId\}/submit», «4. Image quality», «5. Limits».

- Цитаты:
  - строка 13, [REQ-001](#req-001): «The API lets the mobile app upload images of the applicant's identity document (ID card of the Republic of Kazakhstan or passport) and a selfie during remote onboarding, submit them for verification and query the verification status.»
  - строка 28, [REQ-008](#req-008): «Uploads one document image. Request body: multipart/form-data.»
  - строка 60, [REQ-023](#req-023): «Returns the verification status of the session. Authorization is not required for this endpoint so that the web landing page can poll it.»
- Как исправить: Присвойте каждому требованию уникальный номер (например, Ф-5, НФТ-1, ДАН-1, API-3) и ссылайтесь на эти номера в критериях приёмки.
- Вопрос автору: [В-08](#v-08)
- Источник: автоматическое правило `missing-ids`
- Основание: IEEE 830-1998 п. 4.3.8 (Traceable)

Покрытие требований критериями приёмки строит модель; без неё оно не оценивалось.

<a id="s4-obs"></a>
### Наблюдения (проверьте сами, на итог не влияют)

Эти признаки находятся словарными правилами низкой точности: часто это нормальная формулировка. Они не входят в число дефектов, индексы и вердикт.

- <a id="n-01"></a>**Н-01** · строка 17 · [REQ-002](#req-002) · Обобщение «All» без границ: «All endpoints are served over HTTPS only, TLS 1.2 or higher; plain HTTP requests are rejected without redirect»
- <a id="n-02"></a>**Н-02** · строка 18 · [REQ-003](#req-003) · Обобщение «Every» без границ: «Every request carries Authorization: Bearer \<access\_token\> issued to the onboarding session»
- <a id="n-03"></a>**Н-03** · строка 19 · [REQ-004](#req-004) · Обобщение «All» без границ: «All timestamps are ISO 8601 strings in UTC, e.g. 2026-11-12T09:30:00Z»
- <a id="n-04"></a>**Н-04** · строка 20 · [REQ-005](#req-005) · Обобщение «Every» без границ: «Every POST request must carry an Idempotency-Key header (UUID v4)»
- <a id="n-05"></a>**Н-05** · строка 74 · [REQ-026](#req-026) · В одном пункте несколько утверждений: «Removes a previously uploaded document. A document can be removed only while the session status is DRAFT; otherwise the server returns 400.»
- <a id="n-06"></a>**Н-06** · строка 78 · [REQ-027](#req-027) · В одном пункте несколько утверждений: «Submits all uploaded documents for verification. The session must contain an ID card (both sides) or a passport, and one selfie; otherwise 400 with \{"error": "INCOMPLETE\_SET"\}. On success the status… (цитата сокращена)»
- <a id="n-07"></a>**Н-07** · строка 78 · [REQ-027](#req-027) · Обобщение «all» без границ: «Submits all uploaded documents for verification»
- <a id="n-08"></a>**Н-08** · строка 80 · [REQ-028](#req-028) · В одном пункте несколько утверждений: «The verification provider is called asynchronously. When it responds, the status changes to PROCESSING and then to APPROVED or REJECTED. If the verification provider fails, the session is handled… (цитата сокращена)»
- <a id="n-09"></a>**Н-09** · строка 109 · [REQ-038](#req-038) · Обобщение «Every» без границ: «Every upload, deletion and status change is written to the audit log with sessionId, documentId, operation and timestamp»

<a id="s5"></a>
## 5. Сводка

| Показатель | Значение |
| :--- | ---: |
| Требований всего | 44 |
| — без замечаний | 36 |
| — с замечаниями | 8 |
| Дефектов всего | 8 |
| — 🔴 CRITICAL (блокеры) | 1 |
| — 🟡 MAJOR (существенные) | 6 |
| — 🟢 MINOR (замечания) | 1 |
| Наблюдений (на итог не влияют) | 9 |
| Вопросов автору | 8 |
| Тестов можно написать уже сейчас | 5 |
| Тестов нельзя написать без ответа автора | 5 |

**Дефекты по критериям качества**

| Критерий | Дефектов |
| :--- | ---: |
| Однозначность | 1 |
| Проверяемость | 1 |
| Полнота набора | 1 |
| Взаимная непротиворечивость | 2 |
| Реализуемость, стандарты и безопасность | 2 |
| Прослеживаемость | 1 |

**Индекс готовности документа**

**77 из 100 — готов после правок** (предварительно: применены только правила). Шкала: 85 и выше — готов, 60–84 — готов после правок, 40–59 — нужна существенная доработка, ниже 40 — не готов.

Как посчитан: 10 × 9,78 (средняя ясность 44 треб.) − 21 (штраф за замечания к набору: 8×1 + 3×4 + 1×1 = 21) = 77. Ясность требования = 10 − 6 за каждый блокер, − 2,5 за существенное, − 0,75 за мелкое замечание (замечания к набору, где требование упомянуто, — вдвое меньше), но не ниже 1.

Требования по ясности: ясное — 39 · есть замечания — 5 · нужна доработка — 0 · в таком виде использовать нельзя — 0.

Плотность: 0,18 дефекта на одно требование · 9,8 на 1000 слов документа.
Вердикт определяют блокеры, а не индекс: индекс показывает, сколько работы осталось.

**Критичные дефекты**

- [D-005](#d-005) — Метод GET используется для изменения данных (строка 72)

**Время и стоимость**

- Агент: 0,0 с, $0 (модель не вызывалась).
- Ручное ревью (оценка, не замер): 44 треб. × 4 мин + 45 мин на отчёт ≈ 3 ч 41 мин.

<a id="s6"></a>
## 6. Итог

**⛔ Начинать разработку и тестирование нельзя: сначала устранить блокеры (модель не использовалась: проверены только автоматические правила, итог предварительный)**

Найдено блокеров: 1 (Метод GET используется для изменения данных). Пока они не устранены, разработчик и тестировщик будут работать по описанию, которое противоречит себе или общепринятым стандартам, и часть работы придётся переделывать. Проверка выполнена только автоматическими правилами: смысловые противоречия, пропущенные сценарии и пограничные случаи без модели не ищутся.

**Что исправить в первую очередь**

1. Метод GET используется для изменения данных. Используйте PUT или PATCH для изменения настройки (POST — для создания новой записи) и передавайте параметры в теле запроса в формате JSON; GET оставьте только для чтения. ([D-005](#d-005))
2. Чувствительные данные передаются в адресной строке запроса. Передавайте такие значения в теле запроса (POST/PUT/PATCH), а токены — только в заголовке Authorization; вместо номера карты используйте внутренний непрозрачный идентификатор и запретите запись этих значений в журналы. ([D-002](#d-002))
3. Код ответа 409 возвращается, но не описан. Добавьте описание кода 409: при каком условии он возвращается и пример тела ответа; или уберите упоминание. ([D-003](#d-003))
4. Ограничение по времени без условий измерения. Допишите условия измерения: «within 3 seconds для 95% событий при нагрузке до N событий в секунду; время считается от \<момента начала\> до \<момента окончания\>», и что делать, если срок не выдержан. ([D-004](#d-004))
5. Код ответа 202 возвращается, но не описан. Добавьте описание кода 202: при каком условии он возвращается и пример тела ответа; или уберите упоминание. ([D-006](#d-006))
6. Размытая формулировка: «accordingly». Замените «accordingly» проверяемым условием: опишите, что именно должно быть сделано: конкретное действие, его результат и как это проверить. ([D-007](#d-007))
7. Раздел «3.5. Callback to the core banking system» не заполнен: вместо содержания заглушка. Заполните раздел до начала разработки: для раздела о тестировании — что проверяется, на каких данных, кто и когда проводит проверку, какие критерии приёмки. Если раздел заполняет другая команда — назовите ответственного и срок. ([D-008](#d-008))

**Что в документе уже хорошо**

Без модели сильные стороны документа не оцениваются.

<a id="s7"></a>
## 7. Уточняющие вопросы к автору ТЗ

Если автор ответит на блокирующие вопросы и внесёт правки, вердикт станет: **⚠️ Начинать можно только после ответов на вопросы и исправления существенных замечаний (модель не использовалась: проверены только автоматические правила, итог предварительный)**.

8 вопросов составлено по шаблонам автоматических правил. Ответы удобно вписать в файл-анкету рядом с отчётом (`*.questions.md`).

### Блокирующие (1)

<a id="v-01"></a>
**В-01.** Какой HTTP-метод использовать для этого изменения?

- Кому: архитектор или разработчик · Тип пробела: не описан сценарий
- Почему важно: Запрос описан как GET, но по пути или описанию он сохраняет или меняет данные («/v1/onboarding/\{sessionId\}/documents/\{d…»).
- Где в документе: строка 72: «3.3. GET /v1/onboarding/\{sessionId\}/documents/\{documentId\}/delete»
- Варианты ответа:
  - а) PUT — полная замена настройки
  - б) PATCH — изменение отдельных полей
  - в) POST — создание новой записи
- Относится к: [D-005](#d-005)

### Важные (6)

<a id="v-02"></a>
**В-02.** Как передавать идентификатор карты и другие чувствительные значения?

- Кому: информационная безопасность · Тип пробела: не описан сценарий
- Почему важно: Параметры «iin» передаются в строке запроса (query).
- Где в документе: строки 41–43, [REQ-014](#req-014): «curl -X POST "\$BASE/v1/onboarding/3f1c9a52-0000-4000-8000-000000000001/documents?iin=000000000000" \\ -H "Authorization: Bearer \$TOKEN" -H "Idempotency-Key: 7d4e0c1a-0000-4000-8000-000000000002" \\ -F docType=PHOTO -F file=\@selfie.jpg»
- Варианты ответа:
  - а) В теле запроса
  - б) В пути в виде внутреннего непрозрачного идентификатора
  - в) Оставить в query (обосновать для ИБ)
- Относится к: [D-002](#d-002), [REQ-014](#req-014)

<a id="v-03"></a>
**В-03.** Возвращает ли сервис код 409?

- Кому: архитектор или разработчик · Тип пробела: два места говорят разное
- Почему важно: В тексте код 409 возвращается, но в описании ответов для него нет ни условия, ни примера тела ответа.
- Где в документе: строка 54, [REQ-021](#req-021): «If a document of the same docType and side has already been uploaded in the session, the server returns 409»
- Варианты ответа:
  - а) Да — добавить описание
  - б) Нет — убрать упоминание
- Относится к: [D-003](#d-003), [REQ-021](#req-021)

<a id="v-04"></a>
**В-04.** Для какой доли случаев и при какой нагрузке должен выполняться срок «within 3 seconds»?

- Кому: владелец продукта (бизнес) · Тип пробела: не задано значение, единица или граница
- Почему важно: Задан срок «within 3 seconds», но не сказано, для какой доли случаев он должен выполняться (например, 95% или 99%), при какой нагрузке и от какого до какого момента его считать.
- Где в документе: строка 56, [REQ-022](#req-022): «The upload endpoint responds within 3 seconds»
- Варианты ответа:
  - а) 95% случаев при обычной нагрузке
  - б) 99% случаев при пиковой нагрузке (указать какой)
  - в) Каждый случай без исключений
- Относится к: [D-004](#d-004), [REQ-022](#req-022)

<a id="v-05"></a>
**В-05.** Возвращает ли сервис код 202?

- Кому: архитектор или разработчик · Тип пробела: два места говорят разное
- Почему важно: В тексте код 202 возвращается, но в описании ответов для него нет ни условия, ни примера тела ответа.
- Где в документе: строка 78, [REQ-027](#req-027): «On success the status becomes SUBMITTED and the server returns 202 Accepted»
- Варианты ответа:
  - а) Да — добавить описание
  - б) Нет — убрать упоминание
- Относится к: [D-006](#d-006), [REQ-027](#req-027)

<a id="v-06"></a>
**В-06.** Что конкретно означает «accordingly» в этом требовании?

- Кому: аналитик — автор документа · Тип пробела: неясное слово или термин
- Почему важно: Слово «accordingly» каждый понимает по-своему: заказчик, разработчик и тестировщик представят разное.
- Где в документе: строка 80, [REQ-028](#req-028): «If the verification provider fails, the session is handled accordingly»
- Варианты ответа:
  - а) Задать число и условия измерения (дописать в требование)
  - б) Перечислить случаи, когда это применяется
  - в) Убрать слово — оно ничего не добавляет
- Относится к: [D-007](#d-007), [REQ-028](#req-028)

<a id="v-07"></a>
**В-07.** Кто и когда заполнит раздел «3.5. Callback to the core banking system»?

- Кому: аналитик — автор документа · Тип пробела: не описан сценарий
- Почему важно: Вместо содержания раздела стоит заглушка «TBD.».
- Где в документе: строка 82: «3.5. Callback to the core banking system»
- Варианты ответа:
  - а) Автор документа — до начала разработки
  - б) Другая команда — указать ответственного и срок
  - в) Раздел не нужен — удалить
- Относится к: [D-008](#d-008)

### Желательные (1)

<a id="v-08"></a>
**В-08.** Какую схему номеров использовать для требований без номера?

- Кому: аналитик — автор документа · Тип пробела: не назначен ответственный или источник
- Почему важно: Номер из документа есть только у 17 из 44 требований.
- Где в документе: строка 13, [REQ-001](#req-001): «The API lets the mobile app upload images of the applicant's identity document (ID card of the Republic of Kazakhstan or passport) and a selfie during remote onboarding, submit them for verification and query the verification status.»
- Варианты ответа:
  - а) Продолжить существующую нумерацию на все разделы
  - б) Свой префикс для каждого раздела (НФТ-, ДАН-, API-)
- Относится к: [D-001](#d-001), [REQ-001](#req-001), [REQ-008](#req-008), [REQ-023](#req-023)

<a id="s8"></a>
## 8. Черновики приёмочных тестов

Можно написать уже сейчас: 5 · Нельзя написать без ответа автора: 5. Полные тексты (Дано / Когда / Тогда) — в файле `*.tests.md` рядом с отчётом.

| Тест | Тип | Требования | Что проверяем | Статус |
| :--- | :--- | :--- | :--- | :--- |
| <a id="t-01"></a>Т-01 | позитивный | [REQ-014](#req-014) | REQ-014: Example: curl -X POST "\$BASE/v1/onboarding/ /documents?iin=000000000000" \\ -H… | ожидаемый результат в документе не описан — нужен ответ автора ([В-02](#v-02)) |
| <a id="t-02"></a>Т-02 | позитивный | [REQ-021](#req-021) | REQ-021: If a document of the same docType and side has already been uploaded in the session, the… | ожидаемый результат в документе не описан — нужен ответ автора ([В-03](#v-03)) |
| <a id="t-03"></a>Т-03 | позитивный | [REQ-022](#req-022) | REQ-022: The upload endpoint responds within 3 seconds | ожидаемый результат в документе не описан — нужен ответ автора ([В-04](#v-04)) |
| <a id="t-04"></a>Т-04 | позитивный | [REQ-027](#req-027) | REQ-027: Submits all uploaded documents for verification | ожидаемый результат в документе не описан — нужен ответ автора ([В-05](#v-05)) |
| <a id="t-05"></a>Т-05 | позитивный | [REQ-028](#req-028) | REQ-028: The verification provider is called asynchronously | ожидаемый результат в документе не описан — нужен ответ автора ([В-06](#v-06)) |
| <a id="t-06"></a>Т-06 | позитивный | [REQ-040](#req-040) | REQ-040: Upload a 2 MB JPEG ID\_CARD FRONT | готов |
| <a id="t-07"></a>Т-07 | позитивный | [REQ-041](#req-041) | REQ-041: Upload a GIF file | готов |
| <a id="t-08"></a>Т-08 | позитивный | [REQ-042](#req-042) | REQ-042: Repeat POST with the same Idempotency-Key within 24 hours | готов |
| <a id="t-09"></a>Т-09 | позитивный | [REQ-043](#req-043) | REQ-043: Delete a document | готов |
| <a id="t-10"></a>Т-10 | позитивный | [REQ-044](#req-044) | REQ-044: Upload a 4th selfie | готов |

<a id="app"></a>
## Приложение. Как получен отчёт

**Метод.**

1. Документ читается и делится на блоки (абзацы, пункты, строки таблиц); номера строк берутся из исходного текста. Значения, похожие на номера карт и ИИН, маскируются до любой обработки.
2. Блоки делятся на требования и служебные части эвристикой; номера REQ-… присваивает программа.
3. Автоматические правила (набор 1.0) ищут формальные дефекты: размытые слова, заглушки, единицы измерения, коды и методы HTTP, пустые разделы, подсадной текст для ИИ.
4. Модель не использовалась: проверки по смыслу (противоречия, пропущенные сценарии, пограничные случаи) не выполнялись.
5. Каждую цитату программа ищет в тексте документа; замечание без найденной цитаты выбрасывается, в отчёт попадает исходный текст, а не пересказ. Номера, индексы и вердикт считает программа.

**Честно о границах.** Это первый проход, который экономит время проверяющего; решение остаётся за человеком. Опубликованные замеры ИИ-проверки требований показывают, что лучшие модели находят примерно половину настоящих проблем и ошибочно помечают около одного требования из десяти. Поэтому цитаты сверяет программа, а полнота агента измеряется на контрольных документах, которых он раньше не видел. В этом прогоне модель не работала, поэтому найдено заведомо меньше.

**Отклонено при перепроверке**

Перепроверка не выполнялась.

**Ограничения этого прогона**

- Модель не использовалась: найдено только то, что выявляют правила по формальным признакам.
- Рисунки и схемы-картинки не проверяются: учитывается только текст.
- Агент не знает внутренних стандартов банка и не видит связанных документов, если их не передали через --context.

**Журнал решений** (JSON-версия отчёта, поле `audit`; только хэши текстов, без содержимого документа): 0 обращений к модели, 0 решений программы (отброшенные цитаты, объединения, защита замечаний правил, переходы без модели).
