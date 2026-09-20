"""Durable control intent and STOP state shared by controller and watchdogs."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4


class ControlStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=2, isolation_level=None, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL)')
        if os.name != 'nt':os.chmod(self.path, 0o600)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute('INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (key, json.dumps(value, allow_nan=False)))

    def save(self, key, value):
        with self.transaction():self.put(key, value)

    def event(self, actor, payload):
        with self.transaction():
            cur = self.db.execute('INSERT INTO events(at,actor,payload) VALUES(?,?,?)',
                                  (time.time(), actor, json.dumps(payload, allow_nan=False)))
            return cur.lastrowid

    def events(self, after=0):
        with self.lock:
            return [{**json.loads(p),'id':i,'recorded_at':at,'actor':a} for i,at,a,p in
                    self.db.execute('SELECT id,at,actor,payload FROM events WHERE id>? ORDER BY id', (after,)).fetchall()]

    @property
    def latch(self):return self.get('latch')

    @property
    def state(self):return self.get('controller_state', {})

    def journal(self, row):self.event('controller', row)

    def persist(self, row):self.save('controller_state', row)

    def set_latch(self, row):
        with self.transaction():
            current = self.get('latch')
            if current is None:
                self.put('latch', {**row, 'id':uuid4().hex, 'latched_at':time.time()})
            self.put('active', False)

    def acknowledge(self, expected_id):
        with self.transaction():
            current = self.get('latch')
            if current is None or current['id'] != expected_id:
                raise ValueError('stop_changed_during_review')
            self.put('latch', None)
            self.put('active', False)
            self.put('last_acknowledged', {'id':expected_id,'at':time.time()})

    def arm(self):
        with self.transaction():
            if self.get('latch') is not None or self.get('owner_stop', False):
                raise ValueError('stop_changed_during_activation')
            self.put('active', True)

    def close(self):self.db.close()


class DeviceLease:
    """OS-backed single-role ownership. Process death releases the lock."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self.fd).st_size == 0:os.write(self.fd,b'0')
                os.lseek(self.fd,0,os.SEEK_SET)
                msvcrt.locking(self.fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise RuntimeError('equipment_role_already_owned') from None

    def close(self):
        if self.fd is None:return
        os.close(self.fd)
        self.fd = None
