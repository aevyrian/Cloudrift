import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.comments import CommentService
from app.core import CommentBlocked
from app.store import Store
from test_comments_store import WORK, item
from test_comment_fixtures import COOKIE, setup_comment_accounts
from app.comment_accounts import AccountError


class FakeCore:
    ready = True
    def __init__(self):
        self.calls = []
        self.responses = []
        self.app = SimpleNamespace(parameter=SimpleNamespace(headers={'Cookie': COOKIE}))
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
        setup_comment_accounts(self.store)
        self.core = FakeCore()
        self.service = CommentService(self.store, self.core)

    def queue(self, work_id, action='save'):
        return self.service.queue(work_id, action, account_role='primary', allow_primary=True)

    async def test_only_manual_queue_and_media_cooldown_have_priority(self):
        self.assertFalse(await self.service.step())
        self.assertEqual(self.core.calls, [])
        self.queue(WORK)
        self.store.set_setting('queue_resume_at', 9999999999)
        self.assertFalse(await self.service.step())
        self.store.set_setting('queue_resume_at', 0)
        self.store.create_task(WORK, 'detail')
        self.assertFalse(await self.service.step())
        self.assertEqual(self.core.calls, [])

    async def test_core_requires_matching_unambiguous_cookie_identity(self):
        for cookie in ('', 'sessionid=synthetic-session',
                       'uid_tt=synthetic-primary; uid_tt=other; sessionid=synthetic-session',
                       'uid_tt=synthetic-primary; uid_tt_ss=other; sessionid=synthetic-session',
                       'uid_tt=other; sessionid=synthetic-session'):
            with self.subTest(cookie=cookie):
                self.store.set_setting('cookie', cookie)
                with self.assertRaises(AccountError):
                    self.queue(WORK)
                self.assertFalse(await self.service.step())
                self.assertEqual(self.core.calls, [])
        self.store.set_setting('cookie', 'uid_tt=synthetic-primary; uid_tt_ss=synthetic-primary; sessionid=synthetic-session')
        self.store.comment_accounts.resume(True)
        self.core.responses = [{'items': [], 'next_cursor': 0, 'has_more': False, 'total': 0}]
        self.queue(WORK)
        self.assertTrue(await self.service.step())
        self.assertEqual(len(self.core.calls), 1)

    async def test_account_pause_stops_queued_core_without_request(self):
        self.queue(WORK)
        self.store.comment_accounts.pause()
        self.assertFalse(await self.service.step())
        self.assertEqual(self.core.calls, [])
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'interrupted')
        with self.assertRaises(AccountError):
            self.queue(WORK, 'continue')

    async def test_running_core_identity_mismatch_before_request_stops(self):
        self.queue(WORK)
        self.core.app.parameter.headers['Cookie'] = 'uid_tt=other; sessionid=synthetic-session'
        self.assertTrue(await self.service.step())
        self.assertEqual(self.core.calls, [])
        self.assertTrue(self.store.comment_accounts.status()['paused'])
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'interrupted')

    async def test_running_core_identity_change_after_request_discards_page(self):
        self.queue(WORK)
        async def changed(*args, **kwargs):
            self.core.app.parameter.headers['Cookie'] = 'uid_tt=other; sessionid=synthetic-session'
            return {'items': [item('111')], 'next_cursor': 0, 'has_more': False, 'total': 1}
        self.core.comment_page = changed
        self.assertTrue(await self.service.step())
        self.assertEqual(self.store.comment_state(WORK)['total'], 0)
        self.assertTrue(self.store.comment_accounts.status()['paused'])

    async def test_main_reply_traversal_three_second_gap_and_completion(self):
        main = {**item('111'), 'reply_count': 1}
        self.core.responses = [{'items': [main], 'next_cursor': 1, 'has_more': False, 'total': 1},
                               {'items': [item('222', '111', '111')], 'next_cursor': 1, 'has_more': False, 'total': 1}]
        self.queue(WORK)
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
        self.queue(WORK)
        self.assertTrue(await self.service.step())
        state = self.store.comment_state(WORK)
        self.assertEqual(state['operation'], 'interrupted')
        self.assertTrue(self.store.comment_accounts.status()['paused'])
        self.assertNotIn('private', state['reason'])
        with self.assertRaises(AccountError):
            self.queue(WORK, 'continue')
        self.store.comment_accounts.resume(True)
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'interrupted')
        self.queue(WORK, 'continue')
        self.assertFalse(self.store.comment_accounts.status()['paused'])

    async def test_stop_during_request_preserves_data_and_status(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            return {'items': [item('111')], 'next_cursor': 0, 'has_more': False, 'total': 1}
        self.core.comment_page = delayed
        self.queue(WORK)
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
        self.queue(WORK)
        running = asyncio.create_task(self.service.step())
        await started.wait()
        self.service.cancel(WORK)
        self.queue(WORK, 'continue')
        release.set()
        await running
        self.assertEqual((self.store.comment_state(WORK)['operation'], self.store.comment_state(WORK)['total']), ('queued', 0))

    async def test_cancel_during_platform_block_pauses_queue_without_overwriting_stopped(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            raise CommentBlocked('private')
        self.core.comment_page = delayed
        self.queue(WORK)
        running = asyncio.create_task(self.service.step())
        await started.wait()
        self.service.cancel(WORK)
        release.set()
        await running
        state = self.store.comment_state(WORK)
        self.assertEqual(state['operation'], 'stopped')
        self.assertTrue(self.store.comment_accounts.status()['paused'])
        self.assertFalse(await self.service.step())

    async def test_stale_block_after_new_operation_pauses_all_comments(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            raise CommentBlocked('private')
        self.core.comment_page = delayed
        self.queue(WORK)
        running = asyncio.create_task(self.service.step())
        await started.wait()
        self.service.cancel(WORK)
        self.queue(WORK, 'continue')
        release.set()
        await running
        state = self.store.comment_state(WORK)
        self.assertEqual(state['operation'], 'interrupted')
        self.assertTrue(self.store.comment_accounts.status()['paused'])

    async def test_repeated_cursor_is_failure_and_does_not_advance(self):
        self.core.responses = [{'items': [item('111')], 'next_cursor': 0, 'has_more': True, 'total': 1}]
        self.queue(WORK)
        await self.service.step()
        state = self.store.comment_state(WORK)
        self.assertEqual((state['operation'], state['total']), ('interrupted', 0))
        self.assertTrue(self.store.comment_accounts.status()['paused'])
        self.assertEqual(self.store.comment_job(WORK)['checkpoint']['cursor'], 0)

    async def test_limits_stop_before_request_and_recover_only_running(self):
        self.queue(WORK)
        with self.store.connect() as db:
            db.execute('UPDATE comment_jobs SET requests=200 WHERE work_id=?', (WORK,))
        self.assertTrue(await self.service.step())
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'stopped')
        self.assertEqual(self.core.calls, [])
        self.queue(WORK, 'continue')
        self.service.recover()
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'queued')
        self.store.comment_claim()
        self.service.recover()
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'interrupted')

    async def test_assets_are_saved_one_per_step_and_missing_is_partial(self):
        self.queue(WORK)
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
        self.queue(WORK)
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

    async def test_refresh_failure_keeps_offline_image_and_export_then_continue_replaces_it(self):
        import json
        import zipfile
        from app.comment_assets import asset_path, export_snapshot
        asset_dir = self.store.root / 'comment-assets'
        asset_dir.mkdir()
        old_file = asset_dir / ('a' * 64 + '.png')
        new_file = asset_dir / ('b' * 64 + '.png')
        old_file.write_bytes(b'old-image')
        new_file.write_bytes(b'new-image')
        self.queue(WORK)
        job = self.store.comment_claim()
        attachment = {'kind': 'image', 'source_url': 'https://example.invalid/a', 'status': 'pending', 'error': ''}
        checkpoint = {'cursor': 0, 'root_done': True, 'replies': []}
        self.store.comment_commit_page(WORK, job['token'], [item('111', attachments=[attachment])], checkpoint, 1, 0)
        asset = self.store.comment_next_asset(WORK)
        old = {'relative_path': 'comment-assets/' + old_file.name, 'sha256': 'a' * 64, 'bytes': 9}
        self.store.comment_commit_asset(WORK, job['token'], asset['id'], old, 0)
        self.store.comment_end(WORK, job['token'], 'finished', '', finished=True)
        self.queue(WORK, 'update')
        job = self.store.comment_claim()
        changed = {**attachment, 'source_url': 'https://example.invalid/b'}
        self.store.comment_commit_page(WORK, job['token'], [item('111', attachments=[changed])], checkpoint, 1, 0)
        self.assertEqual(asset_path(self.store.root, self.store.comment_asset(asset['id'])).read_bytes(), b'old-image')
        with patch('app.comment_assets.download_asset', new=AsyncMock(side_effect=RuntimeError('expired secret'))):
            await self.service.step()
            await self.service.step()
        status = self.store.comment_state(WORK)
        self.assertEqual(status['integrity'], 'partial')
        self.assertIn('保留', status['reason'])
        self.assertEqual(asset_path(self.store.root, self.store.comment_asset(asset['id'])).read_bytes(), b'old-image')
        snapshot = self.store.comment_snapshot(WORK)
        exported, _, _ = export_snapshot(snapshot, self.store.root, 'zip')
        with zipfile.ZipFile(exported) as archive:
            self.assertEqual(archive.read('assets/' + old_file.name), b'old-image')
            self.assertEqual(json.loads(archive.read('comments.json'))['status']['integrity'], 'partial')
        exported.unlink()
        self.queue(WORK, 'continue')
        new = {'relative_path': 'comment-assets/' + new_file.name, 'sha256': 'b' * 64, 'bytes': 9}
        with patch('app.comment_assets.download_asset', new=AsyncMock(return_value=new)):
            await self.service.step()
        self.assertEqual(asset_path(self.store.root, self.store.comment_asset(asset['id'])).read_bytes(), b'new-image')
        self.assertEqual(self.store.comment_asset(asset['id'])['error'], '')


if __name__ == '__main__':
    unittest.main()
