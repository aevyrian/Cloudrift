import asyncio
import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx

from app.main import make_app


class ConfigCore:
    ready = True
    error = ''

    def __init__(self):
        self.started = []

    async def start(self, cookie='', download_dir=''):
        self.started.append((cookie, download_dir))
        self.ready = cookie != 'rejected-private-cookie'
        if not self.ready:
            raise RuntimeError('rejected-private-cookie')

    async def close(self):
        pass


class ObservedLock:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.waiting = asyncio.Event()

    async def __aenter__(self):
        self.waiting.set()
        await self.lock.acquire()

    async def __aexit__(self, *args):
        self.lock.release()


class SettingsAtomicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.core = ConfigCore()
        self.app = make_app(root / 'data', root / 'media', token='original-admin-key', core=self.core)
        self.store = self.app.state.store
        self.store.set_setting('cookie', 'original-private-cookie')
        self.auth = {'Authorization': 'Bearer original-admin-key'}
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def test_queued_old_key_cannot_change_settings_or_receive_new_key(self):
        lock = ObservedLock()
        self.app.state.service.lock = lock
        await lock.lock.acquire()
        pending = asyncio.create_task(self.client.put('/api/settings', headers=self.auth,
                                                      json={'quality_policy': 'confirm'}))
        await asyncio.wait_for(lock.waiting.wait(), 2)
        changed = await self.client.put('/api/auth/key', headers=self.auth, json={'key': 'replacement-admin-key'})
        self.assertEqual(changed.status_code, 200)
        lock.lock.release()
        response = await asyncio.wait_for(pending, 2)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('replacement-admin-key', response.text)
        self.assertIsNone(self.store.setting('quality_policy'))

    async def test_read_authorized_before_rotation_never_discloses_replacement_key(self):
        original = self.store.setting
        changed = False

        def rotate_after_auth_read(name, default=None):
            nonlocal changed
            value = original(name, default)
            if name == 'access_token' and not changed:
                changed = True
                self.store.set_setting('access_token', 'replacement-admin-key')
            return value

        with patch.object(self.store, 'setting', side_effect=rotate_after_auth_read):
            response = await self.client.get('/api/settings', headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('replacement-admin-key', response.text)

    async def test_failed_core_settings_restore_previous_config_and_return_error(self):
        response = await self.client.put('/api/settings', headers=self.auth,
                                         json={'cookie': 'rejected-private-cookie', 'quality_policy': 'confirm'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.setting('cookie'), 'original-private-cookie')
        self.assertIsNone(self.store.setting('quality_policy'))
        self.assertTrue(self.core.ready)
        self.assertEqual(self.core.started[-1], ('original-private-cookie', ''))
        self.assertNotIn('private-cookie', response.text)

    async def test_health_is_degraded_when_worker_alive_but_core_failed(self):
        service = self.app.state.service
        service.running = True
        service.worker = asyncio.current_task()
        self.core.ready = False
        response = await self.client.get('/health')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'degraded')

    async def test_database_commit_failure_keeps_config_and_restores_core(self):
        with patch.object(self.store, 'set_settings', side_effect=sqlite3.OperationalError('disk full')):
            response = await self.client.put('/api/settings', headers=self.auth,
                                             json={'cookie': 'new-private-cookie', 'quality_policy': 'confirm'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.setting('cookie'), 'original-private-cookie')
        self.assertIsNone(self.store.setting('quality_policy'))
        self.assertEqual(self.core.started[-1], ('original-private-cookie', ''))
        self.assertTrue(self.core.ready)

    async def test_cancelled_core_start_restores_original_and_releases_lock(self):
        entered = asyncio.Event()
        original = self.core.start

        async def start(cookie='', download_dir=''):
            if cookie == 'new-private-cookie':
                self.core.ready = False
                entered.set()
                await asyncio.Event().wait()
            await original(cookie, download_dir)

        with patch.object(self.core, 'start', side_effect=start):
            request = asyncio.create_task(self.client.put('/api/settings', headers=self.auth,
                                                          json={'cookie': 'new-private-cookie'}))
            await asyncio.wait_for(entered.wait(), 2)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
        self.assertEqual(self.store.setting('cookie'), 'original-private-cookie')
        self.assertTrue(self.core.ready)
        self.assertFalse(self.app.state.service.lock.locked())


if __name__ == '__main__':
    unittest.main()
