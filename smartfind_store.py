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
