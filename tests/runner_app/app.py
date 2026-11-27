"""Minimal reservation app used only to verify the compose-postgres-k6 runner.

APP_MODE=correct locks the event row before counting; APP_MODE=oversell reads
without a lock and waits before inserting, so concurrent requests overbook.
APP_DB_ENV names the variable holding the database URL (default DATABASE_URL).
"""

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

DB_URL = os.environ[os.environ.get("APP_DB_ENV", "DATABASE_URL")]
MODE = os.environ.get("APP_MODE", "correct")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, capacity INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reservations (
    id TEXT PRIMARY KEY, event_id TEXT NOT NULL, user_id TEXT NOT NULL, status TEXT NOT NULL
);
"""


def remaining(conn, event_id):
    row = conn.execute("SELECT capacity FROM events WHERE id = %s", (event_id,)).fetchone()
    if row is None:
        return None, None
    used = conn.execute(
        "SELECT count(*) FROM reservations WHERE event_id = %s AND status = 'CONFIRMED'", (event_id,)
    ).fetchone()[0]
    return row[0], row[0] - used


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path == "/health":
            try:
                with psycopg.connect(DB_URL) as conn:
                    conn.execute("SELECT 1")
            except psycopg.Error:
                return self._send(503, {"status": "database unavailable"})
            return self._send(200, {"status": "ok"})
        if self.path.startswith("/events/"):
            event_id = self.path.split("/", 2)[2]
            with psycopg.connect(DB_URL) as conn:
                capacity, left = remaining(conn, event_id)
            if capacity is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"event": {"id": event_id, "capacity": capacity, "remaining": left}})
        if self.path.startswith("/bookings/"):
            booking_id = self.path.split("/", 2)[2]
            with psycopg.connect(DB_URL) as conn:
                row = conn.execute("SELECT id, status FROM reservations WHERE id = %s", (booking_id,)).fetchone()
            if row is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"booking": {"id": row[0], "state": row[1]}})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/events":
            capacity = int(self._json().get("capacity", 0))
            event_id = uuid.uuid4().hex
            with psycopg.connect(DB_URL) as conn:
                conn.execute("INSERT INTO events (id, capacity) VALUES (%s, %s)", (event_id, capacity))
            return self._send(201, {"event": {"id": event_id, "capacity": capacity, "remaining": capacity}})
        if self.path != "/bookings":
            return self._send(404, {"error": "not found"})
        payload = self._json()
        event_id, user_id = payload.get("event"), payload.get("user")
        booking_id = str(uuid.uuid4())
        with psycopg.connect(DB_URL) as conn:
            with conn.transaction():
                lock = " FOR UPDATE" if MODE == "correct" else ""
                event = conn.execute(f"SELECT capacity FROM events WHERE id = %s{lock}", (event_id,)).fetchone()
                if event is None:
                    return self._send(404, {"error": "event not found"})
                used = conn.execute(
                    "SELECT count(*) FROM reservations WHERE event_id = %s AND status = 'CONFIRMED'", (event_id,)
                ).fetchone()[0]
                if MODE == "oversell":
                    time.sleep(0.2)
                if used >= event[0]:
                    return self._send(409, {"error": "full"})
                conn.execute(
                    "INSERT INTO reservations (id, event_id, user_id, status) VALUES (%s, %s, %s, 'CONFIRMED')",
                    (booking_id, event_id, user_id),
                )
        return self._send(201, {"booking": {"id": booking_id, "state": "CONFIRMED"}})


with psycopg.connect(DB_URL) as _conn:
    _conn.execute(SCHEMA)

ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
