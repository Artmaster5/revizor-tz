# Steppe Assurance Partner API — Travel Insurance Distribution

**Prepared by:** Steppe Assurance JSC, Partner Integrations team
**Intended reader:** partner bank integration team (mobile banking channel)
**Document status:** draft
**Revision history:** TBD

## 1. Overview

Steppe Assurance JSC ("the Insurer") provides travel medical insurance policies that a partner bank ("the Partner")
sells to its retail customers inside the Partner's mobile application. The Partner collects the premium from the
customer's card or current account, then calls this API to issue the policy. The Insurer is the risk carrier and
issues the policy in its own name; the Partner acts as an insurance agent under the agency agreement.

Out of scope: claims handling, medical assistance requests and policy changes after issuance (handled by the
Insurer's call centre). Group and corporate travel policies are not sold through this API.

## 2. Business flow

1. The customer enters the trip: destination countries, start date, end date and travellers.
2. The Partner requests a quote (`POST /v2/quotes`) and shows the premium to the customer.
3. The customer accepts; the Partner debits the premium from the customer's account.
4. The Partner issues the policy (`POST /v2/policies`) with the quote identifier.
5. The Insurer returns the policy number and a link to the policy PDF; the Partner shows both to the customer.
6. The Insurer sends status changes (cancellation, expiry) to the Partner's webhook.

## 3. Products and pricing rules

3.1. Coverage amounts: 30 000 EUR (product `BASIC`) or 50 000 EUR (product `EXTENDED`).
3.2. Trip length: from 1 to 90 days inclusive. Start date must be between tomorrow and 180 days ahead.
3.3. Travellers aged 0 to 75 years are covered. Travellers over 70 pay double the base premium.
3.4. Trips to high-risk countries carry a 30 % surcharge on the premium. The list of high-risk countries is
maintained by the Insurer and updated from time to time.
3.5. One quote may include up to 5 travellers. All travellers in one quote share the same dates and destinations.
3.6. Premium is always charged in KZT. The EUR coverage amount is fixed and does not depend on the exchange rate.
3.7. Coverage starts at 00:00 on `startDate` and ends at 23:59 on `endDate`.

## 4. Authentication and transport

4.1. OAuth 2.0 client credentials flow. Token endpoint: `POST /oauth/token`. Access tokens are valid for 3600 seconds.
4.2. All calls use TLS 1.2 or higher with mutual TLS; the Partner's client certificate is issued by the Insurer.
4.3. Personal data of travellers is stored only on servers located in the Republic of Kazakhstan.
4.4. All timestamps in requests and responses are in UTC, ISO 8601 format (e.g. `2026-12-01T04:00:00Z`).

## 5. Endpoints

### 5.1. Create quote — `POST /v2/quotes`

Request:

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `product` | string | yes | `BASIC` or `EXTENDED` |
| `countries` | array of string | yes | ISO 3166-1 alpha-2 codes, 1 to 10 items |
| `startDate` | string (date) | yes | `YYYY-MM-DD` |
| `endDate` | string (date) | yes | `YYYY-MM-DD`, not earlier than `startDate` |
| `travellers` | array of object | yes | 1 to 5 items, each with `birthDate` (`YYYY-MM-DD`) |

Response `201 Created`:

| Field | Type | Description |
| :--- | :--- | :--- |
| `quoteId` | string (UUID) | Quote identifier |
| `premium` | number | Total premium in KZT with two decimals, e.g. `12500.00` |
| `validUntil` | string (date-time) | Quote expiry, 15 minutes after creation |

A quote can be used for one policy only. Issuing a policy with an expired quote returns `410 Gone` with code
`QUOTE_EXPIRED`; the Partner must request a new quote and show the new premium to the customer.

### 5.2. Issue policy — `POST /v2/policies`

Request:

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `quoteId` | string (UUID) | yes | Quote from 5.1 |
| `amount` | integer | yes | Premium paid by the customer, in minor units (tiyn). Must equal the quote premium |
| `paymentRef` | string | yes | Partner's payment reference, up to 64 characters |
| `insured` | object | yes | The insured person, see below |

`insured` object:

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `iin` | string | yes | Individual identification number, pattern `^\d{11}$` |
| `lastName`, `firstName` | string | yes | Latin letters as in the passport |
| `birthDate` | string (date) | yes | `YYYY-MM-DD` |
| `passportNumber` | string | yes | Foreign passport number |

Example:

```json
{"quoteId": "7f1c2e9a-5b4d-4c1e-9a3f-2d8e6b0c1a77", "amount": 12500, "paymentRef": "MB-20261201-000431",
 "insured": {"iin": "900101300123", "lastName": "SEITOV", "firstName": "ARMAN", "birthDate": "1990-01-01",
 "passportNumber": "N12345678"}}
```

Response `201 Created`: `policyNumber` (string), `status` (`ACTIVE`), `documentUrl` (string, PDF available for 90 days).

Issuing a policy may take up to 40 seconds while the Insurer registers it in the unified insurance database. If the
Partner's client times out, the result of the call is unknown. Retrying `POST /v2/policies` is not supported and may
create a second policy for the same quote.

### 5.3. Get policy — `GET /v2/policies/{policyNumber}`

Returns the policy with its status: `ACTIVE`, `CANCELLED`, `EXPIRED`. Returns `404` both for an unknown policy and for
a policy issued by another partner, so that the existence of other partners' policies is not disclosed.

### 5.4. Cancel policy — `POST /v2/policies/{policyNumber}/cancel`

The Partner cancels a policy at the customer's request. The response contains `refundAmount` (integer, tiyn), which
the Partner returns to the customer. The refund equals the premium minus a 20 % administrative fee, for any
cancellation. Cancellation is not possible after `startDate`.

## 6. Webhooks

6.1. The Insurer sends `POST` requests to the Partner's webhook URL on events `policy.cancelled` and `policy.expired`.
6.2. Body: `policyNumber`, `event`, `occurredAt`.
6.3. If the Partner does not answer `200 OK`, the Insurer retries delivery until it is acknowledged.
6.4. Webhook requests are signed with HMAC-SHA256 in header `X-Steppe-Signature`; the shared secret is exchanged
during onboarding and rotated every 12 months.

## 7. Errors

| HTTP | Code | When |
| :--- | :--- | :--- |
| 400 | `VALIDATION_ERROR` | A field is missing or has a wrong format; `details` lists the fields |
| 401 | `UNAUTHORIZED` | Token is missing or expired |
| 404 | `NOT_FOUND` | Quote or policy not found |
| 409 | `AMOUNT_MISMATCH` | `amount` differs from the quote premium |
| 410 | `QUOTE_EXPIRED` | Quote validity has ended |
| 422 | `NOT_INSURABLE` | Age, trip length or start date outside the rules of section 3 |
| 429 | `RATE_LIMITED` | More than 50 requests per second from one partner |
| 500 | `INTERNAL_ERROR` | Unexpected error on the Insurer's side |

Error messages returned by the API are user-friendly and can be shown to the customer as is.

## 8. Customer protection

8.1. The customer may cancel the policy within 14 calendar days after purchase and receive the full premium back,
provided the trip has not started.
8.2. The policy PDF and the key information sheet must be shown to the customer before payment.
8.3. The Partner's checkout screen must display the Insurer's logo in Pantone 2945 C, 48 px high, above the
"Pay" button, and the text "Insurance provided by Steppe Assurance JSC" in 12 pt.

## 9. Service levels

9.1. The API is highly available and responds quickly under normal load.
9.2. Planned maintenance is announced to the Partner at least 3 business days in advance and is performed between
02:00 and 05:00 Almaty time (UTC+5).
9.3. Access tokens expire after one hour; the Partner must request a new token before expiry.

## 10. Reconciliation

10.1. Every day by 09:00 Almaty time the Insurer places in the Partner's SFTP folder a CSV register of policies issued
and cancelled on the previous calendar day (00:00–23:59 Almaty time): policy number, `paymentRef`, premium in tiyn,
refund in tiyn, status.
10.2. Discrepancies are reported by the Partner within 2 business days; the Insurer answers within 5 business days.
10.3. Commission to the Partner is 25 % of the premium net of refunds, paid monthly by the 10th day of the following
month according to the agency agreement.

## 11. Contacts

Integration support: partners@steppe-assurance.example, working days 09:00–18:00 Almaty time.
