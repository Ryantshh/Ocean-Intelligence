"""Local conversation storage. Never connects to Supabase."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class HistoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, updated TEXT, payload TEXT)')
        self.path.chmod(0o600)

    def save(self, payload, conversation_id=None):
        key = conversation_id or str(uuid4())
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT OR REPLACE INTO conversations VALUES (?, ?, ?)',
                       (key, datetime.now(timezone.utc).isoformat(), json.dumps(payload)))
        return key

    def list(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT id, updated FROM conversations ORDER BY updated DESC').fetchall()

    def load(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT payload FROM conversations WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('Conversation not found')
        return json.loads(row[0])

    def delete(self, key):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM conversations WHERE id=?", (key,))
