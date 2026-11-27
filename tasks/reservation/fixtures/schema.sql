-- Shared schema for the reservation example apps.
-- The runner applies this file before starting the app and resets the data
-- before each probe and check round. The example apps must not create or
-- alter these tables; they only read and write rows. An app may create its
-- own additional tables (for example, idempotency records); the runner does
-- not reset or read them.

CREATE TABLE IF NOT EXISTS events (
    id        TEXT PRIMARY KEY,
    capacity  INTEGER NOT NULL CHECK (capacity >= 0)
);

CREATE TABLE IF NOT EXISTS reservations (
    id          TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL REFERENCES events (id),
    user_id     TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('CONFIRMED')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS reservations_event_id_idx ON reservations (event_id);
