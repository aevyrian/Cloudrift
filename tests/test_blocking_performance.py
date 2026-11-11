import asyncio
import importlib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import JobService
from app.store import Store
from test_backend import FakeCore, WORK_ID


class BlockingTests(unittest.IsolatedAsyncioTestCase):
    def runner(self):
        try:
            return importlib.import_module('app.blocking').run_blocking
        except ModuleNotFoundError:
            self.fail('A cancellation-safe background runner is required')

    async def test_background_write_finishes_before_repeated_cancellation_returns(self):
        run_blocking = self.runner()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def write():
            entered.set()
            release.wait(2)
            finished.set()
        task = asyncio.create_task(run_blocking(write))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            task.cancel()
            await asyncio.sleep(.02)
            task.cancel()
            await asyncio.sleep(.02)
            self.assertFalse(task.done())
            self.assertFalse(finished.is_set())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())

    async def test_background_result_and_error_propagate(self):
        run_blocking = self.runner()
        self.assertEqual(await run_blocking(pow, 2, exp=3), 8)
        with self.assertRaisesRegex(ValueError, 'invalid literal'):
            await run_blocking(int, 'bad')

    async def test_download_dedup_does_not_hold_event_loop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = JobService(Store(root/'data'), FakeCore(root/'media'), root/'media')
            service.store.create_task(WORK_ID, 'detail', accept_quality=True)
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            def slow_saved(*args):
                entered.set()
                release.wait(.5)
                finished.set()
                return True
            with patch.object(service, 'saved', side_effect=slow_saved):
                task = asyncio.create_task(service.process_one())
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                    self.assertFalse(finished.is_set(), 'dedup blocked the event loop until disk work ended')
                finally:
                    release.set()
                    await task
            self.assertEqual(service.core.download_calls, 0)
