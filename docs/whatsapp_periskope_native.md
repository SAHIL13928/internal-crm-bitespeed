# Periskope native webhook — for Arindam

Alternative to the intern-bridge path (`docs/whatsapp_ingestion_for_intern.md`).
This endpoint accepts Periskope's **raw payload** directly. No
adapter / no field translation on your side — just point Periskope's
webhook console at this URL and you're done.

## What you do in Periskope

1. https://console.periskope.app/settings/integrations/webhooks → **Add Webhook**
2. **URL:**
   ```
   https://internal-crm-bitespeed.onrender.com/webhooks/periskope
   ```
3. **Events to subscribe to** (uncheck the rest):
   - ✅ `message.created` — the actual messages
   - ✅ `message.updated` — edits (without this, our DB shows stale text forever)
   - ✅ `message.deleted` — soft-delete in our DB; preserves body for audit
   - ✅ `chat.created` — group name + members map
   - ✅ `chat.notification.created` — member adds/removes, group renames
   - ❌ reactions, tickets, flags, phone status, notes — ignored server-side
4. **Generate a signing key.** Copy it — you'll send this to me via DM
   so I can put it in our Render env var `PERISKOPE_SIGNING_SECRET`.
5. Save.

That's it from your side. Periskope will start delivering events; we
verify the HMAC and persist them.

## What we do server-side

- Verify HMAC-SHA256 of the raw body against `x-periskope-signature`
  header. Wrong signature → `401`.
- For `message.created`:
  - Strip the `@c.us` / `@g.us` suffix from `sender_phone` and `chat_id`
  - For group chats (`@g.us`), look up the group name we learned from
    `chat.created`. For 1:1 chats, store the contact JID as the
    synthetic `group_name` so dedupe still works.
  - Collapse `message_type` to either `text` (when Periskope says
    `chat`) or `document` (anything else — image, video, audio, ptt,
    document)
  - Insert into `whatsapp_raw_messages`. Idempotent on the natural key
    `(group_name, sender_phone, timestamp, body)`, so retries are safe.
  - Inline-resolve to a merchant via static directory + identity graph.
- For `chat.created`:
  - Upsert a `whatsapp_groups` row keyed by the chat JID. Future
    `message.created` events with the same `chat_id` will pick up
    `chat_name` from this cache.
- Anything else we don't recognize: `200 OK`, ignored. Periskope's
  retry queue stays clean.

## Status codes

| Status | Meaning                         | Periskope retry? |
|--------|---------------------------------|------------------|
| `200`  | Accepted (or recognized + ignored) | No |
| `400`  | Body wasn't valid JSON          | No |
| `401`  | Bad / missing HMAC signature    | No (fix secret) |
| `503`  | We forgot to set the signing secret server-side | Yes |
| `500`  | Real DB error                   | Yes |

## Caveats / known limitations

- **`sender_name` is `NULL`** for now. Periskope's `message.created`
  payload doesn't include the contact's display name — only the JID.
  The Chat object has it (under `members.{jid}.contact_name`) but
  pulling it per-message would mean an extra API call. We can backfill
  later by walking the Chat object on `chat.created` and storing the
  members.
- **`media_url` is the relative path** Periskope sent
  (`/storage/v1/object/public/...`). To actually fetch the file you'd
  prepend Periskope's storage base URL — that's a downstream concern.
- **First message in a new group has the JID as group_name** until
  `chat.created` arrives. The natural-key dedupe still works; once
  `chat.created` lands, `whatsapp_groups.group_name` updates and the
  next message correctly carries the human-readable name. Existing
  rows with JID-as-name can be backfilled by the reprocess script.

## Testing it yourself

If you want to fire a test event without going through Periskope:

```bash
SECRET="<the signing secret you'll DM us>"
BODY='{"event":"message.created","data":{"sender_phone":"919999999999@c.us","chat_id":"119999999999@c.us","body":"hi","message_type":"chat","from_me":false,"timestamp":"2026-04-28 10:00:00+00"}}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $2}')

curl -X POST 'https://internal-crm-bitespeed.onrender.com/webhooks/periskope' \
  -H 'Content-Type: application/json' \
  -H "x-periskope-signature: $SIG" \
  -d "$BODY"
# expected: 200 with {"event":"message.created","inserted":1,...}
```

## Picking between this and the intern bridge

You only need to pick **one**. If you're already mid-implementation on
the intern-bridge path (the one in `docs/whatsapp_ingestion_for_intern.md`),
keep going — that path works too. If you're starting fresh, this one
is less code on your side.
