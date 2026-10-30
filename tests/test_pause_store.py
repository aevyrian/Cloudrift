import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.store import Store


class PauseStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_pause_resume_preserves_task_and_prevents_duplicate(self):
        task = self.store.create_task('7431234567890123456', 'detail')
        self.store.update_task(task['id'], completed=3, total=8, preview=[{'id': 'x'}])
        paused = self.store.pause_task(task['id'])
        self.assertEqual((paused['status'], paused['completed'], paused['total'], paused['preview']), ('paused', 3, 8, [{'id': 'x'}]))
        self.assertIsNone(self.store.claim_task())
        self.assertEqual(self.store.create_task(task['link'], 'detail')['id'], task['id'])
        self.store.recover()
        self.assertEqual(Store(self.store.root).task(task['id'])['status'], 'paused')
        self.assertEqual(self.store.resume_task(task['id'])['status'], 'queued')
        self.assertEqual(self.store.claim_task()['id'], task['id'])

    def test_global_pause_and_resume_honor_existing_cooldown(self):
        task = self.store.create_task('7431234567890123456', 'detail')
        self.store.set_setting('queue_resume_at', 200)
        self.store.set_setting('queue_paused', True)
        with patch('app.store.time.time', return_value=300):
            self.assertIsNone(self.store.claim_task())
        self.assertTrue(Store(self.store.root).setting('queue_paused'))
        self.store.set_setting('queue_paused', False)
        with patch('app.store.time.time', return_value=199):
            self.assertIsNone(self.store.claim_task())
        with patch('app.store.time.time', return_value=200):
            self.assertEqual(self.store.claim_task()['id'], task['id'])
        self.assertEqual(self.store.setting('queue_resume_at'), 200)

    def test_finished_tasks_cannot_be_paused_or_resumed(self):
        task = self.store.create_task('7431234567890123456', 'detail')
        self.store.update_task(task['id'], status='completed')
        with self.assertRaises(ValueError):
            self.store.pause_task(task['id'])
        with self.assertRaises(ValueError):
            self.store.resume_task(task['id'])

    def test_pause_and_resume_all_atomically_preserve_cooldown_and_nonactive_states(self):
        first = self.store.create_task('7431234567890123456', 'detail')
        second = self.store.create_task('7431234567890123457', 'detail')
        waiting = self.store.create_task('7431234567890123458', 'detail')
        self.store.update_task(waiting['id'], status='awaiting_confirmation')
        self.store.update_task(first['id'], status='downloading')
        self.store.set_setting('queue_resume_at', 200)
        self.assertEqual(set(self.store.pause_queue()), {first['id'], second['id']})
        reloaded = Store(self.store.root)
        self.assertTrue(reloaded.setting('queue_paused'))
        self.assertEqual(reloaded.task(first['id'])['status'], 'paused')
        self.assertEqual(reloaded.task(second['id'])['status'], 'paused')
        self.assertEqual(reloaded.task(waiting['id'])['status'], 'awaiting_confirmation')
        self.assertEqual(set(reloaded.resume_queue()), {first['id'], second['id']})
        self.assertFalse(reloaded.setting('queue_paused'))
        self.assertEqual(reloaded.setting('queue_resume_at'), 200)
        with patch('app.store.time.time', return_value=199):
            self.assertIsNone(reloaded.claim_task())

    def test_repeated_pause_marks_explicit_user_intent(self):
        task = self.store.create_task('7431234567890123456', 'detail')
        self.store.update_task(task['id'], status='paused', message='临时等待同步')
        self.assertEqual(self.store.pause_task(task['id'])['message'], '已暂停，已保存内容保留')

    def test_task_created_during_global_pause_is_paused_and_single_resume_releases_queue(self):
        first = self.store.create_task('7431234567890123456', 'detail')
        self.store.pause_queue()
        second = self.store.create_task('7431234567890123457', 'detail')
        self.assertEqual(second['status'], 'paused')
        self.store.set_setting('queue_resume_at', 200)
        self.assertEqual(self.store.resume_task(second['id'])['status'], 'queued')
        self.assertFalse(self.store.setting('queue_paused'))
        self.assertEqual(self.store.task(first['id'])['status'], 'paused')
        self.assertEqual(self.store.setting('queue_resume_at'), 200)
        with patch('app.store.time.time', return_value=199):
            self.assertIsNone(self.store.claim_task())
        with patch('app.store.time.time', return_value=200):
            self.assertEqual(self.store.claim_task()['id'], second['id'])

    def test_retry_during_global_pause_remains_paused(self):
        failed = self.store.create_task('7431234567890123456', 'detail')
        self.store.update_task(failed['id'], status='failed')
        self.store.pause_queue()
        self.assertEqual(self.store.requeue_task(failed['id'])['status'], 'paused')
        chosen = self.store.create_task('7431234567890123457', 'detail')
        self.store.resume_task(chosen['id'])
        self.assertEqual(self.store.task(failed['id'])['status'], 'paused')
        self.assertEqual(self.store.claim_task()['id'], chosen['id'])


if __name__ == '__main__':
    unittest.main()
