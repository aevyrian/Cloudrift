import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient
from app.main import make_app


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_library_query_does_not_block_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'APP_LEGACY': ''}):
            root = Path(folder)
            app = make_app(root / 'data', root / 'media', token='test-navigation-key', core=SimpleNamespace(ready=True, error=''))
            started, release = threading.Event(), threading.Event()
            original = app.state.store.library
            def slow_library(*args, **kwargs):
                started.set()
                release.wait(1)
                return original(*args, **kwargs)
            app.state.store.library = slow_library
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers={'Authorization': 'Bearer test-navigation-key'}) as client:
                library = asyncio.create_task(client.get('/api/library?seed=123'))
                try:
                    while not started.is_set():
                        await asyncio.sleep(.001)
                    settings = await asyncio.wait_for(client.get('/api/settings'), .4)
                    self.assertEqual(settings.status_code, 200)
                    self.assertFalse(library.done(), '作品库同步查询占住事件循环，设置只能等查询结束')
                finally:
                    release.set()
                    await library
