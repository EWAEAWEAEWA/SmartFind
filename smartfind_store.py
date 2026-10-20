"""Small SQLite-backed report store for the Streamlit prototype."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Lock

DB_PATH = Path(os.environ.get("SMARTFIND_DB_PATH", Path(__file__).parent / "data" / "smartfind.db"))
_SUPABASE_DATABASE_URL = ""
_INITIALIZED_TARGETS = set()
_INITIALIZATION_LOCK = Lock()


def configure_supabase(database_url):
    global _SUPABASE_DATABASE_URL
    _SUPABASE_DATABASE_URL = str(database_url or os.environ.get("SUPABASE_DB_URL", "")).strip()


def using_supabase():
    return bool(_SUPABASE_DATABASE_URL)


def _storage_target():
    """A stable key for the backend currently selected by the app."""
    return ("postgres", _SUPABASE_DATABASE_URL) if using_supabase() else ("sqlite", str(DB_PATH.resolve()))


def _postgres_connection():
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Supabase storage requires psycopg. Install requirements.txt.") from exc
    return psycopg.connect(_SUPABASE_DATABASE_URL, sslmode="require", connect_timeout=10)


def initialize_storage():
    """Apply this backend's schema once per process, outside normal query paths.

    The statements are deliberately idempotent so first starts in separate app
    processes remain safe.  Normal reads and writes never execute schema DDL.
    """
    target = _storage_target()
    if target in _INITIALIZED_TARGETS:
        return
    with _INITIALIZATION_LOCK:
        if target in _INITIALIZED_TARGETS:
            return
        if using_supabase():
            connection = _postgres_connection()
            try:
                connection.execute("""CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )""")
                connection.execute("""CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )""")
                connection.execute("""CREATE TABLE IF NOT EXISTS notifications (
                    id BIGSERIAL PRIMARY KEY,
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
                connection.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS email_status TEXT NOT NULL DEFAULT 'pending'")
                connection.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS email_attempted_at TEXT")
                connection.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS email_sent_at TEXT")
                connection.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS email_error TEXT")
                # Keep the existing table-level access posture; direct database
                # connections continue to use the configured database role.
                connection.execute("ALTER TABLE reports ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE app_meta ENABLE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        else:
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
                    connection.execute("ALTER TABLE notifications ADD COLUMN email_status TEXT NOT NULL DEFAULT 'legacy'")
                    connection.execute("UPDATE notifications SET email_status = 'legacy'")
                    notification_columns.add("email_status")
                for column, definition in (("email_attempted_at", "TEXT"),
                                           ("email_sent_at", "TEXT"),
                                           ("email_error", "TEXT")):
                    if column not in notification_columns:
                        connection.execute(f"ALTER TABLE notifications ADD COLUMN {column} {definition}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        _INITIALIZED_TARGETS.add(target)


@contextmanager
def _connect():
    """Open one short-lived transactional connection for one storage operation."""
    if using_supabase():
        initialize_storage()
        connection = _postgres_connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return

    initialize_storage()
    connection = sqlite3.connect(DB_PATH, timeout=15)
    try:
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
        order = "created_at, id" if using_supabase() else "rowid"
        select = "payload::text" if using_supabase() else "payload"
        rows = connection.execute(f"SELECT {select} FROM reports ORDER BY {order}").fetchall()
    reports = [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in rows]
    for report in reports:
        report.setdefault("brand", "")
    return reports


def save_report(report):
    """Insert or update one report while retaining its original insertion order."""
    payload = dict(report)
    payload.setdefault("brand", "")
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        json_value = f"{placeholder}::jsonb" if using_supabase() else placeholder
        connection.execute(f"""INSERT INTO reports (id, payload) VALUES ({placeholder}, {json_value})
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload""",
                           (payload["id"], json.dumps(payload, ensure_ascii=False)))


def delete_report(report_id):
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        connection.execute(f"DELETE FROM reports WHERE id = {placeholder}", (report_id,))


def notification_keys():
    with _connect() as connection:
        rows = connection.execute("SELECT key FROM notifications").fetchall()
    return {row[0] for row in rows}


def save_notifications(notifications):
    """Insert new inbox rows once; return how many were created."""
    if not notifications:
        return 0
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        created = 0
        for entry in notifications:
            cursor = connection.execute(f"""INSERT INTO notifications
                (key, pair_key, recipient, item, score, created_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                ON CONFLICT (key) DO NOTHING""",
                               (entry["key"], entry["pair_key"], entry["recipient"], entry["item"],
                                entry["score"], entry["time"]))
            created += cursor.rowcount
        return created


def load_notifications(recipient, unread_only=False):
    placeholder = "%s" if using_supabase() else "?"
    query = """SELECT id, key, pair_key, recipient, item, score, created_at, read_at
        , email_status, email_attempted_at, email_sent_at, email_error
        FROM notifications WHERE recipient = """ + placeholder
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
        placeholder = "%s" if using_supabase() else "?"
        cursor = connection.execute("""UPDATE notifications SET email_status = 'sending',
            email_attempted_at = PLACEHOLDER1, email_error = NULL
            WHERE id = PLACEHOLDER2 AND email_status = 'pending'""".replace(
                "PLACEHOLDER1", placeholder).replace("PLACEHOLDER2", placeholder),
                                       (attempted_at, notification_id))
        return cursor.rowcount == 1


def fail_stuck_notification_emails(stale_after_seconds=120):
    """Turn abandoned SMTP claims into failed entries after the send timeout window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat(timespec="seconds")
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        cursor = connection.execute("""UPDATE notifications SET email_status = 'failed',
            email_error = 'SMTP attempt did not finish; check the inbox before retrying.'
            WHERE email_status = 'sending'
            AND (email_attempted_at IS NULL OR email_attempted_at < PLACEHOLDER)""".replace(
                "PLACEHOLDER", placeholder), (cutoff,))
        return cursor.rowcount


def finish_notification_email(notification_id, status, timestamp=None, error=None):
    if status not in {"sent", "failed"}:
        raise ValueError("Email status must be 'sent' or 'failed'.")
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        connection.execute(f"""UPDATE notifications SET email_status = {placeholder},
            email_sent_at = {placeholder}, email_error = {placeholder} WHERE id = {placeholder}""",
                           (status, timestamp if status == "sent" else None,
                            str(error)[:500] if error else None, notification_id))


def retry_notification_email(notification_id, recipient):
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        cursor = connection.execute(f"""UPDATE notifications SET email_status = 'pending',
            email_error = NULL, email_attempted_at = NULL
            WHERE id = {placeholder} AND recipient = {placeholder} AND email_status = 'failed'""",
                                    (notification_id, str(recipient).strip().casefold()))
        return cursor.rowcount == 1


def mark_notification_read(notification_id, recipient, timestamp):
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        connection.execute(f"""UPDATE notifications SET read_at = {placeholder}
            WHERE id = {placeholder} AND recipient = {placeholder} AND read_at IS NULL""",
                           (timestamp, notification_id, str(recipient).strip().casefold()))


def mark_all_notifications_read(recipient, timestamp):
    with _connect() as connection:
        placeholder = "%s" if using_supabase() else "?"
        connection.execute(f"""UPDATE notifications SET read_at = {placeholder}
            WHERE recipient = {placeholder} AND read_at IS NULL""",
                           (timestamp, str(recipient).strip().casefold()))


def seed_reports_if_empty(reports):
    """Seed initial demo data once; a user deleting all reports won't restore the demo set."""
    with _connect() as connection:
        seeded = connection.execute("SELECT 1 FROM app_meta WHERE key = 'demo_seeded'").fetchone()
        if not seeded:
            count = connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            if count == 0:
                placeholder = "%s" if using_supabase() else "?"
                json_value = f"{placeholder}::jsonb" if using_supabase() else placeholder
                for report in reports:
                    connection.execute(
                        f"INSERT INTO reports (id, payload) VALUES ({placeholder}, {json_value})",
                        (report["id"], json.dumps({**report, "brand": report.get("brand", "")},
                                                  ensure_ascii=False)))
            placeholder = "%s" if using_supabase() else "?"
            connection.execute(f"INSERT INTO app_meta (key, value) VALUES ({placeholder}, {placeholder})",
                               ("demo_seeded", "1"))


def migrate_sqlite_to_supabase(sqlite_path=None):
    """Idempotently copy this app instance's SQLite records into Supabase Postgres."""
    if not using_supabase():
        raise RuntimeError("Configure the Supabase database URL before migrating.")
    source_path = Path(sqlite_path) if sqlite_path else DB_PATH
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        reports = source.execute("SELECT payload FROM reports ORDER BY rowid").fetchall()
        app_meta = (source.execute("SELECT key, value FROM app_meta").fetchall()
                    if "app_meta" in tables else [])
        notification_rows = []
        if "notifications" in tables:
            notification_rows = source.execute("SELECT * FROM notifications ORDER BY id").fetchall()
    finally:
        source.close()

    imported_reports = 0
    imported_notifications = 0
    with _connect() as connection:
        for row in reports:
            payload = json.loads(row["payload"])
            payload.setdefault("brand", "")
            cursor = connection.execute("""INSERT INTO reports (id, payload) VALUES (%s, %s::jsonb)
                ON CONFLICT (id) DO NOTHING""",
                                        (payload["id"], json.dumps(payload, ensure_ascii=False)))
            imported_reports += cursor.rowcount

        for row in notification_rows:
            entry = dict(row)
            status = entry.get("email_status", "legacy")
            error = entry.get("email_error")
            if status == "sending":
                status = "failed"
                error = "SMTP attempt was in progress during database migration; check your inbox before retrying."
            cursor = connection.execute("""INSERT INTO notifications
                (key, pair_key, recipient, item, score, created_at, read_at, email_status,
                 email_attempted_at, email_sent_at, email_error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (key) DO NOTHING""",
                                        (entry["key"], entry["pair_key"], entry["recipient"],
                                         entry["item"], entry["score"], entry["created_at"],
                                         entry.get("read_at"), status, entry.get("email_attempted_at"),
                                         entry.get("email_sent_at"), error))
            imported_notifications += cursor.rowcount

        for entry in app_meta:
                connection.execute("""INSERT INTO app_meta (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO NOTHING""", (entry["key"], entry["value"]))

    return {"reports": imported_reports, "notifications": imported_notifications,
            "source": str(source_path)}
