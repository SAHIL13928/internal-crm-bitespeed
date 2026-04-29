-- Postgres schema for first-time deploys.
--
-- For Render's managed Postgres, the app's `Base.metadata.create_all()`
-- in crm_app/main.py creates these tables automatically on first boot.
-- This file is the human-reviewable equivalent — apply manually with:
--
--     psql "$DATABASE_URL" < migrations/postgres_init.sql
--
-- Differences from migrations/0001_identity_and_raw_messages.sql:
--   - SERIAL instead of INTEGER PRIMARY KEY AUTOINCREMENT
--   - TIMESTAMP instead of DATETIME
--   - BOOLEAN as native (not 0/1 INTEGER)
--   - VARCHAR sizes are unlimited by default (no need to specify)
--
-- The table list below is intentionally limited to what the SQLite
-- migration covered. The rest (shops, contacts, calls, meetings,
-- whatsapp_groups, whatsapp_messages, whatsapp_group_events) are
-- created via SQLAlchemy. Re-create those by booting the app once
-- against an empty Postgres database.

CREATE TABLE IF NOT EXISTS whatsapp_raw_messages (
    id                  SERIAL PRIMARY KEY,
    group_name          VARCHAR  NOT NULL,
    sender_phone        VARCHAR  NOT NULL,
    sender_name         VARCHAR,
    timestamp           TIMESTAMP NOT NULL,
    body                TEXT     NOT NULL DEFAULT '',
    is_from_me          BOOLEAN  NOT NULL DEFAULT FALSE,
    message_type        VARCHAR  NOT NULL,
    media_url           TEXT,
    source_message_id   VARCHAR,
    is_edited           BOOLEAN  NOT NULL DEFAULT FALSE,
    edited_at           TIMESTAMP,
    is_deleted          BOOLEAN  NOT NULL DEFAULT FALSE,
    deleted_at          TIMESTAMP,
    received_at         TIMESTAMP NOT NULL,
    processed_at        TIMESTAMP,
    resolution_status   VARCHAR  NOT NULL DEFAULT 'pending',
    resolved_shop_url   VARCHAR  REFERENCES shops(shop_url),
    resolution_method   VARCHAR,
    CONSTRAINT uq_whatsapp_raw_messages_natural_key
        UNIQUE (group_name, sender_phone, timestamp, body),
    CONSTRAINT ck_whatsapp_raw_messages_resolution_status
        CHECK (resolution_status IN ('pending','resolved','unresolvable','conflict'))
);
CREATE INDEX IF NOT EXISTS ix_whatsapp_raw_messages_group_name        ON whatsapp_raw_messages(group_name);
CREATE INDEX IF NOT EXISTS ix_whatsapp_raw_messages_sender_phone      ON whatsapp_raw_messages(sender_phone);
CREATE INDEX IF NOT EXISTS ix_whatsapp_raw_messages_timestamp         ON whatsapp_raw_messages(timestamp);
CREATE INDEX IF NOT EXISTS ix_whatsapp_raw_messages_resolution_status ON whatsapp_raw_messages(resolution_status);
CREATE INDEX IF NOT EXISTS ix_whatsapp_raw_messages_resolved_shop_url ON whatsapp_raw_messages(resolved_shop_url);
CREATE INDEX IF NOT EXISTS ix_whatsapp_raw_messages_source_message_id ON whatsapp_raw_messages(source_message_id);

CREATE TABLE IF NOT EXISTS identities (
    id    SERIAL PRIMARY KEY,
    kind  VARCHAR NOT NULL,
    value VARCHAR NOT NULL,
    CONSTRAINT uq_identities_kind_value UNIQUE (kind, value),
    CONSTRAINT ck_identities_kind
        CHECK (kind IN ('shop_url','phone','email','meeting_link','group_name'))
);
CREATE INDEX IF NOT EXISTS ix_identities_kind_value ON identities(kind, value);

CREATE TABLE IF NOT EXISTS bindings (
    id              SERIAL PRIMARY KEY,
    identity_a_id   INTEGER NOT NULL REFERENCES identities(id),
    identity_b_id   INTEGER NOT NULL REFERENCES identities(id),
    source          VARCHAR NOT NULL,
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    observed_at     TIMESTAMP NOT NULL,
    evidence_table  VARCHAR,
    evidence_id     VARCHAR,
    CONSTRAINT uq_bindings_natural_key
        UNIQUE (identity_a_id, identity_b_id, source, evidence_id),
    CONSTRAINT ck_bindings_undirected_order
        CHECK (identity_a_id < identity_b_id)
);
CREATE INDEX IF NOT EXISTS ix_bindings_identity_a_id ON bindings(identity_a_id);
CREATE INDEX IF NOT EXISTS ix_bindings_identity_b_id ON bindings(identity_b_id);
