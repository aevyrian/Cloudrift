import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.comment_channels import (AssistantCommentChannel, CommentChannelUnavailable,
                                  ServerCommentChannel, assistant_page, channel_checkpoint)
from app.comments import CommentService
from app.core import CommentBlocked, comment_item
from app.store import Store

WORK = '7431234567890123456'


def raw(cid='111', **extra):
    value = {'cid': cid, 'text': 'hello', 'create_time': 10, 'digg_count': 1,
             'reply_comment_total': 0, 'user': {'nickname': '名字'}}
    value.update(extra)
    return value


class ChannelCheckpointTests(unittest.TestCase):
    def test_old_checkpoint_defaults_to_server_and_keeps_progress(self):
        checkpoint = channel_checkpoint({'cursor': 5, 'root_done': False, 'replies': [{'id': '9', 'cursor': 0}]})
        self.assertEqual(checkpoint['channel'], 'server')
        self.assertEqual(checkpoint['cursor'], 5)
        self.assertEqual(checkpoint['replies'], [{'id': '9', 'cursor': 0}])

    def test_new_checkpoint_and_channel_switch_reset_pagination(self):
        self.assertEqual(channel_checkpoint(None),
                         {'channel': 'server', 'cursor': 0, 'root_done': False, 'replies': []})
        switched = channel_checkpoint({'channel': 'server', 'cursor': 5, 'root_done': True, 'replies': []},
                                      'assistant')
        self.assertEqual(switched,
                         {'channel': 'assistant', 'cursor': 0, 'root_done': False, 'replies': []})


class ChannelAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_channel_delegates_without_changing_page(self):
        page = {'items': [], 'next_cursor': 0, 'has_more': False, 'total': None}
        core = type('Core', (), {'comment_page': AsyncMock(return_value=page)})()
        channel = ServerCommentChannel(core)
        self.assertEqual(await channel.page(WORK, cursor=4, root_id='9'), page)
        core.comment_page.assert_awaited_once_with(WORK, cursor=4, root_id='9')

    async def test_assistant_page_uses_shared_comment_mapping(self):
        payload = {'comments': [raw('111', reply_comment_total=2), raw('222', reply_to_reply_id='111',
                                                                      reply_to_user={'nickname': 'Bob'})],
                   'cursor': 20, 'has_more': 1, 'total': 2}
        page = assistant_page(payload, 0, '')
        self.assertEqual(page['items'], [comment_item(item, '') for item in payload['comments']])
        self.assertEqual((page['next_cursor'], page['has_more'], page['total']), (20, True, 2))
        reply = assistant_page({'comments': [raw('222')], 'cursor': 0, 'has_more': 0}, 0, '111')
        self.assertEqual(reply['items'][0]['root_id'], '111')

    async def test_assistant_page_rejects_invalid_pagination(self):
        invalid = [None, {}, {'comments': None, 'cursor': 0, 'has_more': 0},
                   {'comments': [], 'cursor': -1, 'has_more': 0},
                   {'comments': [], 'cursor': 0, 'has_more': 2},
                   {'comments': [], 'cursor': 0, 'has_more': 1},
                   {'comments': [raw()], 'cursor': 0, 'has_more': 1},
                   {'comments': [raw()], 'cursor': 0, 'has_more': 0, 'total': -1}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                assistant_page(payload, 0, '')

    async def test_assistant_channel_without_connection_is_unavailable(self):
        with self.assertRaises(CommentChannelUnavailable):
            await AssistantCommentChannel().page(WORK)
        fetch = AsyncMock(return_value={'comments': [raw()], 'cursor': 0, 'has_more': 0, 'total': 1})
        page = await AssistantCommentChannel(fetch).page(WORK, cursor=0)
        self.assertEqual(page['items'][0]['id'], '111')
        fetch.assert_awaited_once_with(WORK, cursor=0, root_id=None)


class ChannelStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_queue_records_channel_and_rejects_unknown(self):
        self.assertEqual(self.store.comment_queue(WORK, 'save')['checkpoint'],
                         {'channel': 'server', 'cursor': 0, 'root_done': False, 'replies': []})
        self.store.comment_cancel(WORK)
        self.assertEqual(self.store.comment_queue(WORK, 'save', 'assistant')['checkpoint']['channel'], 'assistant')
        with self.assertRaises(ValueError):
            self.store.comment_queue(WORK, 'save', 'other')

    def test_continue_preserves_legacy_and_assistant_checkpoints(self):
        self.store.comment_queue(WORK, 'save', 'assistant')
        self.store.comment_cancel(WORK)
        self.assertEqual(self.store.comment_queue(WORK, 'continue')['checkpoint']['channel'], 'assistant')

        with self.store.connect() as db:
            db.execute("UPDATE comment_jobs SET operation='stopped',checkpoint=? WHERE work_id=?",
                       (json.dumps({'cursor': 5, 'root_done': False, 'replies': []}), WORK))
        checkpoint = self.store.comment_queue(WORK, 'continue')['checkpoint']
        self.assertEqual((checkpoint['channel'], checkpoint['cursor']), ('server', 5))

    def test_both_channels_share_comment_table_and_id_dedup(self):
        token = self.store.comment_queue(WORK, 'save')['token']
        first = comment_item(raw('111'), '')
        self.assertTrue(self.store.comment_commit_page(WORK, token, [first],
                                                       {'channel': 'server', 'cursor': 1, 'root_done': True, 'replies': []},
                                                       None, 0))
        self.store.comment_cancel(WORK)
        token = self.store.comment_queue(WORK, 'save', 'assistant')['token']
        updated = comment_item(raw('111', text='updated'), '')
        self.assertTrue(self.store.comment_commit_page(WORK, token, [updated],
                                                       {'channel': 'assistant', 'cursor': 1, 'root_done': True, 'replies': []},
                                                       None, 0))
        state = self.store.comment_state(WORK)
        self.assertEqual((state['total'], state['replies']), (1, 0))
        self.assertEqual(self.store.comment_job(WORK)['new_comments'], 0)
        self.assertEqual(self.store.comment_page(WORK)['items'][0]['text'], 'updated')


class FakeCore:
    ready = True

    def __init__(self):
        self.calls = []

    async def comment_page(self, work_id, cursor=0, root_id=None):
        self.calls.append((work_id, cursor, root_id))
        return {'items': [], 'next_cursor': 0, 'has_more': False, 'total': None}


class ChannelServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.core = FakeCore()

    def service(self, fetch):
        return CommentService(self.store, self.core, {
            'server': ServerCommentChannel(self.core),
            'assistant': AssistantCommentChannel(fetch),
        })

    async def test_assistant_checkpoint_selects_assistant_channel_and_saves_page(self):
        fetch = AsyncMock(return_value={'comments': [raw('111')], 'cursor': 0, 'has_more': 0, 'total': 1})
        service = self.service(fetch)
        service.queue(WORK, 'save', 'assistant')
        self.assertTrue(await service.step())
        self.assertEqual(self.core.calls, [])
        fetch.assert_awaited_once_with(WORK, cursor=0, root_id=None)
        job = self.store.comment_job(WORK)
        self.assertEqual(job['checkpoint']['channel'], 'assistant')
        self.assertEqual(self.store.comment_state(WORK)['total'], 1)
        self.assertTrue(await service.step())
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'finished')

    async def test_missing_assistant_connection_fails_safely_without_pausing_queue(self):
        service = self.service(None)
        service.queue(WORK, 'save', 'assistant')
        self.assertTrue(await service.step())
        state = self.store.comment_state(WORK)
        self.assertEqual(state['operation'], 'failed')
        self.assertFalse(state['queue_paused'])
        self.assertIn('通道', state['reason'])
        self.assertEqual(self.core.calls, [])

    async def test_assistant_blocked_uses_shared_pause_semantics(self):
        async def blocked(*args, **kwargs):
            raise CommentBlocked('private')
        service = self.service(blocked)
        service.queue(WORK, 'save', 'assistant')
        self.assertTrue(await service.step())
        state = self.store.comment_state(WORK)
        self.assertEqual(state['operation'], 'failed')
        self.assertTrue(state['queue_paused'])
        self.assertNotIn('private', state['reason'])

    async def test_default_server_checkpoint_still_uses_core_channel(self):
        service = self.service(None)
        service.queue(WORK, 'save')
        self.assertTrue(await service.step())
        self.assertEqual(self.core.calls, [(WORK, 0, None)])


if __name__ == '__main__':
    unittest.main()
