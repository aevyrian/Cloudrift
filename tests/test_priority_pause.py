import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import asynccontextmanager
from fastapi.testclient import TestClient

from app.cookie_sync import CookieSync
from app.main import JobService, make_app
from app.store import Store

WORK = '7431234567890123456'


class BusyCore:
    ready = True
    error = ''

    def __init__(self, root):
        self.root = root
        self.started = asyncio.Event()
        self.cookies = []

    async def start(self, cookie='', directory=''):
        self.cookies.append(cookie)
        self.ready = True

    async def parse(self, *args):
        return [{'id': WORK, 'type': '视频', 'downloads': 'https://example.invalid/video'}]

    async def download(self, item):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'partial.tmp').write_bytes(b'kept')
        self.started.set()
        await asyncio.Event().wait()


class PriorityPauseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = Store(root / 'data')
        self.store.set_setting('access_token', 'test-owner-key')
        self.store.set_setting('cookie', 'sessionid=old')
        self.core = BusyCore(root / 'media')
        self.service = JobService(self.store, self.core, self.core.root)

    async def active_job(self):
        job = self.store.create_task(WORK, 'detail', accept_quality=True)
        worker = asyncio.create_task(self.service.process_one())
        await asyncio.wait_for(self.core.started.wait(), 2)
        return job, worker

    async def test_pause_current_and_all_preserve_files_and_cooldown(self):
        job, worker = await self.active_job()
        second = self.store.create_task('7431234567890123457', 'detail')
        self.store.set_setting('queue_resume_at', 123)
        changed = await self.service.pause_all()
        await worker
        self.assertEqual(set(changed), {job['id'], second['id']})
        self.assertEqual(self.store.task(job['id'])['status'], 'paused')
        self.assertFalse(await self.service.process_one())
        self.assertEqual(self.store.setting('queue_resume_at'), 123)
        self.assertEqual((self.core.root / 'partial.tmp').read_bytes(), b'kept')
        self.assertEqual(set(self.store.resume_queue()), set(changed))

    async def test_cookie_sync_preempts_busy_download_and_requeues_it(self):
        job, worker = await self.active_job()
        sync = CookieSync(self.store, self.service)
        request = await sync.create('test-owner-key')
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            sync.submit(request['id'], 'test-owner-key', cookie='sessionid=new')
            await asyncio.wait_for(sync.task, 2)
        await worker
        self.assertEqual(sync.view(request['id'], 'test-owner-key')['status'], 'success')
        self.assertEqual(self.store.setting('cookie'), 'sessionid=new')
        self.assertEqual(self.store.task(job['id'])['status'], 'queued')
        self.assertFalse(self.service.configuration_pending)
        self.assertEqual((self.core.root / 'partial.tmp').read_bytes(), b'kept')

    async def test_failed_cookie_keeps_old_value_and_user_pause_wins(self):
        job, worker = await self.active_job()
        sync = CookieSync(self.store, self.service)
        request = await sync.create('test-owner-key')

        async def invalid(cookie):
            self.store.pause_queue()
            return False

        with patch('app.cookie_sync.verify_cookie', side_effect=invalid):
            sync.submit(request['id'], 'test-owner-key', cookie='sessionid=invalid')
            await asyncio.wait_for(sync.task, 2)
        await worker
        self.assertEqual(self.store.setting('cookie'), 'sessionid=old')
        self.assertEqual(self.store.task(job['id'])['status'], 'paused')
        self.assertEqual(sync.view(request['id'], 'test-owner-key')['reason'], 'verify_failed')


class PauseApiTests(unittest.TestCase):
    def test_pause_resume_apis_and_global_pause_covers_new_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            app=make_app(root/'data',root/'media',token='test-owner-key')
            @asynccontextmanager
            async def idle(app):
                yield
            app.router.lifespan_context=idle
            auth={'Authorization':'Bearer test-owner-key'}
            with TestClient(app) as client:
                first=client.post('/api/tasks',headers=auth,json={'link':WORK}).json()
                self.assertEqual(client.post('/api/queue/pause').status_code,401)
                self.assertEqual(client.post('/api/queue/pause',headers=auth).json()['changed'],[first['id']])
                second=client.post('/api/tasks',headers=auth,json={'link':'7431234567890123457'}).json()
                self.assertEqual(second['status'],'paused')
                self.assertEqual(client.post(f"/api/tasks/{first['id']}/resume",headers=auth).json()['status'],'queued')
                self.assertEqual(app.state.store.task(second['id'])['status'],'paused')
                self.assertEqual(client.post(f"/api/tasks/{first['id']}/pause",headers=auth).json()['status'],'paused')
                self.assertEqual(set(client.post('/api/queue/resume',headers=auth).json()['changed']),{first['id'],second['id']})


if __name__ == '__main__':
    unittest.main()
