import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.comment_accounts import AccountError
from app.comment_batch import CommentBatchService
from app.comment_browser import BrowserCommentService
from app.store import Store
from test_comment_fixtures import setup_comment_accounts


WORK = '7431234567890123456'


class CommentBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        setup_comment_accounts(self.store)
        self.author = self.store.save_author('author', 'https://www.douyin.com/user/SEC_user_123', '作者')
        self.store.save_work({'id': WORK, 'sec_uid': 'SEC_user_123'}, ['video.mp4'])
        self.browser = BrowserCommentService(self.store)
        self.service = CommentBatchService(self.store, None, self.browser)
        self.service.recover()

    def start(self, channel='browser', allow_primary=False, request_id='a'*32):
        self.store.set_setting('comment_channel', channel)
        return self.service.start('author', {'channel': channel, 'allow_primary': allow_primary, 'request_id': request_id})['batch']

    def test_browser_idempotent_stop_and_token_ownership(self):
        batch = self.start()
        self.assertEqual(batch['status'], 'waiting_browser')
        current = self.service.browser_current(batch['id'])
        self.assertEqual(current['work']['work_id'], WORK)
        self.assertFalse(current['work']['claimed'])
        with self.store.connect() as db:
            db.execute('UPDATE comment_jobs SET client_id=? WHERE work_id=?', ('c'*32, WORK))
        current = self.service.browser_current(batch['id'])
        self.assertTrue(current['work']['claimed'])
        self.assertNotIn('client_id', current['work'])
        self.assertEqual(self.start()['id'], batch['id'])
        self.assertNotIn('token', str(self.service.status()))
        with self.store.connect() as db:
            db.execute("UPDATE comment_jobs SET token=? WHERE work_id=?", ('b'*32, WORK))
        self.service.stop(batch['id'])
        self.assertEqual(self.store.comment_job(WORK)['operation'], 'browser')
        self.assertIsNone(self.service.browser_current(batch['id'])['work'])

    def test_core_requires_explicit_primary_and_pauses_on_recovery(self):
        with self.assertRaises(AccountError):
            self.start('core')
        batch = self.start('core', True)
        self.assertEqual(self.store.comment_job(WORK)['operation'], 'queued')
        self.service.recover()
        self.assertEqual(self.service.status()['batch']['status'], 'paused')
        self.assertEqual(self.store.comment_job(WORK)['operation'], 'interrupted')
        with self.assertRaises(AccountError):
            self.service.resume(batch['id'])
        self.service.resume(batch['id'], True)
        self.service.step()
        self.assertEqual(self.store.comment_job(WORK)['operation'], 'queued')

    def test_pause_stops_dispatch_and_finished_advances(self):
        batch = self.start()
        token = self.service.browser_current(batch['id'])['work']['token']
        self.store.comment_accounts.pause()
        self.service.step()
        self.assertEqual(self.service.status()['batch']['status'], 'paused')
        self.store.comment_accounts.resume(True)
        self.service.resume(batch['id'])
        current = self.service.browser_current(batch['id'])['work']
        self.assertNotEqual(token, current['token'])
        self.store.comment_end(WORK, current['token'], 'finished', '', finished=True)
        self.service.step()
        self.assertEqual(self.service.status()['batch']['status'], 'completed')
        with self.assertRaises(HTTPException):
            self.service.resume(batch['id'])

    def test_failed_item_does_not_dispatch_next(self):
        self.store.save_work({'id': '7431234567890123457', 'sec_uid': 'SEC_user_123'}, ['b'])
        batch = self.start()
        current = self.service.browser_current(batch['id'])['work']
        self.store.comment_end(current['work_id'], current['token'], 'failed', '模拟失败')
        self.service.step()
        self.assertEqual(self.service.status()['batch']['status'], 'paused')
        self.assertIsNone(self.store.comment_job(WORK))
        self.service.resume(batch['id'])
        self.assertEqual(self.service.browser_current(batch['id'])['work']['work_id'], current['work_id'])
        self.assertIsNone(self.store.comment_job(WORK))

    def test_recovery_preserves_completed_count_and_resume_dispatches(self):
        self.store.save_work({'id': '7431234567890123457', 'sec_uid': 'SEC_user_123'}, ['b'])
        batch = self.start()
        first = self.service.browser_current(batch['id'])['work']
        self.store.comment_end(first['work_id'], first['token'], 'finished', '', finished=True)
        self.service.step()
        second = self.service.browser_current(batch['id'])['work']
        self.assertEqual(self.service.status()['batch']['completed'], 1)
        self.service.recover()
        self.assertEqual(self.service.status()['batch']['completed'], 1)
        resumed = self.service.resume(batch['id'])['batch']
        self.assertEqual(resumed['completed'], 1)
        after = self.service.browser_current(batch['id'])['work']
        self.assertEqual(after['work_id'], second['work_id'])
        self.assertNotEqual(after['token'], second['token'])

    def test_alias_change_pauses_without_dispatch(self):
        self.store.save_work({'id': 'legacy-one', 'uid': '111'}, ['old'])
        self.store.save_work({'id': '7431234567890123457', 'sec_uid': 'SEC_user_123'}, ['new'])
        task = self.store.create_task(self.author['url'], 'author', author_id='author')
        self.store.update_task(task['id'], preview=[{'sec_uid': 'SEC_user_123', 'uid': '111'}])
        with self.store.connect() as db:
            db.execute('INSERT INTO work_aliases VALUES (?,?)', ('7431234567890123458', 'legacy-one'))
        batch = self.start()
        first = self.service.browser_current(batch['id'])['work']
        with self.store.connect() as db:
            db.execute('DELETE FROM work_aliases WHERE work_id=?', ('legacy-one',))
        self.store.comment_end(first['work_id'], first['token'], 'finished', '', finished=True)
        self.service.step()
        second = self.service.browser_current(batch['id'])['work']
        if second:
            self.store.comment_end(second['work_id'], second['token'], 'finished', '', finished=True)
            self.service.step()
        self.assertEqual(self.service.status()['batch']['status'], 'paused')
        self.assertIsNone(self.store.comment_job('7431234567890123458'))

    def test_added_counts_active_then_stop_once(self):
        batch = self.start()
        with self.store.connect() as db:
            db.execute('UPDATE comment_jobs SET new_comments=5 WHERE work_id=?', (WORK,))
        self.assertEqual(self.service.status()['batch']['added'], 5)
        self.assertEqual(self.service.stop(batch['id'])['batch']['added'], 5)
        self.assertEqual(self.service.stop(batch['id'])['batch']['added'], 5)

    def test_single_work_queue_still_uses_own_transaction(self):
        job = self.store.comment_queue(WORK, 'save', account_role='primary', allow_primary=True)
        self.assertEqual(job['operation'], 'queued')
        self.assertEqual(self.store.comment_job(WORK)['token'], job['token'])

    def test_startup_rejects_start_and_resume_without_writes(self):
        self.service.ready = False
        with self.assertRaises(HTTPException) as start_error:
            self.start()
        self.assertEqual(start_error.exception.status_code, 503)
        self.assertEqual(self.service.status(), {'batch': None})
        self.assertIsNone(self.store.comment_job(WORK))
        with self.assertRaises(HTTPException) as resume_error:
            self.service.resume('missing')
        self.assertEqual(resume_error.exception.status_code, 503)
        self.service.recover()
        self.assertEqual(self.start()['status'], 'waiting_browser')

    def test_pause_resume_counts_once_and_does_not_interrupt_other_token(self):
        batch = self.start()
        first = self.service.browser_current(batch['id'])['work']
        with self.store.connect() as db:
            db.execute('UPDATE comment_jobs SET new_comments=5 WHERE work_id=?', (WORK,))
        self.assertEqual(self.service.pause(batch['id'])['batch']['added'], 5)
        self.assertEqual(self.store.comment_job(WORK)['operation'], 'interrupted')
        self.assertEqual(self.service.pause(batch['id'])['batch']['added'], 5)
        resumed = self.service.resume(batch['id'])['batch']
        second = self.service.browser_current(batch['id'])['work']
        self.assertEqual(resumed['added'], 5)
        self.assertNotEqual(first['token'], second['token'])
        with self.store.connect() as db:
            db.execute('UPDATE comment_jobs SET token=? WHERE work_id=?', ('b'*32, WORK))
        self.service.pause(batch['id'])
        self.assertEqual(self.store.comment_job(WORK)['operation'], 'browser')
        with self.assertRaises(HTTPException):
            self.service.resume(batch['id'])
