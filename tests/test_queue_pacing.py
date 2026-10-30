import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import JobService, make_app, recorded_downloads
from app.store import Store
from test_backend import FakeCore, WORK_ID


class QueuePacingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.core = FakeCore(self.root / 'media')
        self.service = JobService(self.store, self.core, self.root / 'media')
        self.url = 'https://www.douyin.com/user/abc'

    async def test_first_author_rests_before_next_task_and_restart_keeps_deadline(self):
        first = self.store.create_task(self.url, 'author')
        second = self.store.create_task('7431234567890123457', 'detail')
        with patch('app.main.time.time', return_value=1000), patch('app.main.secrets.randbelow', return_value=0):
            await self.service.process_one()
        self.assertEqual(self.store.task(first['id'])['status'], 'completed')
        self.assertEqual(self.store.setting('queue_resume_at'), 1300)
        restarted = JobService(Store(self.root / 'data'), self.core, self.root / 'media')
        with patch('app.main.time.time', return_value=1299):
            self.assertFalse(await restarted.process_one())
        self.assertEqual(self.store.task(second['id'])['status'], 'queued')
        with patch('app.main.time.time', return_value=1300):
            self.assertTrue(await restarted.process_one())
        self.assertEqual(self.store.task(second['id'])['status'], 'completed')

    async def test_first_author_upper_bound_and_repeat_uses_update_range(self):
        self.store.create_task(self.url, 'author')
        with patch('app.main.time.time', return_value=1000), patch('app.main.secrets.randbelow', return_value=180):
            await self.service.process_one()
        self.assertEqual(self.store.setting('queue_resume_at'), 1480)
        repeated = self.store.create_task(self.url, 'author')
        with patch('app.main.time.time', return_value=1500), patch('app.main.secrets.randbelow', return_value=180):
            await self.service.process_one()
        self.assertEqual(self.store.setting('queue_resume_at'), 1800)
        self.assertEqual(self.store.task(repeated['id'])['completed'], 0)
        self.assertEqual(self.core.download_calls, 1)
        self.store.create_task(self.url, 'author')
        with patch('app.main.time.time', return_value=2000), patch('app.main.secrets.randbelow', return_value=0):
            await self.service.process_one()
        self.assertEqual(self.store.setting('queue_resume_at'), 2120)

    async def test_existing_complete_work_counts_as_update_without_author_history(self):
        self.store.create_task(WORK_ID, 'detail')
        await self.service.process_one()
        self.assertEqual(self.store.setting('queue_resume_at', 0), 0)
        self.store.create_task(self.url, 'author')
        with patch('app.main.time.time', return_value=1000), patch('app.main.secrets.randbelow', return_value=0):
            await self.service.process_one()
        self.assertEqual(self.store.setting('queue_resume_at'), 1120)
        self.assertEqual(self.core.download_calls, 1)

    async def test_quality_confirmation_does_not_start_rest(self):
        self.store.set_setting('quality_policy', 'confirm')
        task = self.store.create_task(self.url, 'author')
        await self.service.process_one()
        self.assertEqual(self.store.task(task['id'])['status'], 'awaiting_confirmation')
        self.assertEqual(self.store.setting('queue_resume_at', 0), 0)

    async def test_failed_author_still_rests_to_avoid_immediate_retry_burst(self):
        async def fail(*args):
            raise RuntimeError('作品接口暂时失败')
        self.core.parse = fail
        task = self.store.create_task(self.url, 'author')
        with patch('app.main.time.time', return_value=1000), patch('app.main.secrets.randbelow', return_value=0):
            await self.service.process_one()
        self.assertEqual(self.store.task(task['id'])['status'], 'failed')
        self.assertEqual(self.store.setting('queue_resume_at'), 1300)

    async def test_cancelled_author_keeps_partial_files_and_rest_before_retry(self):
        started = asyncio.Event()
        async def blocked(item):
            (self.root / 'media' / 'keep.part').write_bytes(b'partial')
            started.set()
            await asyncio.Event().wait()
        self.core.download = blocked
        task = self.store.create_task(self.url, 'author')
        with patch('app.main.time.time', return_value=1000), patch('app.main.secrets.randbelow', return_value=0):
            processing = asyncio.create_task(self.service.process_one())
            await asyncio.wait_for(started.wait(), 2)
            await self.service.cancel(task['id'])
            await asyncio.wait_for(processing, 2)
            self.service.retry(task['id'])
            self.assertFalse(await self.service.process_one())
        self.assertEqual(self.store.setting('queue_resume_at'), 1300)
        self.assertEqual((self.root / 'media' / 'keep.part').read_bytes(), b'partial')
        self.assertEqual(self.store.task(task['id'])['status'], 'queued')

    async def test_legacy_record_and_unknown_names_are_checked_once_per_batch(self):
        legacy_root = self.root / 'legacy'
        legacy_root.mkdir()
        with sqlite3.connect(legacy_root / 'DouK-Downloader.db') as db:
            db.execute('CREATE TABLE download_data(ID TEXT PRIMARY KEY)')
        db.close()
        self.store.set_setting('legacy_scan', {'status': 'completed'})
        self.service.legacy_root = legacy_root
        parse = self.core.parse
        async def many(link, kind):
            item = (await parse(link, kind))[0]
            return [{**item, 'uid': 'same-author', 'id': str(int(WORK_ID) + i)} for i in range(3)]
        self.core.parse = many
        task = self.store.create_task(self.url, 'author')
        with patch('app.main.recorded_downloads', wraps=recorded_downloads) as records, patch.object(
                self.store, 'legacy_unidentified', wraps=self.store.legacy_unidentified) as names:
            await self.service.process_one()
        self.assertEqual(self.store.task(task['id'])['completed'], 3)
        self.assertEqual(records.call_count, 1)
        self.assertEqual(names.call_count, 1)


class PacingApiTests(unittest.TestCase):
    def test_queued_tasks_show_rest_deadline_without_changing_stored_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(root / 'data', root / 'media', token='test-key', core=FakeCore(root / 'media'))
            store = app.state.store
            store.set_setting('queue_resume_at', 9999999999)
            task = store.create_task(WORK_ID, 'detail')
            with TestClient(app) as client:
                response = client.get('/api/tasks', headers={'Authorization': 'Bearer test-key'})
                self.assertEqual(response.status_code, 200)
                self.assertIn('休息', response.json()[0]['message'])
                self.assertEqual(response.json()[0]['queue_resume_at'], 9999999999)
                self.assertEqual(client.get('/health').status_code, 200)
            self.assertEqual(store.task(task['id'])['message'], '')
