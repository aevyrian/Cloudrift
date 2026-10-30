import asyncio
import tempfile
import unittest
from unittest.mock import patch

from app.cookie_sync import CookieSync
from app.main import JobService
from app.store import Store


OWNER = 'test-admin-key'
OLD = 'sessionid_ss=old-test-value'
NEW = 'sessionid_ss=new-test-value'


class StartupCore:
    def __init__(self):
        self.ready = False
        self.cookie = None
        self.initial_started = asyncio.Event()
        self.release_initial = asyncio.Event()
        self.new_started = asyncio.Event()

    async def start(self, cookie, directory):
        if cookie == OLD:
            self.initial_started.set()
            await self.release_initial.wait()
        else:
            self.new_started.set()
        self.cookie = cookie
        self.ready = True


class CookieStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_waits_for_startup_and_core_matches_saved_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.set_setting('access_token', OWNER)
            store.set_setting('cookie', OLD)
            core = StartupCore()
            service = JobService(store, core, directory)
            sync = CookieSync(store, service)

            async def idle_loop():
                await asyncio.Event().wait()

            service.run_queue = idle_loop
            service.run_schedule = idle_loop
            worker = asyncio.create_task(service.run())
            try:
                await asyncio.wait_for(core.initial_started.wait(), 2)
                with patch('app.cookie_sync.verify_cookie', return_value=True):
                    request = await sync.create(OWNER)
                    sync.submit(request['id'], OWNER, NEW)
                    await asyncio.sleep(0.05)
                    self.assertFalse(core.new_started.is_set(), '同步不能与启动初始化同时修改下载核心')
                    self.assertEqual(store.setting('cookie'), OLD)
                    core.release_initial.set()
                    await asyncio.wait_for(sync.task, 2)
                    self.assertEqual(sync.view(request['id'], OWNER)['status'], 'success')
                    self.assertEqual(store.setting('cookie'), NEW)
                    self.assertEqual(core.cookie, store.setting('cookie'))
            finally:
                core.release_initial.set()
                await sync.close()
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
