import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.comments import CommentService
from app.core import CommentBlocked
from app.store import Store
from test_comments_store import WORK, item


class FakeCore:
    ready = True
    def __init__(self):
        self.calls = []
        self.responses = []
    async def comment_page(self, work_id, cursor=0, root_id=None):
        self.calls.append((work_id, cursor, root_id))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class CommentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.core = FakeCore()
        self.service = CommentService(self.store, self.core)

    async def test_only_manual_queue_and_media_cooldown_have_priority(self):
        self.assertFalse(await self.service.step())
        self.assertEqual(self.core.calls, [])
        self.service.queue(WORK)
        self.store.set_setting('queue_resume_at', 9999999999)
        self.assertFalse(await self.service.step())
        self.store.set_setting('queue_resume_at', 0)
        self.store.create_task(WORK, 'detail')
        self.assertFalse(await self.service.step())
        self.assertEqual(self.core.calls, [])

    async def test_main_reply_traversal_three_second_gap_and_completion(self):
        main = {**item('111'), 'reply_count': 1}
        self.core.responses = [{'items': [main], 'next_cursor': 1, 'has_more': False, 'total': 1},
                               {'items': [item('222', '111', '111')], 'next_cursor': 1, 'has_more': False, 'total': 1}]
        self.service.queue(WORK)
        with patch('app.comments.time.time', return_value=100):
            self.assertTrue(await self.service.step())
            self.assertFalse(await self.service.step())
        with patch('app.comments.time.time', return_value=103):
            self.assertTrue(await self.service.step())
            await self.service.step()
        self.assertEqual([c[2] for c in self.core.calls], [None, '111'])
        state = self.store.comment_state(WORK)
        self.assertEqual((state['operation'], state['integrity'], state['total'], state['replies']), ('finished', 'finished-visible', 2, 1))

    async def test_blocked_stops_queue_and_explicit_resume_unpauses(self):
        self.core.responses = [CommentBlocked('private')] 
        self.service.queue(WORK)
        self.assertTrue(await self.service.step())
        state = self.store.comment_state(WORK)
        self.assertEqual(state['operation'], 'failed')
        self.assertTrue(state['queue_paused'])
        self.assertNotIn('private', state['reason'])
        self.service.queue(WORK, 'continue')
        self.assertFalse(self.store.comment_state(WORK)['queue_paused'])

    async def test_stop_during_request_preserves_data_and_status(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            return {'items': [item('111')], 'next_cursor': 0, 'has_more': False, 'total': 1}
        self.core.comment_page = delayed
        self.service.queue(WORK)
        running = asyncio.create_task(self.service.step())
        await started.wait()
        self.service.cancel(WORK)
        release.set()
        await running
        state = self.store.comment_state(WORK)
        self.assertEqual((state['operation'], state['total']), ('stopped', 1))

    async def test_stale_response_after_stop_and_resume_is_discarded(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            return {'items': [item('111')], 'next_cursor': 0, 'has_more': False, 'total': 1}
        self.core.comment_page = delayed
        self.service.queue(WORK)
        running = asyncio.create_task(self.service.step())
        await started.wait()
        self.service.cancel(WORK)
        self.service.queue(WORK, 'continue')
        release.set()
        await running
        self.assertEqual((self.store.comment_state(WORK)['operation'], self.store.comment_state(WORK)['total']), ('queued', 0))

    async def test_repeated_cursor_is_failure_and_does_not_advance(self):
        self.core.responses = [{'items': [item('111')], 'next_cursor': 0, 'has_more': True, 'total': 1}]
        self.service.queue(WORK)
        await self.service.step()
        state = self.store.comment_state(WORK)
        self.assertEqual((state['operation'], state['total']), ('failed', 0))
        self.assertEqual(self.store.comment_job(WORK)['checkpoint']['cursor'], 0)

    async def test_limits_stop_before_request_and_recover_only_running(self):
        self.service.queue(WORK)
        with self.store.connect() as db:
            db.execute('UPDATE comment_jobs SET requests=200 WHERE work_id=?', (WORK,))
        self.assertTrue(await self.service.step())
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'stopped')
        self.assertEqual(self.core.calls, [])
        self.service.queue(WORK, 'continue')
        self.service.recover()
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'queued')
        self.store.comment_claim()
        self.service.recover()
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'interrupted')

    async def test_assets_are_saved_one_per_step_and_missing_is_partial(self):
        self.service.queue(WORK)
        job = self.store.comment_job(WORK)
        assets = [{'kind': 'image', 'source_url': 'https://example.invalid/a', 'status': 'pending', 'error': ''},
                  {'kind': 'sticker', 'source_url': 'https://example.invalid/b', 'status': 'pending', 'error': ''}]
        self.store.comment_commit_page(WORK, job['token'], [item('111', attachments=assets)],
                                       {'cursor': 1, 'root_done': True, 'replies': []}, 1, 0)
        saved = {'relative_path': 'comment-assets/fake.png', 'sha256': 'a' * 64, 'bytes': 10}
        with patch('app.comment_assets.download_asset', new=AsyncMock(side_effect=[saved, RuntimeError('private url')])) as download:
            await self.service.step()
            self.assertEqual(download.await_count, 1)
            await self.service.step()
            self.assertEqual(download.await_count, 2)
            await self.service.step()
        state = self.store.comment_state(WORK)
        self.assertEqual((state['operation'], state['integrity']), ('finished', 'partial'))
        attached = self.store.comment_page(WORK)['items'][0]['attachments']
        self.assertEqual([a['status'] for a in attached], ['saved', 'missing'])
        self.assertNotIn('private', str(attached))

    async def test_asset_storage_failure_stops_and_leaves_attachment_pending(self):
        from app.comment_assets import AssetStorageError
        self.service.queue(WORK)
        job = self.store.comment_job(WORK)
        assets = [{'kind': 'image', 'source_url': 'https://example.invalid/a', 'status': 'pending', 'error': ''}]
        self.store.comment_commit_page(WORK, job['token'], [item('111', attachments=assets)],
                                       {'cursor': 1, 'root_done': True, 'replies': []}, 1, 0)
        with patch('app.comment_assets.download_asset', new=AsyncMock(side_effect=AssetStorageError('private'))):
            await self.service.step()
        state = self.store.comment_state(WORK)
        self.assertEqual(state['operation'], 'failed')
        self.assertNotIn('private', state['reason'])
        self.assertEqual(self.store.comment_page(WORK)['items'][0]['attachments'][0]['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
