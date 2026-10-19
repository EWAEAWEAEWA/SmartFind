"""Small SQLite-backed report store for the Streamlit prototype."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager

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
            read_at TEXT
        )""")
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
        FROM notifications WHERE recipient = ?"""
    params = [str(recipient).strip().casefold()]
    if unread_only:
        query += " AND read_at IS NULL"
    query += " ORDER BY id DESC"
    with _connect() as connection:
        rows = connection.execute(query, params).fetchall()
    columns = ("id", "key", "pair_key", "recipient", "item", "score", "time", "read_at")
    return [dict(zip(columns, row)) for row in rows]


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
