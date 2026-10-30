import asyncio
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import make_app


TOKEN = 'test-admin-key'
COOKIE = 'sessionid=test-cookie'


class FakeCore:
    def __init__(self):
        self.calls = []
        self.value = 0
        self.error = None
        self.delay = 0
        self.active = 0
        self.max_active = 0
        self.started = Event()

    async def author_count(self, sec_uid, cookie):
        self.calls.append((sec_uid, cookie))
        self.started.set()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.error:
                raise self.error
            return self.value
        finally:
            self.active -= 1


@asynccontextmanager
async def no_lifespan(app):
    yield


class AuthorCountApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.core = FakeCore()
        self.app = make_app(root / 'data', root / 'media', token=TOKEN, core=self.core)
        self.app.router.lifespan_context = no_lifespan
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Authorization': f'Bearer {TOKEN}'}
        self.app.state.store.set_setting('cookie', COOKIE)

    def get(self, uid='valid_author', headers=None):
        return self.client.get('/api/author-count', params={'sec_uid': uid}, headers=self.headers if headers is None else headers)

    def test_requires_auth_and_valid_identity_without_side_effects(self):
        self.assertEqual(self.get(headers={}).status_code, 401)
        for uid in ['', '../bad', 'a' * 129, 'bad/author']:
            self.assertEqual(self.get(uid).status_code, 422)
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.app.state.store.authors(), [])
        self.assertEqual(self.app.state.store.tasks(), [])

    def test_zero_success_is_cached_and_cookie_change_invalidates(self):
        first = self.get()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {'sec_uid': 'valid_author', 'count': 0})
        self.assertEqual(first.headers['cache-control'], 'no-store')
        self.assertEqual(self.get().json(), first.json())
        self.assertEqual(len(self.core.calls), 1)
        self.app.state.store.set_setting('cookie', 'sessionid=new-cookie')
        self.core.value = 42
        self.assertEqual(self.get().json()['count'], 42)
        self.assertEqual(len(self.core.calls), 2)
        self.assertEqual(self.app.state.store.authors(), [])
        self.assertEqual(self.app.state.store.tasks(), [])

    def test_failure_is_safe_cached_and_missing_cookie_does_not_query(self):
        self.core.error = RuntimeError('private-secret')
        for _ in range(2):
            response = self.get()
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private-secret', response.text)
            self.assertNotIn(COOKIE, response.text)
            self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(len(self.core.calls), 1)
        self.app.state.store.set_setting('cookie', '')
        self.assertEqual(self.get().status_code, 503)
        self.assertEqual(len(self.core.calls), 1)

    def test_concurrent_same_author_is_one_query_and_download_lock_is_not_taken(self):
        self.core.delay = 0.1
        self.client.portal.call(self.app.state.service.lock.acquire)
        self.addCleanup(self.client.portal.call, self.app.state.service.lock.release)
        barrier = Barrier(6)

        def request():
            barrier.wait(timeout=3)
            return self.get()

        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: request(), range(6)))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(len(self.core.calls), 1)
        self.assertTrue(self.app.state.service.lock.locked())

    def test_different_authors_use_at_most_two_connections(self):
        self.core.delay = 0.1
        barrier = Barrier(4)

        def request(uid):
            barrier.wait(timeout=3)
            return self.get(uid)

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(request, ['one', 'two', 'three', 'four']))
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(len(self.core.calls), 4)
        self.assertEqual(self.core.max_active, 2)

    def test_cache_expiry_and_bounded_size(self):
        now = [1000.0]
        clock = SimpleNamespace(monotonic=lambda: now[0], time=time.time)
        with patch('app.main.time', clock):
            self.core.value = 7
            self.assertEqual(self.get().json()['count'], 7)
            now[0] += 299
            self.assertEqual(self.get().json()['count'], 7)
            self.assertEqual(len(self.core.calls), 1)
            now[0] += 1
            self.assertEqual(self.get().json()['count'], 7)
            self.assertEqual(len(self.core.calls), 2)
            self.core.error = RuntimeError('private')
            self.assertEqual(self.get('failed').status_code, 503)
            self.core.error = None
            now[0] += 29
            self.assertEqual(self.get('failed').status_code, 503)
            now[0] += 1
            self.assertEqual(self.get('failed').status_code, 200)
            for index in range(129):
                self.assertEqual(self.get(f'author_{index}').status_code, 200)
            calls = len(self.core.calls)
            self.assertEqual(self.get().status_code, 200)
            self.assertEqual(len(self.core.calls), calls + 1, 'old cache entries must be evicted')

    def test_cookie_change_during_request_rejects_stale_result(self):
        self.core.delay = 0.1
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.get)
            self.assertTrue(self.core.started.wait(timeout=3))
            self.app.state.store.set_setting('cookie', 'sessionid=new-cookie')
            self.assertEqual(pending.result(timeout=3).status_code, 503)
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(len(self.core.calls), 2)

    def test_timeout_releases_slots_and_caches_failure(self):
        real_asyncio = asyncio
        fake_asyncio = SimpleNamespace(timeout=lambda _: real_asyncio.timeout(0.04),
                                       create_task=real_asyncio.create_task, shield=real_asyncio.shield)
        self.core.delay = 0.15
        barrier = Barrier(3)

        def request(uid):
            barrier.wait(timeout=3)
            return self.get(uid)

        with patch('app.main.asyncio', fake_asyncio), ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(request, ['slow_a', 'slow_b', 'slow_c']))
            self.assertTrue(all(r.status_code == 503 for r in responses))
            self.assertEqual(self.core.active, 0)
            calls = len(self.core.calls)
            self.assertEqual(self.get('slow_a').status_code, 503)
            self.assertEqual(len(self.core.calls), calls)
            self.core.delay = 0
            self.assertEqual(self.get('after_timeout').status_code, 200)


if __name__ == '__main__':
    unittest.main()
