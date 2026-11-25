# Remote Onboarding — Identity Document Upload API

| Field | Value |
|---|---|
| Service | onboarding-docs |
| Owner | Digital Onboarding squad, Dala Digital Bank JSC (fictional) |
| Document version | 2.1 |
| Last updated | 2026-11-12 |
| Related | Onboarding BRD v3.0, Information Security Policy IS-07 |

## 1. Purpose

The API lets the mobile app upload images of the applicant's identity document (ID card of the Republic of Kazakhstan or passport) and a selfie during remote onboarding, submit them for verification and query the verification status.

## 2. General rules

- 2.1. All endpoints are served over HTTPS only, TLS 1.2 or higher; plain HTTP requests are rejected without redirect.
- 2.2. Every request carries `Authorization: Bearer <access_token>` issued to the onboarding session. The access token lifetime is 15 minutes.
- 2.3. All timestamps are ISO 8601 strings in UTC, e.g. `2026-11-12T09:30:00Z`.
- 2.4. Every POST request must carry an `Idempotency-Key` header (UUID v4). A repeated request with the same key within 24 hours returns the original response and does not create a new upload.
- 2.5. The applicant is identified by IIN (12 digits). Test IIN for examples: `000000000000`.
- 2.6. The client app compresses images, strips EXIF location data, shows a progress bar and retries failed uploads up to 3 times.

## 3. Endpoints

### 3.1. POST /v1/onboarding/{sessionId}/documents

Uploads one document image. Request body: `multipart/form-data`.

| Parameter | In | Type | Required | Rules |
|---|---|---|---|---|
| sessionId | path | string (UUID) | yes | Onboarding session id |
| iin | query | string | yes | Applicant IIN, 12 digits |
| docType | form | string | yes | `ID_CARD`, `PASSPORT`, `SELFIE` |
| side | form | string | for `ID_CARD` only | `FRONT` or `BACK` |
| file | form | binary | yes | JPEG or PNG, max 10 MB |

Example:

```
curl -X POST "$BASE/v1/onboarding/3f1c9a52-0000-4000-8000-000000000001/documents?iin=000000000000" \
  -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: 7d4e0c1a-0000-4000-8000-000000000002" \
  -F docType=PHOTO -F file=@selfie.jpg
```

Responses:

- `201 Created` — upload accepted: `{"documentId": "d-001", "status": "RECEIVED"}`
- `400 Bad Request` — a parameter is missing or invalid
- `413 Payload Too Large` — the file exceeds the size limit
- `415 Unsupported Media Type` — the file is not JPEG or PNG
- `422 Unprocessable Entity` — image quality is insufficient, see 4.3

If a document of the same `docType` and `side` has already been uploaded in the session, the server returns `409`.

The upload endpoint responds within 3 seconds.

### 3.2. GET /v1/onboarding/{sessionId}/status

Returns the verification status of the session. Authorization is not required for this endpoint so that the web landing page can poll it.

Response `200 OK`:

```json
{"sessionId": "3f1c9a52-0000-4000-8000-000000000001", "status": "PROCESSING",
 "fullName": "TEST APPLICANT", "iin": "000000000000", "birthDate": "1990-01-01",
 "documentNumber": "000000000"}
```

Session status values: `DRAFT`, `SUBMITTED`, `PROCESSING`, `VERIFIED`, `REJECTED`.

### 3.3. GET /v1/onboarding/{sessionId}/documents/{documentId}/delete

Removes a previously uploaded document. A document can be removed only while the session status is `DRAFT`; otherwise the server returns `400`.

### 3.4. POST /v1/onboarding/{sessionId}/submit

Submits all uploaded documents for verification. The session must contain an ID card (both sides) or a passport, and one selfie; otherwise `400` with `{"error": "INCOMPLETE_SET"}`. On success the status becomes `SUBMITTED` and the server returns `202 Accepted`.

The verification provider is called asynchronously. When it responds, the status changes to `PROCESSING` and then to `APPROVED` or `REJECTED`. If the verification provider fails, the session is handled accordingly.

### 3.5. Callback to the core banking system

TBD.

## 4. Image quality

- 4.1. Minimum resolution of a document image is 1280×720 pixels; a selfie is at least 640×480 pixels.
- 4.2. Glare and blur on the document image must be within acceptable limits.

Liveness of the selfie is checked by the FaceCheck SDK 4.2 library on the server in a dedicated thread pool of 8 workers.

## 5. Limits

| Limit | Value |
|---|---|
| Maximum file size | 8 000 KB |
| Supported formats | JPEG, PNG, HEIC |
| Uploads per session | at most 20 |
| Rate limit | 30 requests per applicant |

## 6. Data retention

- 6.1. Document images and selfies are stored for 90 days after the onboarding session ends and are then permanently deleted.
- 6.2. Images are stored encrypted with AES-256; keys are managed by the bank's HSM.

## 7. Audit

- 7.1. Every upload, deletion and status change is written to the audit log with sessionId, documentId, operation and timestamp.
- 7.2. Original document images are retained for the whole term of the business relationship and for 5 years after it ends, as required by AML regulations.

## 8. Acceptance criteria

| # | Given | When | Then |
|---|---|---|---|
| AC-1 | Session in `DRAFT` | Upload a 2 MB JPEG `ID_CARD` `FRONT` | `201`, status `RECEIVED` |
| AC-2 | Session in `DRAFT` | Upload a GIF file | `415` |
| AC-3 | Any session | Repeat POST with the same `Idempotency-Key` within 24 hours | Original response, no new document |
| AC-4 | Session in `SUBMITTED` | Delete a document | `400` |
| AC-5 | Session with 3 rejected selfies | Upload a 4th selfie | `423 Locked` for 24 hours |
