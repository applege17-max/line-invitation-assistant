"""Batch classification progress with deduplicated, retriable LINE summaries."""
import json
import sqlite3
import threading
import time
import uuid


class ProgressTracker:
    def __init__(self, path, send, clock=time.time, autostart=True):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, total INTEGER, completed TEXT, deadline REAL, notified INTEGER DEFAULT 0)')
        self.connection.commit()
        self.lock = threading.RLock()
        self.send, self.clock, self.autostart = send, clock, autostart
        self.started = False

    def start(self):
        if self.autostart and not self.started:
            self.started = True
            threading.Thread(target=self.run, daemon=True, name='classification-summary').start()

    def record(self, batch, total, thread):
        with self.lock:
            row = self.connection.execute('SELECT total, completed FROM batches WHERE id=?', (batch,)).fetchone()
            if row and row[0] != total:
                raise ValueError('Batch size mismatch')
            completed = set(json.loads(row[1])) if row else set()
            if thread not in completed:
                completed.add(thread)
                if len(completed) > total:
                    raise ValueError('Batch overflow')
                deadline = self.clock() if len(completed) == total else self.clock() + 60
                self.connection.execute('INSERT INTO batches(id,total,completed,deadline) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET completed=excluded.completed,deadline=excluded.deadline', (batch, total, json.dumps(sorted(completed)), deadline))
                self.connection.commit()
            self.start()
        self.flush()

    def flush(self):
        with self.lock:
            rows = self.connection.execute('SELECT id,total,completed,notified FROM batches WHERE deadline<=?', (self.clock(),)).fetchall()
            for batch, total, completed, notified in rows:
                count = len(json.loads(completed))
                if count <= notified:
                    continue
                text = '全部已完成' if count == total else f'{count} 個已成功，{total-count} 個未回覆'
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, f'line-batch:{batch}:{count}'))
                try:
                    self.send(text, key)
                except RuntimeError:
                    continue  # Keep the same retry key and try again on the next tick.
                self.connection.execute('UPDATE batches SET notified=? WHERE id=?', (count, batch))
                self.connection.commit()

    def run(self):
        while True:
            time.sleep(5)
            self.flush()
