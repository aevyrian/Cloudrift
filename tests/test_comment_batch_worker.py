import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.main import JobService, make_app
from app.store import Store
from test_comment_fixtures import COOKIE, setup_comment_accounts
from test_comments_store import item


@asynccontextmanager
async def no_lifespan(app):
    yield


class CommentBatchApiTests(unittest.TestCase):
    def test_auth_validation_cache_and_startup_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(root / 'data', root / 'media', token='test-batch-key')
            app.router.lifespan_context = no_lifespan
            auth = {'Authorization': 'Bearer test-batch-key'}
            batch = 'b' * 32
            start = '/api/authors/author/comment-batches'
            body = {'channel': 'core', 'allow_primary': True, 'request_id': 'a' * 32}
            routes = [('get', '/api/comment-batches/current', None),
                      ('post', start, body),
                      ('post', f'/api/comment-batches/{batch}/stop', None),
                      ('post', f'/api/comment-batches/{batch}/pause', None),
                      ('post', f'/api/comment-batches/{batch}/resume', {'allow_primary': True}),
                      ('get', f'/api/comment-batches/{batch}/browser', None)]
            with TestClient(app) as client:
                for method, url, payload in routes:
                    with self.subTest(url=url):
                        response = client.request(method, url, params={'token': 'test-batch-key'}, json=payload)
                        self.assertEqual(response.status_code, 401)
                        self.assertIn('no-store', response.headers['cache-control'])
                for url, payload in [(start, {**body, 'allow_primary': 'true'}),
                                     (f'/api/comment-batches/{batch}/resume', {'allow_primary': 'true'})]:
                    response = client.post(url, headers=auth, json=payload)
                    self.assertEqual(response.status_code, 422)
                    self.assertIn('no-store', response.headers['cache-control'])
                for url, payload in [(start, body), (f'/api/comment-batches/{batch}/resume', {'allow_primary': True})]:
                    response = client.post(url, headers=auth, json=payload)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn('no-store', response.headers['cache-control'])
                response = client.get('/api/comment-batches/current', headers=auth)
                self.assertEqual(response.json(), {'batch': None})
                self.assertIn('no-store', response.headers['cache-control'])
                with app.state.store.connect() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM comment_batches').fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM comment_jobs').fetchone()[0], 0)


class FakeCore:
    ready = True

    def __init__(self):
        self.app = SimpleNamespace(parameter=SimpleNamespace(headers={'Cookie': COOKIE}))
        self.calls = []
        self.second_started = asyncio.Event()

    async def start(self, cookie, download_dir):
        pass

    async def comment_page(self, work_id, cursor=0, root_id=None):
        self.calls.append(work_id)
        if len(self.calls) == 2:
            self.second_started.set()
            await asyncio.Event().wait()
        return {'items': [item('111')], 'next_cursor': 1, 'has_more': False, 'total': 1}


class CommentBatchWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_progress_survives_restart_and_can_be_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'data')
            setup_comment_accounts(store)
            store.set_setting('comment_channel', 'core')
            store.save_author('author', 'https://www.douyin.com/user/SEC_user_123', '作者')
            for work in ('7431234567890123456', '7431234567890123457'):
                store.save_work({'id': work, 'sec_uid': 'SEC_user_123'}, ['video.mp4'])
            core = FakeCore()
            service = JobService(store, core, root / 'media')
            worker = asyncio.create_task(service.run())
            try:
                async with asyncio.timeout(5):
                    while not service.comment_batches.ready:
                        await asyncio.sleep(.02)
                batch = service.comment_batches.start('author', {
                    'channel': 'core', 'allow_primary': True, 'request_id': 'a' * 32,
                })['batch']
                await asyncio.wait_for(core.second_started.wait(), 15)
                current = service.comment_batches.status()['batch']
                self.assertEqual((current['status'], current['completed'], current['added']), ('running', 1, 1))
                self.assertEqual(len(core.calls), 2)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

            restarted = JobService(Store(root / 'data'), FakeCore(), root / 'media')
            worker = asyncio.create_task(restarted.run())
            try:
                async with asyncio.timeout(5):
                    while not restarted.comment_batches.ready:
                        await asyncio.sleep(.02)
                current = restarted.comment_batches.status()['batch']
                self.assertEqual((current['id'], current['status'], current['completed'], current['added']),
                                 (batch['id'], 'paused', 1, 1))
                current = restarted.comment_batches.resume(batch['id'], allow_primary=True)['batch']
                self.assertEqual((current['status'], current['completed']), ('running', 1))
                stopped = restarted.comment_batches.stop(batch['id'])['batch']
                self.assertEqual((stopped['status'], stopped['completed'], stopped['added']), ('stopped', 1, 1))
                await asyncio.sleep(.1)
                self.assertEqual(restarted.core.calls, [])
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
