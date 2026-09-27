"""Legacy SQLite ledger, retained for earlier local data and regression tests.

The web API uses pg_store; this module is not a production storage fallback.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


@contextmanager
def connect():
    path = Path(os.environ.get("OI_RECOMMENDATION_DB", "runs/recommendations.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS recommendations (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
      CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, recommendation_id TEXT NOT NULL,
        action TEXT NOT NULL, vessel_id TEXT, entered_by TEXT NOT NULL, reason TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
    """)
    try:
        with db:
            yield db
    finally:
        db.close()


def save(payload):
    identifier = str(uuid4())
    with connect() as db:
        db.execute(
            "INSERT INTO recommendations(id,payload) VALUES (?,?)",
            (identifier, json.dumps(payload)),
        )
    return {"id": identifier, **payload}


def get(identifier):
    with connect() as db:
        row = db.execute(
            "SELECT payload FROM recommendations WHERE id=?", (identifier,)
        ).fetchone()
    return {"id": identifier, **json.loads(row[0])} if row else None


def decide(identifier, body):
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT payload FROM recommendations WHERE id=?", (identifier,)
        ).fetchone()
        if not row:
            raise LookupError("Recommendation not found")
        payload = json.loads(row[0])
        eligible = {v["vessel_id"] for v in payload["candidates"]}
        known = eligible | {
            v["vessel_id"]
            for v in payload["rejected"]
            + payload.get("held", [])
            + payload.get("not_shortlisted", [])
        }
        if body.action == "accept" and body.vessel_id not in eligible:
            raise ValueError("Accept requires a shortlisted vessel")
        if body.action == "override" and body.vessel_id not in known:
            raise ValueError("Override requires a vessel evaluated in this run")
        db.execute(
            "INSERT INTO decisions(recommendation_id,action,vessel_id,entered_by,reason) VALUES (?,?,?,?,?)",
            (identifier, body.action, body.vessel_id, body.entered_by, body.reason),
        )
    return history(identifier)


def history(identifier):
    with connect() as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM decisions WHERE recommendation_id=? ORDER BY id DESC",
                (identifier,),
            )
        ]
