# Session report — Google Calendar integration

Built per-user OAuth + service-account DWD as a single feature where
mode-switching is a config flip, not a rebuild. Per-user OAuth ships
tonight without admin involvement; DWD activates when Workspace super
admin grants the SA's client_id.

## Files added

```
crm_app/google/__init__.py
crm_app/google/crypto.py            Fernet helpers for refresh tokens
crm_app/google/oauth.py             /auth/google/{connect,callback,
                                    connections,disconnect} routes
crm_app/google/client.py            mode-aware credentials factory +
                                    get_calendar_service_for(email, db)

scripts/sync_google_calendars.py    cron-style runner (every 10 min)

migrations/0002_calendar.sql        DDL for calendar_connections +
                                    calendar_events

tests/test_calendar_oauth.py        4 tests
tests/test_calendar_sync.py         5 tests

docs/calendar_setup.md              setup walkthrough incl. verbatim
                                    Workspace admin message
```

## Files modified

```
requirements.txt                    + authlib, itsdangerous, google-auth*,
                                    google-api-python-client, cryptography
crm_app/models.py                   + CalendarConnection, CalendarEvent
crm_app/main.py                     + SessionMiddleware, /api/shops/{x}/
                                    upcoming-meetings, /auth/google/* in
                                    auth-gate exempt list
crm_app/admin.py                    + POST /api/admin/calendar/enable-dwd
render.yaml                         + 6 calendar env vars,
                                    + cs-crm-calendar-sync cron service
.env                                + 6 calendar env vars (CLIENT_ID/SECRET
                                    blank — fill from GCP console)
tests/conftest.py                   + calendar env fixtures, also wipe
                                    scripts.* modules between tests
README.md                           link to docs/calendar_setup.md
```

## Tests

`43 passed in 43s` — full suite. Calendar tests: `9 passed in 11s`.

Coverage:
- OAuth callback persists encrypted refresh token (mocked authlib)
- Disconnect marks revoked + clears token
- Token refresh updates access_token + token_expires_at
- Sync resolves event to shop via attendee email + adds new bindings
- Idempotent on re-run (same google_event_id → no duplicate row)
- Per-connection failure isolation (invalid_grant → status='revoked',
  rest of run unaffected)
- DWD credentials build calls `service_account.Credentials.with_subject`,
  not the user's refresh token
- /api/admin/calendar/enable-dwd flips mode (rejects without SA env,
  accepts list or "all")

## New env vars (set on Render)

```
CALENDAR_TOKEN_ENCRYPTION_KEY=oUnkXDX3NsMceJYLSDdhGa_V9UwKAgwbDCKz4GTzZP8=
GOOGLE_CLIENT_ID=<from GCP console — see docs/calendar_setup.md>
GOOGLE_CLIENT_SECRET=<from GCP console>
GOOGLE_REDIRECT_URI=https://<host>/auth/google/callback
GOOGLE_SERVICE_ACCOUNT_JSON=<paste SA JSON when DWD activates>
GOOGLE_WORKSPACE_DOMAIN=bitespeed.co
```

`CALENDAR_TOKEN_ENCRYPTION_KEY` printed once above. Generate fresh on
production with:
```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Open TODOs

- **Frontend wiring:** `Upcoming` tab in the TypeScript SPA should
  call `/api/shops/{shop_url}/upcoming-meetings` and render results
  inline next to the existing extracted-from-WA upcoming meetings.
  Distinguish source (`mapping_source = 'google_calendar' vs
  'wa_upcoming_invite'`) with a small badge.
- **OAuth consent screen verification:** for Internal-only consent
  screens, no Google verification needed. If we ever flip to External,
  needs the verification dance.
- **Per-user "Connect calendar" button on the dashboard:** currently
  users have to hit `/auth/google/connect` directly. A button in the
  AM avatar dropdown would be nicer.
- **Calendar webhooks:** Google supports push notifications via
  `events.watch` — once the bulk pull is healthy, switch to live
  push for sub-minute freshness.

## What's blocked

Nothing. Per-user OAuth works without admin involvement. DWD waits on
the super admin clicking through the message in `docs/calendar_setup.md`.
