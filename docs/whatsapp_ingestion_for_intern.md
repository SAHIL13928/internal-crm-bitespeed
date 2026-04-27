# WhatsApp message ingestion — bridge contract

Hand this to the WA bridge intern. The endpoint is live in production.

## Endpoint

```
POST https://internal-crm-bitespeed.onrender.com/webhooks/whatsapp/messages
```

Local dev (when testing against your laptop):

```
POST http://127.0.0.1:8765/webhooks/whatsapp/messages
```

## Authentication

Pass the shared secret in the `X-Webhook-Secret` header:

```
X-Webhook-Secret: <value provided out-of-band>
```

The server compares constant-time. Wrong/missing → `401`.

## Payload — single message

All fields below are required unless marked optional. `timestamp` must be
ISO-8601; we accept both `+00:00` and `Z` suffixes.

```json
{
  "group_name":   "Acme Inc <> Bitespeed",
  "sender_phone": "+919999999999",
  "sender_name":  "Aditi Sharma",
  "timestamp":    "2026-04-26T10:00:00Z",
  "body":         "When can we schedule the migration?",
  "is_from_me":   false,
  "message_type": "text",
  "media_url":    null
}
```

- `body` — text content. May be omitted/null for media-only messages
  (we'll coerce null → empty string server-side for dedupe).
- `media_url` — required when `message_type == "document"`.
- `message_type` — `"text"` or `"document"`.

## Payload — batch (preferred for backfill / catch-up)

Send up to **500** messages per request:

```json
{
  "messages": [
    { /* …message 1… */ },
    { /* …message 2… */ }
  ]
}
```

> Batches larger than 500 are rejected with `413 Payload Too Large`. Split
> them client-side.

## Response (always JSON, status `200`)

```json
{
  "received":   10,
  "duplicates": 3,
  "resolved":   6,
  "pending":    1
}
```

- `received` — how many messages we parsed from your payload.
- `duplicates` — already in our DB (idempotent retry, not an error).
- `resolved` — newly-inserted messages we successfully bound to a merchant.
- `pending` — newly-inserted but not yet bound to a merchant; we'll
  retry resolution as more data arrives. **Don't resend** — they're
  stored.

## Idempotency

We dedupe on the natural key `(group_name, sender_phone, timestamp, body)`.
If your bridge retries the same message (after a network blip, after a
restart, anything), it will **not** create a duplicate row — it counts as
a duplicate in the response. **You can retry safely.**

## Status codes & retry policy

| Status | Meaning                                       | Retry? |
|--------|-----------------------------------------------|--------|
| `200`  | Success (duplicates are normal, not errors)   | No     |
| `401`  | Wrong/missing secret                          | No — fix the header |
| `413`  | Batch >500 messages                           | No — split client-side |
| `422`  | Schema violation (missing required field, bad enum, etc.) | No — fix the payload |
| `5xx`  | Server / DB error                             | **Yes** — exponential backoff (start 1s, cap at 60s, max 6 attempts) |

## Sample curl

Single message:

```bash
curl -X POST 'https://internal-crm-bitespeed.onrender.com/webhooks/whatsapp/messages' \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: <SECRET>' \
  -d '{
    "group_name":   "Acme Inc <> Bitespeed",
    "sender_phone": "+919999999999",
    "sender_name":  "Aditi Sharma",
    "timestamp":    "2026-04-26T10:00:00Z",
    "body":         "Hello",
    "is_from_me":   false,
    "message_type": "text"
  }'
```

Batch:

```bash
curl -X POST 'https://internal-crm-bitespeed.onrender.com/webhooks/whatsapp/messages' \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: <SECRET>' \
  -d '{
    "messages": [
      {"group_name":"Acme Inc <> Bitespeed","sender_phone":"+919999999999","sender_name":"Aditi","timestamp":"2026-04-26T10:00:00Z","body":"first","is_from_me":false,"message_type":"text"},
      {"group_name":"Acme Inc <> Bitespeed","sender_phone":"+919999999999","sender_name":"Aditi","timestamp":"2026-04-26T10:01:00Z","body":"second","is_from_me":false,"message_type":"text"}
    ]
  }'
```

## What you don't need to send

You may have noticed our older endpoint accepted optional fields like
`message_id`, `group_id` (JID), `reply_to_message_id`, `media_mime_type`,
`media_caption`, `is_edited`, `is_deleted`, `raw`. **You don't need any of
those.** The new ingestion path uses the natural-key dedupe described
above, and we resolve groups by `group_name` alone.

If you have a stable `message_id` you'd like to send for traceability,
that's fine — we ignore unknown fields rather than rejecting them — but
it has no effect on dedupe.

## Questions?

Ping the CRM owner. The server-side spec lives at
`crm_app/webhooks/whatsapp.py`; tests at `tests/test_whatsapp_ingestion.py`.
