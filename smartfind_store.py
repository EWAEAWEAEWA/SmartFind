"""Small SQLite-backed report store for the Streamlit prototype."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = Path(os.environ.get("SMARTFIND_DB_PATH", Path(__file__).parent / "data" / "smartfind.db"))


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            pair_key TEXT NOT NULL,
            recipient TEXT NOT NULL,
            item TEXT NOT NULL,
            score INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            read_at TEXT,
            email_status TEXT NOT NULL DEFAULT 'pending',
            email_attempted_at TEXT,
            email_sent_at TEXT,
            email_error TEXT
        )""")
        notification_columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(notifications)").fetchall()}
        if "email_status" not in notification_columns:
            connection.execute("""ALTER TABLE notifications ADD COLUMN email_status
                TEXT NOT NULL DEFAULT 'legacy'""")
            connection.execute("UPDATE notifications SET email_status = 'legacy'")
            notification_columns.add("email_status")
        for column, definition in (
            ("email_attempted_at", "TEXT"),
                ("email_sent_at", "TEXT"),
                ("email_error", "TEXT")):
            if column not in notification_columns:
                connection.execute(f"ALTER TABLE notifications ADD COLUMN {column} {definition}")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def load_reports():
    """Read reports in insertion order and ensure older records have a brand field."""
    with _connect() as connection:
        rows = connection.execute("SELECT payload FROM reports ORDER BY rowid").fetchall()
    reports = [json.loads(row[0]) for row in rows]
    for report in reports:
        report.setdefault("brand", "")
    return reports


def save_report(report):
    """Insert or update one report while retaining its original insertion order."""
    payload = dict(report)
    payload.setdefault("brand", "")
    with _connect() as connection:
        connection.execute("""INSERT INTO reports (id, payload) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload""",
                           (payload["id"], json.dumps(payload, ensure_ascii=False)))


def delete_report(report_id):
    with _connect() as connection:
        connection.execute("DELETE FROM reports WHERE id = ?", (report_id,))


def notification_keys():
    with _connect() as connection:
        rows = connection.execute("SELECT key FROM notifications").fetchall()
    return {row[0] for row in rows}


def save_notifications(notifications):
    """Insert new inbox rows once; return how many were created."""
    if not notifications:
        return 0
    with _connect() as connection:
        before = connection.total_changes
        connection.executemany("""INSERT OR IGNORE INTO notifications
            (key, pair_key, recipient, item, score, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""", [
                (entry["key"], entry["pair_key"], entry["recipient"], entry["item"],
                 entry["score"], entry["time"])
                for entry in notifications
            ])
        return connection.total_changes - before


def load_notifications(recipient, unread_only=False):
    query = """SELECT id, key, pair_key, recipient, item, score, created_at, read_at
        , email_status, email_attempted_at, email_sent_at, email_error
        FROM notifications WHERE recipient = ?"""
    params = [str(recipient).strip().casefold()]
    if unread_only:
        query += " AND read_at IS NULL"
    query += " ORDER BY id DESC"
    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()
    columns = ("id", "key", "pair_key", "recipient", "item", "score", "time", "read_at",
               "email_status", "email_attempted_at", "email_sent_at", "email_error")
    return [dict(zip(columns, row)) for row in rows]


def pending_notification_emails():
    with _connect() as connection:
        rows = connection.execute("""SELECT id, key, pair_key, recipient, item, score, created_at
            FROM notifications WHERE email_status = 'pending' ORDER BY id""").fetchall()
    columns = ("id", "key", "pair_key", "recipient", "item", "score", "time")
    return [dict(zip(columns, row)) for row in rows]


def claim_notification_email(notification_id):
    """Atomically claim a pending message so concurrent sessions cannot send twice."""
    attempted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as connection:
        cursor = connection.execute("""UPDATE notifications SET email_status = 'sending',
            email_attempted_at = ?, email_error = NULL
            WHERE id = ? AND email_status = 'pending'""", (attempted_at, notification_id))
        return cursor.rowcount == 1


def fail_stuck_notification_emails(stale_after_seconds=120):
    """Turn abandoned SMTP claims into failed entries after the send timeout window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat(timespec="seconds")
    with _connect() as connection:
        cursor = connection.execute("""UPDATE notifications SET email_status = 'failed',
            email_error = 'SMTP attempt did not finish; check the inbox before retrying.'
            WHERE email_status = 'sending'
            AND (email_attempted_at IS NULL OR email_attempted_at < ?)""", (cutoff,))
        return cursor.rowcount


def finish_notification_email(notification_id, status, timestamp=None, error=None):
    if status not in {"sent", "failed"}:
        raise ValueError("Email status must be 'sent' or 'failed'.")
    with _connect() as connection:
        connection.execute("""UPDATE notifications SET email_status = ?, email_sent_at = ?, email_error = ?
            WHERE id = ?""", (status, timestamp if status == "sent" else None,
                                str(error)[:500] if error else None, notification_id))


def retry_notification_email(notification_id, recipient):
    with _connect() as connection:
        cursor = connection.execute("""UPDATE notifications SET email_status = 'pending', email_error = NULL
            , email_attempted_at = NULL WHERE id = ? AND recipient = ? AND email_status = 'failed'""",
                                    (notification_id, str(recipient).strip().casefold()))
        return cursor.rowcount == 1


def mark_notification_read(notification_id, recipient, timestamp):
    with _connect() as connection:
        connection.execute("""UPDATE notifications SET read_at = ?
            WHERE id = ? AND recipient = ? AND read_at IS NULL""",
                           (timestamp, notification_id, str(recipient).strip().casefold()))


def mark_all_notifications_read(recipient, timestamp):
    with _connect() as connection:
        connection.execute("""UPDATE notifications SET read_at = ?
            WHERE recipient = ? AND read_at IS NULL""",
                           (timestamp, str(recipient).strip().casefold()))


def seed_reports_if_empty(reports):
    """Seed initial demo data once; a user deleting all reports won't restore the demo set."""
    with _connect() as connection:
        seeded = connection.execute("SELECT 1 FROM app_meta WHERE key = 'demo_seeded'").fetchone()
        if not seeded:
            count = connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            if count == 0:
                connection.executemany("INSERT INTO reports (id, payload) VALUES (?, ?)", [
                    (report["id"], json.dumps({**report, "brand": report.get("brand", "")}, ensure_ascii=False))
                    for report in reports
                ])
            connection.execute("INSERT INTO app_meta (key, value) VALUES ('demo_seeded', '1')")
