import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class Repository:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, status TEXT NOT NULL, question TEXT NOT NULL,
                  depth TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  payload TEXT NOT NULL, error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                  created_at TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL,
                  data TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES runs(id)
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def interrupt_unfinished(self):
        with self.connect() as db:
            now = datetime.now(UTC).isoformat()
            db.execute(
                "UPDATE runs SET status='interrupted', updated_at=? WHERE status IN ('queued','running')",
                (now,),
            )

    def create_run(self, run_id: str, request: dict):
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, 'queued', ?, ?, ?, ?, ?, NULL)",
                (
                    run_id,
                    request["question"],
                    request["depth"],
                    now,
                    now,
                    json.dumps({"request": request}),
                ),
            )

    def save_result(self, run_id: str, status: str, payload: dict, error: str | None = None):
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET status=?, updated_at=?, payload=?, error=? WHERE id=?",
                (status, now, json.dumps(payload), error, run_id),
            )

    def add_event(self, run_id: str, kind: str, message: str, data: dict | None = None):
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO events(run_id, created_at, kind, message, data) VALUES (?, ?, ?, ?, ?)",
                (
                    run_id,
                    datetime.now(UTC).isoformat(),
                    kind,
                    message,
                    json.dumps(data or {}),
                ),
            )
            return cursor.lastrowid

    def get_run(self, run_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "payload": json.loads(row["payload"])}

    def list_runs(self, limit: int = 50):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,status,question,depth,created_at,updated_at,error FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def events_after(self, run_id: str, cursor: int = 0, limit: int = 200):
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
                (run_id, cursor, limit),
            ).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]
