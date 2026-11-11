import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.comment_assets import download_asset
from app.comments import CommentService
from app.store import Store
from test_comment_fixtures import setup_comment_accounts
from test_comments_service import FakeCore
from test_comments_store import WORK, item


class _Response:
    status_code = 200
    headers = {'content-type': 'image/png'}


class _Session:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def request(self, method, url, **kwargs):
        content = b'\x89PNG\r\n\x1a\ncontent'
        assert kwargs['content_callback'](content) == len(content)
        return _Response()


class CommentPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_comment_step_does_not_block_event_loop_on_database_wait(self):
        class SlowStore:
            def setting(self, key, default=None):
                time.sleep(.25)
                return True
        service = CommentService(SlowStore(), None)
        task = asyncio.create_task(service.step())
        await asyncio.sleep(.05)
        self.assertFalse(task.done(), 'a waiting database call blocked the event loop')
        self.assertFalse(await task)

    async def test_stopped_job_during_threaded_preflight_does_not_send_request(self):
        started, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            setup_comment_accounts(store)
            core = FakeCore()
            service = CommentService(store, core)
            service.queue(WORK, account_role='primary', allow_primary=True)
            prepare = service._prepare_step
            def delayed_prepare():
                result = prepare()
                started.set()
                release.wait(2)
                return result
            with patch.object(service, '_prepare_step', delayed_prepare):
                task = asyncio.create_task(service.step())
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    service.cancel(WORK)
                finally:
                    release.set()
                await task
            self.assertEqual(core.calls, [])
            self.assertEqual(store.comment_state(WORK)['operation'], 'stopped')

    async def test_global_pause_stops_new_comment_and_asset_requests(self):
        for kind in ('page', 'asset'):
            for pause_at in ('before_prepare', 'after_reservation'):
                with self.subTest(kind=kind, pause_at=pause_at), tempfile.TemporaryDirectory() as root:
                    store = Store(Path(root))
                    setup_comment_accounts(store)
                    core = FakeCore()
                    service = CommentService(store, core)
                    service.queue(WORK, account_role='primary', allow_primary=True)
                    if kind == 'asset':
                        job = store.comment_job(WORK)
                        assets = [{'kind': 'image', 'source_url': 'https://p1.douyinpic.com/a', 'status': 'pending', 'error': ''}]
                        store.comment_commit_page(WORK, job['token'], [item('111', attachments=assets)],
                                                  {'cursor': 1, 'root_done': True, 'replies': []}, 1, 0)
                    prepare = service._prepare_step
                    if pause_at == 'before_prepare':
                        store.pause_queue()
                    def pause_after_prepare():
                        result = prepare()
                        store.pause_queue()
                        return result
                    with patch.object(service, '_prepare_step', pause_after_prepare if pause_at == 'after_reservation' else prepare), patch('app.comment_assets.download_asset', new=AsyncMock(return_value={'relative_path': 'comment-assets/fake.png', 'sha256': 'a' * 64, 'bytes': 10})) as download:
                        await service.step()
                    self.assertEqual(core.calls, [])
                    download.assert_not_awaited()
                    if kind == 'asset':
                        self.assertEqual(store.comment_page(WORK)['items'][0]['attachments'][0]['status'], 'pending')

    async def test_image_fsync_does_not_block_event_loop(self):
        with tempfile.TemporaryDirectory() as root, patch('app.comment_assets._validated_target', new=AsyncMock(return_value=('p1.douyinpic.com', '1.1.1.1'))), patch('app.comment_assets.AsyncSession', _Session), patch('app.comment_assets.os.fsync', side_effect=lambda fd: time.sleep(.25)):
            task = asyncio.create_task(download_asset('https://p1.douyinpic.com/a', Path(root), 100))
            await asyncio.sleep(.05)
            self.assertFalse(task.done(), 'image fsync blocked the event loop')
            result = await task
            self.assertEqual((Path(root) / result['relative_path']).read_bytes(), b'\x89PNG\r\n\x1a\ncontent')

    async def test_cancel_during_image_save_does_not_commit_asset(self):
        started, release = threading.Event(), threading.Event()
        def slow_fsync(fd):
            started.set()
            release.wait(2)
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            setup_comment_accounts(store)
            service = CommentService(store, FakeCore())
            service.queue(WORK, account_role='primary', allow_primary=True)
            job = store.comment_job(WORK)
            assets = [{'kind': 'image', 'source_url': 'https://p1.douyinpic.com/a', 'status': 'pending', 'error': ''}]
            store.comment_commit_page(WORK, job['token'], [item('111', attachments=assets)],
                                      {'cursor': 1, 'root_done': True, 'replies': []}, 1, 0)
            with patch('app.comment_assets._validated_target', new=AsyncMock(return_value=('p1.douyinpic.com', '1.1.1.1'))), patch('app.comment_assets.AsyncSession', _Session), patch('app.comment_assets.os.fsync', side_effect=slow_fsync):
                task = asyncio.create_task(service.step())
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    task.cancel()
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(store.comment_page(WORK)['items'][0]['attachments'][0]['status'], 'pending')
            self.assertEqual(store.comment_job(WORK)['asset_bytes'], 0)
            self.assertFalse(list(Path(root).rglob('*.tmp')))

    async def test_cancel_during_image_fsync_leaves_no_partial_file(self):
        started = threading.Event()
        release = threading.Event()
        def slow_fsync(fd):
            started.set()
            release.wait(1)
        with tempfile.TemporaryDirectory() as root, patch('app.comment_assets._validated_target', new=AsyncMock(return_value=('p1.douyinpic.com', '1.1.1.1'))), patch('app.comment_assets.AsyncSession', _Session), patch('app.comment_assets.os.fsync', side_effect=slow_fsync):
            task = asyncio.create_task(download_asset('https://p1.douyinpic.com/a', Path(root), 100))
            watchdog = threading.Timer(.5, release.set)
            watchdog.start()
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(list(Path(root).rglob('*.tmp')))
            finally:
                release.set()
                watchdog.cancel()


if __name__ == '__main__':
    unittest.main()
