import os
import tempfile
import unittest
from progress import ProgressTracker


class ProgressTests(unittest.TestCase):
    def make(self, path=':memory:'):
        self.now = 1000
        self.sent = []
        return ProgressTracker(path, lambda text, key: self.sent.append((text, key)), clock=lambda: self.now, autostart=False)

    def test_three_of_five_debounces_after_last_success_and_deduplicates(self):
        tracker = self.make()
        tracker.record('batch', 5, 'a')
        self.now += 20
        tracker.record('batch', 5, 'b')
        self.now += 20
        tracker.record('batch', 5, 'c')
        self.now += 50
        tracker.record('batch', 5, 'c')  # Doesn't extend the deadline.
        self.assertFalse(self.sent)
        self.now += 10
        tracker.flush()
        self.assertEqual(self.sent[0][0], '3 個已成功，2 個未回覆')
        tracker.flush()
        self.assertEqual(len(self.sent), 1)

    def test_all_complete_immediately_and_only_once(self):
        tracker = self.make()
        for thread in ['a', 'b', 'c', 'd', 'e']:
            tracker.record('batch', 5, thread)
        self.assertEqual([x[0] for x in self.sent], ['全部已完成'])
        tracker.record('batch', 5, 'e')
        self.assertEqual(len(self.sent), 1)

    def test_progress_survives_new_tracker_with_same_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'progress.sqlite3')
            tracker = self.make(path)
            tracker.record('batch', 2, 'a')
            tracker.connection.close()
            tracker = self.make(path)
            tracker.record('batch', 2, 'b')
            self.assertEqual(self.sent[0][0], '全部已完成')

    def test_failed_notification_retries_with_same_key(self):
        tracker = self.make()
        calls = []
        def send(text, key):
            calls.append(key)
            if len(calls) == 1:
                raise RuntimeError('upstream failed')
        tracker.send = send
        tracker.record('batch', 1, 'a')
        tracker.flush()
        tracker.flush()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
