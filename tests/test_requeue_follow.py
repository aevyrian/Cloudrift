import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from app.store import Store


class RequeueFollowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))

    def create_pair(self, active='queued'):
        old = self.store.create_task('https://www.douyin.com/user/sample', 'author')
        self.store.update_task(old['id'], status='failed', message='原失败原因', completed=7, total=234, preview=[{'id':'123'}])
        new = self.store.create_task(old['link'], 'author')
        if active != 'queued':
            self.store.update_task(new['id'], status=active)
        return old, new

    def test_duplicate_retry_follows_existing_and_retains_prior_evidence(self):
        for status in ('queued', 'parsing', 'downloading', 'paused', 'awaiting_confirmation'):
            with self.subTest(status=status):
                old, new = self.create_pair(status)
                current = self.store.requeue_task(old['id'])
                prior = self.store.task(old['id'])
                self.assertEqual(current['id'], new['id'])
                self.assertEqual((prior['status'], prior['completed'], prior['total'], prior['preview']), ('cancelled', 7, 234, [{'id':'123'}]))
                self.assertEqual(prior['message'], f"已由同链接任务 #{new['id']} 接续，已保存内容保留。原结果：原失败原因")
                for _ in range(2):
                    self.assertEqual(self.store.requeue_task(old['id'])['id'], new['id'])
                    self.assertEqual(self.store.task(old['id'])['message'], prior['message'])
                self.store.update_task(new['id'], status='completed')
                self.assertEqual(self.store.requeue_task(old['id'])['id'], new['id'])
                self.assertEqual(len(self.store.tasks()), 2)
                self.store.delete_task(new['id'])
                with self.assertRaises(ValueError):
                    self.store.requeue_task(old['id'])
                self.store.delete_task(old['id'])

    def test_concurrent_retries_follow_one_existing_task(self):
        old, new = self.create_pair()
        barrier = Barrier(2)
        def retry():
            barrier.wait(timeout=3)
            return self.store.requeue_task(old['id'])['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: retry(), range(2)))
        self.assertEqual(results, [new['id'], new['id']])
        self.assertEqual(len(self.store.tasks()), 2)
        self.assertEqual(self.store.task(old['id'])['message'].count('接续'), 1)

    def test_unlinked_completed_task_does_not_cancel_a_legitimate_retry(self):
        old, new = self.create_pair()
        self.store.update_task(new['id'], status='completed')
        self.assertEqual(self.store.requeue_task(old['id'])['id'], old['id'])
        self.assertEqual(self.store.task(old['id'])['status'], 'queued')

    def test_expected_task_can_finish_before_retry_without_requeueing_old(self):
        old, new = self.create_pair()
        self.store.update_task(new['id'], status='completed')
        self.assertEqual(self.store.requeue_task(old['id'], new['id'])['id'], new['id'])
        self.assertEqual(self.store.task(old['id'])['status'], 'cancelled')
        self.assertEqual(self.store.requeue_task(old['id'], new['id'])['id'], new['id'])
        self.assertEqual(len(self.store.tasks()), 2)

    def test_expected_missing_failed_or_different_link_is_rejected_unchanged(self):
        old, new = self.create_pair()
        other = self.store.create_task('https://www.douyin.com/user/other', 'author')
        self.store.update_task(new['id'], status='failed')
        before = self.store.task(old['id'])
        for bad in ('0000000000000000', new['id'], other['id']):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.requeue_task(old['id'], bad)
            self.assertEqual(self.store.task(old['id']), before)

    def test_existing_link_cannot_be_repointed(self):
        old, new = self.create_pair()
        self.store.requeue_task(old['id'], new['id'])
        before = self.store.task(old['id'])
        other = self.store.create_task('https://www.douyin.com/user/other', 'author')
        with self.assertRaises(ValueError):
            self.store.requeue_task(old['id'], other['id'])
        self.assertEqual(self.store.task(old['id']), before)


if __name__ == '__main__':
    unittest.main()
