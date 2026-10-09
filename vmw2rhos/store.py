"""Versioned SQLite persistence with optimistic concurrency."""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from .planner import empty_inventory, validate_inventory


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY, created TEXT NOT NULL, inventory TEXT NOT NULL)')
            if db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0] == 0:
                db.execute('INSERT INTO revisions VALUES (1, ?, ?)', (self.now(), json.dumps(empty_inventory())))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def load(self, revision=None):
        with self.connect() as db:
            if revision is None:
                row = db.execute('SELECT id, created, inventory FROM revisions ORDER BY id DESC LIMIT 1').fetchone()
            else:
                row = db.execute('SELECT id, created, inventory FROM revisions WHERE id=?', (revision,)).fetchone()
        if row is None:
            raise ValueError('指定した版は存在しません。')
        return dict(revision=row[0], created=row[1], inventory=json.loads(row[2]))

    def save(self, inventory, expected_revision):
        inventory = validate_inventory(inventory)
        if type(expected_revision) is not int:
            raise ValueError('保存元の版番号が必要です。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT MAX(id) FROM revisions').fetchone()[0]
            if current != expected_revision:
                raise ConflictError('別の画面で更新されています。JSONを書き出してから再読込みしてください。')
            revision = current + 1
            db.execute('INSERT INTO revisions VALUES (?, ?, ?)',
                       (revision, self.now(), json.dumps(inventory, ensure_ascii=False)))
        return self.load(revision)

