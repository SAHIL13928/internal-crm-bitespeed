# Google Calendar setup

> Two modes ship at once. Per-user OAuth works **tonight** without
> any super-admin involvement. Domain-Wide Delegation (DWD) activates
> later when the Workspace super admin grants the service account.
> Switching mode is a config flip — no rebuild.

## Mode A — per-user OAuth (works tonight)

Each AM clicks "Connect calendar" → completes Google's OAuth
consent → we hold their encrypted refresh token + sync their calendar
every 10 min.

**You (no admin needed):**

1. **Create a GCP project** at https://console.cloud.google.com/projectcreate
   (or use an existing one).
2. **Enable Google Calendar API** at
   https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
   → click **Enable**.
3. **Create OAuth 2.0 Client ID:**
   - APIs & Services → Credentials → **Create credentials** → **OAuth client ID**
   - Application type: **Web application**
   - Name: `cs-crm-calendar`
   - Authorized redirect URIs:
     - `http://127.0.0.1:8765/auth/google/callback` (local dev)
     - `http://34.239.140.115/auth/google/callback` (current EC2 host)
     - `https://internal-crm-bitespeed.onrender.com/auth/google/callback` (production)
   - **Save** — copy the **Client ID** and **Client secret** that pop up
4. **Configure the OAuth consent screen** (one-time):
   - APIs & Services → OAuth consent screen
   - User type: **Internal** (only members of bitespeed.co can connect — safest)
   - App name: `Bitespeed CS CRM`
   - User support email: yours
   - Developer contact: yours
   - **Scopes** → click **Add or remove scopes** → tick:
     ```
     https://www.googleapis.com/auth/calendar.readonly
     ```
   - Save
5. **Set the env vars** (locally in `.env` and on Render dashboard):
   ```
   GOOGLE_CLIENT_ID=<the client id from step 3>
   GOOGLE_CLIENT_SECRET=<the client secret from step 3>
   GOOGLE_REDIRECT_URI=https://<your-host>/auth/google/callback
   GOOGLE_WORKSPACE_DOMAIN=bitespeed.co
   CALENDAR_TOKEN_ENCRYPTION_KEY=<see note below>
   ```
6. **Generate the Fernet key once** and put it in env:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
7. **Connect each AM:** open `https://<host>/auth/google/connect` while logged
   into the dashboard. Google's consent screen appears → grant → returns
   to `/app/?google_connected=1`. A row appears in `calendar_connections`
   with `status='active'`, `auth_mode='user_oauth'`.
8. **Sync runs automatically every 10 min** via the `cs-crm-calendar-sync`
   Render cron job (`render.yaml`). Or trigger manually:
   ```bash
   python scripts/sync_google_calendars.py
   ```

That's it. No admin involvement. The dashboard's `Upcoming` tab populates
with whatever calendar events resolved to a known merchant via attendee
emails.

## Mode B — Domain-Wide Delegation (super admin enables later)

This skips per-user consent entirely. Once enabled, the service account
acts on behalf of every `@bitespeed.co` user automatically — even ones
who never clicked "Connect."

### What you do (no admin yet)

1. **Create a service account** in your GCP project:
   - APIs & Services → Credentials → Create credentials → **Service account**
   - Name: `cs-crm-calendar-sa`
   - Skip the "Grant access" step
   - Click into the SA → **Keys** → **Add key** → **JSON**
   - A JSON file downloads. Open it — note the `client_id` field
     (a 21-digit numeric string). **You'll send this to the super admin.**
2. **Enable Domain-Wide Delegation on the SA:**
   - APIs & Services → Credentials → click your SA
   - **Enable G Suite Domain-wide Delegation** → tick
   - Save (this enables the SA's "OAuth 2.0 Client ID" used in admin console)

### What the Workspace super admin does

Send them this **verbatim**:

> Hi, please grant my service account read-only Google Calendar access
> for everyone in bitespeed.co. Two clicks:
>
> 1. https://admin.google.com → **Security** → **Access and data control**
>    → **API controls** → **Manage Domain Wide Delegation** → **Add new**
> 2. Paste these values and save:
>
>    **Client ID:** `<paste the SA's client_id here, the 21-digit number>`
>    **OAuth scopes:** `https://www.googleapis.com/auth/calendar.readonly`
>
> One scope, one client ID. Should take 30 seconds. Thanks!

### Once they've done it

3. **Paste the SA JSON into Render env** (Settings → Environment):
   ```
   GOOGLE_SERVICE_ACCOUNT_JSON=<entire contents of the downloaded JSON, including braces>
   ```
4. **Flip the mode for the AMs you want to switch:**
   ```bash
   curl -X POST https://<host>/api/admin/calendar/enable-dwd \
     -H "X-Admin-Secret: <your ADMIN_SECRET>" \
     -H "Content-Type: application/json" \
     -d '{"user_emails": "all"}'
   ```
   Or pass a specific list:
   ```
   {"user_emails": ["alice@bitespeed.co", "bob@bitespeed.co"]}
   ```
5. **Next sync run** uses the SA — no per-user refresh tokens needed.
   Connections that previously had `auth_mode='user_oauth'` are now in
   `dwd_impersonation`. Refresh tokens stay encrypted in the DB but
   aren't used.

## Resolution mechanics

Once a calendar event lands, we run it through the identity graph
using its **external** attendee emails (we skip `@bitespeed.co`
addresses and the organizer). The first email that resolves to a
shop_url wins; the event's `shop_url` and `resolution_status='resolved'`
are saved.

**Side effect:** every other external attendee on the same event gets
a fresh `(email, shop_url)` binding added to the identity graph with
source='google_calendar'. That means a meeting with three people from
Acme bound to one shop_url retroactively binds the other two for any
future event they attend solo.

Conflicts (multiple attendees resolving to different shops) are
flagged `resolution_status='conflict'` and surface at
`/admin/conflicts`.

## API endpoint

```
GET  /api/shops/{shop_url}/upcoming-meetings?limit=10
```
Returns calendar events for that shop where `end_time > now()`,
ordered by `start_time`. Used by the dashboard's `Upcoming` tab.

## Schema

`migrations/0002_calendar.sql` has the canonical DDL. Two tables:

- **`calendar_connections`** — one row per AM with `auth_mode` +
  encrypted refresh token + status
- **`calendar_events`** — one row per (connection, google_event_id).
  Idempotent upserts.

Both are auto-created on app boot via `Base.metadata.create_all()`;
the SQL file is for human review.

## Tests

`tests/test_calendar_oauth.py` — disconnect, callback (mocked authlib),
token refresh.

`tests/test_calendar_sync.py` — attendee→shop resolution + binding side
effect, idempotent re-run, per-connection failure isolation, DWD
credentials build, mode-flip via /admin/calendar/enable-dwd.

```bash
python -m pytest tests/test_calendar_oauth.py tests/test_calendar_sync.py -v
```

## Cron

`render.yaml` declares a `cs-crm-calendar-sync` cron service running
every 10 minutes:
```
schedule: "*/10 * * * *"
startCommand: python scripts/sync_google_calendars.py
```

Free-tier Render runs cron jobs even when the web service is asleep —
calendar events stay fresh.
