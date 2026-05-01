-- Migration 0002: Google Calendar integration.
-- NOT auto-applied. The app's `Base.metadata.create_all()` creates these
-- tables on first boot. This file is the human-reviewable schema.
--
-- Apply manually (Postgres):
--   psql "$DATABASE_URL" < migrations/0002_calendar.sql
-- Apply manually (SQLite local dev):
--   sqlite3 crm.db < migrations/0002_calendar.sql

CREATE TABLE IF NOT EXISTS calendar_connections (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,  -- (Postgres: SERIAL)
    user_email               VARCHAR  NOT NULL UNIQUE,
    auth_mode                VARCHAR  NOT NULL DEFAULT 'user_oauth',
    refresh_token_encrypted  TEXT,
    access_token             TEXT,
    token_expires_at         DATETIME,                            -- TIMESTAMP on Postgres
    last_synced_at           DATETIME,
    status                   VARCHAR  NOT NULL DEFAULT 'active',
    last_error               TEXT,
    created_at               DATETIME NOT NULL,
    updated_at               DATETIME NOT NULL,
    CONSTRAINT ck_calendar_connections_auth_mode
        CHECK (auth_mode IN ('user_oauth','dwd_impersonation')),
    CONSTRAINT ck_calendar_connections_status
        CHECK (status IN ('active','failing','revoked'))
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    google_event_id   VARCHAR  NOT NULL,
    connection_id     INTEGER  NOT NULL REFERENCES calendar_connections(id),

    summary           TEXT,
    description       TEXT,
    start_time        DATETIME NOT NULL,
    end_time          DATETIME,
    meeting_link      TEXT,
    attendee_emails   TEXT,                                       -- JSONB on Postgres
    organizer_email   VARCHAR,

    shop_url          VARCHAR  REFERENCES shops(shop_url),
    resolution_status VARCHAR  NOT NULL DEFAULT 'pending',
    raw_payload       TEXT,                                       -- JSONB on Postgres

    created_at        DATETIME NOT NULL,
    updated_at        DATETIME NOT NULL,

    CONSTRAINT uq_calendar_events_connection_event
        UNIQUE (connection_id, google_event_id),
    CONSTRAINT ck_calendar_events_resolution_status
        CHECK (resolution_status IN ('pending','resolved','unresolvable','conflict'))
);

CREATE INDEX IF NOT EXISTS ix_calendar_events_google_event_id
    ON calendar_events(google_event_id);
CREATE INDEX IF NOT EXISTS ix_calendar_events_connection_id
    ON calendar_events(connection_id);
CREATE INDEX IF NOT EXISTS ix_calendar_events_start_time
    ON calendar_events(start_time);
CREATE INDEX IF NOT EXISTS ix_calendar_events_shop_url
    ON calendar_events(shop_url);
CREATE INDEX IF NOT EXISTS ix_calendar_events_shop_url_start_time
    ON calendar_events(shop_url, start_time);
