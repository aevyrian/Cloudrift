import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import JobService
from app.store import Store


class ConfigurationPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_pause_during_configuration_stop_remains_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root / 'data')
            task = store.create_task('https://example.test/video', 'detail')
            store.claim_task()
            service = JobService(store, object(), root / 'media')
            service.active_task_id = task['id']
            started, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            second_pause = threading.Event()
            original_pause = store.pause_task
            calls = 0

            def observe_pause(task_id):
                nonlocal calls
                result = original_pause(task_id)
                calls += 1
                if calls == 2:
                    second_pause.set()
                return result

            async def active_download():
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelling.set()
                    while not release.is_set():
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            continue
                    raise

            async def update_configuration():
                async with service.configuration():
                    pass

            with patch.object(store, 'pause_task', side_effect=observe_pause):
                service.active_download = asyncio.create_task(active_download())
                await started.wait()
                update = asyncio.create_task(update_configuration())
                await asyncio.wait_for(cancelling.wait(), 3)
                manual_pause = asyncio.create_task(service.pause(task['id']))
                try:
                    self.assertTrue(await asyncio.to_thread(second_pause.wait, 3))
                finally:
                    release.set()
                await asyncio.wait_for(asyncio.gather(update, manual_pause), 3)

            self.assertEqual(store.task(task['id'])['status'], 'paused')
            self.assertEqual(store.task(task['id'])['message'], '已暂停，已保存内容保留')
