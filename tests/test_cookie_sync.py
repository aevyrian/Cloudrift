import asyncio
import tempfile
import unittest
from unittest.mock import patch

from app.cookie_sync import CookieSync, verify_cookie
from app.store import Store


OWNER = 'test-admin-key'
OLD = 'sessionid_ss=old-test-value; ttwid=old'
NEW = 'sessionid_ss=new-test-value; ttwid=new'


class CoreStub:
    def __init__(self):
        self.ready = True
        self.calls = []
        self.fail = set()
        self.entered = asyncio.Event()
        self.resume = None
        self.rollback_entered = asyncio.Event()
        self.rollback_resume = None

    async def start(self, cookie, directory):
        self.calls.append(cookie)
        self.ready = False
        if cookie == NEW and self.resume:
            self.entered.set()
            await self.resume.wait()
        if cookie == OLD and self.rollback_resume:
            self.rollback_entered.set()
            await self.rollback_resume.wait()
        if cookie in self.fail:
            raise RuntimeError('private response: ' + cookie)
        self.ready = True


class ServiceStub:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.core = CoreStub()


class SyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store.set_setting('cookie', OLD)
        self.store.set_setting('access_token', OWNER)
        self.service = ServiceStub()
        self.sync = CookieSync(self.store, self.service)

    async def asyncTearDown(self):
        await self.sync.close()
        self.tmp.cleanup()

    async def wait_status(self, request_id, statuses):
        async def poll():
            while True:
                current = self.sync.view(request_id, OWNER)
                if current['status'] in statuses:
                    return current
                await asyncio.sleep(.001)
        return await asyncio.wait_for(poll(), 2)

    async def test_success_keeps_credentials_out_of_public_status_and_blocks_replay(self):
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            first = await self.sync.create(OWNER)
            self.assertEqual(first['status'], 'waiting')
            self.assertEqual((await self.sync.create(OWNER))['id'], first['id'])
            with self.assertRaises(ValueError):
                self.sync.view(first['id'], 'not-the-owner')
            initial = self.sync.submit(first['id'], OWNER, NEW)
            self.assertEqual(initial['status'], 'validating')
            with self.assertRaises(ValueError):
                self.sync.submit(first['id'], OWNER, NEW)
            result = await self.wait_status(first['id'], {'success', 'failed'})
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.store.setting('cookie'), NEW)
        self.assertEqual(self.service.core.calls, [NEW])
        self.assertNotIn(OLD, str(result)); self.assertNotIn(NEW, str(result)); self.assertNotIn(OWNER, str(result))

    async def test_failed_probe_keeps_old_cookie_and_never_starts_core(self):
        with patch('app.cookie_sync.verify_cookie', return_value=False):
            first = await self.sync.create(OWNER)
            self.sync.submit(first['id'], OWNER, NEW)
            result = await self.wait_status(first['id'], {'success', 'failed'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'verify_failed')
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertEqual(self.service.core.calls, [])
        self.assertNotIn(NEW, str(result))

    async def test_expiry_and_browser_error_are_single_use_and_redacted(self):
        with patch('app.cookie_sync.time.monotonic', return_value=100):
            first = await self.sync.create(OWNER)
        with patch('app.cookie_sync.time.monotonic', return_value=701):
            with self.assertRaises(ValueError):
                self.sync.submit(first['id'], OWNER, NEW)
        second = await self.sync.create(OWNER)
        result = self.sync.submit(second['id'], OWNER, error='sensitive-error-' + NEW)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'read_failed')
        self.assertNotIn(NEW, str(result))
        with self.assertRaises(ValueError):
            self.sync.submit(second['id'], OWNER, NEW)
        self.assertEqual(self.store.setting('cookie'), OLD)

    async def test_rejects_controls_empty_and_excessive_cookie_without_network(self):
        for value in ('', 'sessionid=x\r\nCookie: bad', 'x=' + 'x' * 30000):
            first = await self.sync.create(OWNER)
            with patch('app.cookie_sync.verify_cookie') as check:
                result = self.sync.submit(first['id'], OWNER, value)
                self.assertEqual(result['status'], 'failed')
                check.assert_not_called()
            self.assertEqual(self.store.setting('cookie'), OLD)

    async def test_cookie_changed_during_validation_does_not_overwrite_newer_value(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def verify(_cookie):
            entered.set(); await release.wait(); return True
        with patch('app.cookie_sync.verify_cookie', verify):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await entered.wait()
            self.store.set_setting('cookie', 'sessionid_ss=changed-by-user')
            release.set()
            result = await self.wait_status(request['id'], {'success', 'failed'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'changed')
        self.assertEqual(self.store.setting('cookie'), 'sessionid_ss=changed-by-user')
        self.assertEqual(self.service.core.calls, [])

    async def test_waits_for_download_lock_and_does_not_break_active_work(self):
        await self.service.lock.acquire()
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await self.wait_status(request['id'], {'waiting_apply'})
            self.assertEqual(self.service.core.calls, [])
            self.assertEqual(self.store.setting('cookie'), OLD)
            self.service.lock.release()
            result = await self.wait_status(request['id'], {'success', 'failed'})
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.store.setting('cookie'), NEW)

    async def test_rotated_admin_while_waiting_for_lock_is_not_authorized(self):
        await self.service.lock.acquire()
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await self.wait_status(request['id'], {'waiting_apply'})
            self.store.set_setting('access_token', 'rotated-test-admin')
            self.service.lock.release()
            await asyncio.sleep(.02)
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertEqual(self.service.core.calls, [])
        with self.assertRaises(ValueError):
            self.sync.view(request['id'], OWNER)

    async def test_start_failure_recovers_old_cookie_and_does_not_expose_error(self):
        self.service.core.fail.add(NEW)
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            result = await self.wait_status(request['id'], {'success', 'failed'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'apply_failed')
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertEqual(self.service.core.calls, [NEW, OLD])
        self.assertTrue(self.service.core.ready)
        self.assertNotIn(NEW, str(result)); self.assertNotIn('private response', str(result))

    async def test_core_restore_failure_is_reported_without_leaking_secrets(self):
        self.service.core.fail.update({NEW, OLD})
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            result = await self.wait_status(request['id'], {'success', 'failed'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'rollback_failed')
        self.assertIn('核心', result['message'])
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertNotIn(NEW, str(result)); self.assertNotIn(OLD, str(result))

    async def test_cancel_during_start_restores_old_core_before_close_returns(self):
        self.service.core.resume = asyncio.Event()
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await self.service.core.entered.wait()
            await self.sync.close()
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertEqual(self.service.core.calls, [NEW, OLD])
        self.assertTrue(self.service.core.ready)
        self.assertFalse(self.service.lock.locked())

    async def test_failed_database_write_restores_old_core(self):
        original = self.store.set_setting
        def write(key, value):
            if key == 'cookie' and value == NEW:
                raise OSError('private candidate: ' + NEW)
            original(key, value)
        with patch('app.cookie_sync.verify_cookie', return_value=True), patch.object(self.store, 'set_setting', side_effect=write):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            result = await self.wait_status(request['id'], {'success', 'failed'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertEqual(self.service.core.calls, [NEW, OLD])
        self.assertNotIn(NEW, str(result))

    async def test_cookie_changed_during_core_start_is_not_overwritten(self):
        self.service.core.resume = asyncio.Event()
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await self.service.core.entered.wait()
            newer = 'sessionid_ss=changed-outside-lock'
            self.store.set_setting('cookie', newer)
            self.service.core.resume.set()
            result = await self.wait_status(request['id'], {'success', 'failed'})
        self.assertEqual(result['reason'], 'changed')
        self.assertEqual(self.store.setting('cookie'), newer)
        self.assertEqual(self.service.core.calls, [NEW, newer])

    async def test_admin_key_rotation_during_core_start_restores_old_core(self):
        self.service.core.resume = asyncio.Event()
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await self.service.core.entered.wait()
            self.store.set_setting('access_token', 'rotated-test-admin')
            self.service.core.resume.set()
            await asyncio.wait_for(self.sync.task, 2)
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertEqual(self.service.core.calls, [NEW, OLD])
        with self.assertRaises(ValueError):
            self.sync.view(request['id'], OWNER)

    async def test_close_before_worker_starts_fails_without_changing_cookie(self):
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await self.sync.close()
        self.assertEqual(self.sync.view(request['id'], OWNER)['status'], 'failed')
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertEqual(self.service.core.calls, [])

    async def test_new_owner_replaces_old_waiter_without_waiting_for_download(self):
        await self.service.lock.acquire()
        try:
            with patch('app.cookie_sync.verify_cookie', return_value=True):
                old = await self.sync.create(OWNER)
                old_item = self.sync.item
                self.sync.submit(old['id'], OWNER, NEW)
                await self.wait_status(old['id'], {'waiting_apply'})
                self.store.set_setting('access_token', 'new-owner')
                first, second = await asyncio.wait_for(asyncio.gather(
                    self.sync.create('new-owner'), self.sync.create('new-owner')), 1)
            self.assertEqual(first['id'], second['id'])
            self.assertNotEqual(first['id'], old['id'])
            self.assertEqual(first['status'], 'waiting')
            self.assertEqual(old_item['reason'], 'changed')
            self.assertEqual(self.service.core.calls, [])
            self.assertEqual(self.store.setting('cookie'), OLD)
        finally:
            self.service.lock.release()

    async def test_new_owner_waits_for_old_core_recovery(self):
        core = self.service.core
        core.resume = asyncio.Event()
        core.rollback_resume = asyncio.Event()
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            old = await self.sync.create(OWNER)
            old_item = self.sync.item
            self.sync.submit(old['id'], OWNER, NEW)
            await core.entered.wait()
            self.store.set_setting('access_token', 'new-owner')
            replacement = asyncio.create_task(self.sync.create('new-owner'))
            await asyncio.wait_for(core.rollback_entered.wait(), 1)
            self.assertFalse(replacement.done())
            self.assertEqual(core.calls, [NEW, OLD])
            core.rollback_resume.set()
            new = await asyncio.wait_for(replacement, 1)
        self.assertEqual(new['status'], 'waiting')
        self.assertEqual(old_item['reason'], 'changed')
        self.assertEqual(self.store.setting('cookie'), OLD)
        self.assertTrue(core.ready)

    async def test_repeated_close_does_not_cancel_core_recovery(self):
        core = self.service.core
        core.resume = asyncio.Event()
        core.rollback_resume = asyncio.Event()
        with patch('app.cookie_sync.verify_cookie', return_value=True):
            request = await self.sync.create(OWNER)
            self.sync.submit(request['id'], OWNER, NEW)
            await core.entered.wait()
            first = asyncio.create_task(self.sync.close())
            await asyncio.wait_for(core.rollback_entered.wait(), 1)
            second = asyncio.create_task(self.sync.close())
            await asyncio.sleep(.01)
            self.assertFalse(second.done())
            core.rollback_resume.set()
            await asyncio.wait_for(asyncio.gather(first, second), 1)
        self.assertEqual(core.calls, [NEW, OLD])
        self.assertTrue(core.ready)
        self.assertEqual(self.store.setting('cookie'), OLD)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_online_probe_requires_signed_in_shape(self):
        class Response:
            status_code = 200
            body = {'status_code': 0, 'notice_count': []}
            def json(self):
                if isinstance(self.body, Exception): raise self.body
                return self.body
        response = Response()
        calls = []
        class Session:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def get(self, url, **kwargs): calls.append((url, kwargs)); return response
        with patch('app.cookie_sync.AsyncSession', Session):
            self.assertTrue(await verify_cookie(NEW))
            url, kwargs = calls.pop()
            self.assertEqual(url, 'https://www.douyin.com/aweme/v1/web/notice/count/?aid=6383&device_platform=webapp')
            self.assertFalse(kwargs['allow_redirects'])
            self.assertEqual(kwargs['headers']['Cookie'], NEW)
            for body in ({'status_code': 8}, {'status_code': 0}, {'status_code': False, 'notice_count': []}, ValueError('private response')):
                response.body = body
                self.assertFalse(await verify_cookie(NEW))
            response.status_code = 302
            self.assertFalse(await verify_cookie(NEW))


if __name__ == '__main__':
    unittest.main()
