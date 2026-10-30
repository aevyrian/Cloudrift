import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from app.core import Core

WORK = '1234567890123456789'
ROOT = '1234567890123456788'
REPLY = '1234567890123456787'


class CoreCommentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = str(Path('vendor/TikTokDownloader').resolve())
        sys.path.insert(0, source)
        self.addCleanup(sys.path.remove, source)
        from src.custom import IMPERSONATE, USERAGENT
        from src.encrypt import DouYinParams
        from src.interface import template
        self.wait = AsyncMock(side_effect=AssertionError('comment requests must not use download waits'))
        wait_patch = patch.object(template, 'wait', self.wait)
        wait_patch.start()
        self.addCleanup(wait_patch.stop)
        logger = SimpleNamespace(info=lambda *a, **kw: None, error=lambda *a, **kw: None,
                                 warning=lambda *a, **kw: None)
        self.client = SimpleNamespace(get=AsyncMock())
        params = SimpleNamespace(headers={'Cookie': 'sessionid=private', 'Referer': 'https://www.douyin.com/'},
                                 logger=logger, console=logger, douyin_params=DouYinParams(), max_retry=0,
                                 max_pages=10, timeout=5, client=self.client, impersonate=IMPERSONATE,
                                 user_agent=USERAGENT)
        self.core = Core(source, '.', '.')
        self.core.ready = True
        self.core.app = SimpleNamespace(parameter=params)
        self.payload = {'status_code': 0, 'comments': [], 'cursor': 0, 'has_more': 0}

        def json_body():
            if isinstance(self.payload, Exception):
                raise self.payload
            return self.payload

        self.response = SimpleNamespace(status_code=200, content=b'json', text='{}', json=json_body, headers={})
        self.client.get.return_value = self.response

    def comment(self, cid=ROOT, **extra):
        item = {'cid': cid, 'text': 'hello', 'create_time': 1700000000, 'digg_count': 4,
                'reply_comment_total': 0, 'user': {'nickname': 'Alice'}}
        return item | extra

    async def test_root_page_one_request_and_image_extraction(self):
        self.payload = {'status_code': 0, 'comments': [self.comment(
            reply_comment_total=2,
            image_list=[{'origin_url': {'url_list': ['https://p1.douyinpic.com/a']}},
                        {'origin_url': {'url_list': ['https://p1.douyinpic.com/b']}}],
            sticker={'static_url': {'url_list': ['https://p1.douyinpic.com/c']}})],
            'cursor': 20, 'has_more': 1, 'total': 9}
        page = await self.core.comment_page(WORK)
        self.assertEqual(page['next_cursor'], 20)
        self.assertTrue(page['has_more'])
        self.assertEqual(page['total'], 9)
        item = page['items'][0]
        self.assertEqual((item['id'], item['root_id'], item['nickname'], item['reply_count']), (ROOT, '', 'Alice', 2))
        self.assertEqual([a['kind'] for a in item['attachments']], ['image', 'image', 'sticker'])
        self.assertEqual([a['status'] for a in item['attachments']], ['pending'] * 3)
        self.client.get.assert_awaited_once()
        args, kwargs = self.client.get.await_args
        self.assertEqual(urlsplit(args[0]).path, '/aweme/v1/web/comment/list/')
        self.assertEqual(parse_qs(urlsplit(args[0]).query)['aweme_id'], [WORK])
        self.assertEqual(kwargs['headers']['Cookie'], 'sessionid=private')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertLessEqual(kwargs['timeout'], 15)
        self.wait.assert_not_awaited()

    async def test_reply_page_preserves_parent_and_reply_target(self):
        self.payload = {'status_code': 0, 'comments': [self.comment(REPLY,
            reply_to_reply_id=ROOT, reply_to_user={'nickname': 'Bob'})], 'cursor': 3, 'has_more': 0}
        result = await self.core.comment_page(WORK, cursor=3, root_id=ROOT)
        item = result['items'][0]
        self.assertEqual((item['id'], item['root_id'], item['reply_to_id'], item['reply_to_user']),
                         (REPLY, ROOT, ROOT, 'Bob'))
        args, _ = self.client.get.await_args
        self.assertEqual(urlsplit(args[0]).path, '/aweme/v1/web/comment/list/reply/')
        self.assertEqual(parse_qs(urlsplit(args[0]).query)['comment_id'], [ROOT])

    async def test_reply_target_uid_is_retained_when_supplied(self):
        self.payload = {'status_code': 0, 'comments': [self.comment(REPLY,
            reply_to_reply_id=ROOT, reply_to_user={'nickname': 'Bob', 'uid': '12345'})], 'cursor': 0, 'has_more': 0}
        result = await self.core.comment_page(WORK, root_id=ROOT)
        self.assertIn('Bob', result['items'][0]['reply_to_user'])
        self.assertIn('12345', result['items'][0]['reply_to_user'])

    async def test_empty_is_only_success_with_explicit_terminal_fields(self):
        result = await self.core.comment_page(WORK)
        self.assertEqual(result, {'items': [], 'next_cursor': 0, 'has_more': False, 'total': None})
        invalid = [None, {}, {'status_code': 0, 'comments': [], 'cursor': 0},
                   {'status_code': 0, 'comments': None, 'cursor': 0, 'has_more': 0},
                   {'status_code': 0, 'comments': [], 'cursor': 1, 'has_more': 1},
                   {'status_code': 0, 'comments': [self.comment()], 'cursor': 0, 'has_more': 1},
                   {'status_code': 0, 'comments': [self.comment()], 'cursor': 1, 'has_more': 2},
                   {'status_code': 1, 'comments': [], 'cursor': 0, 'has_more': 0},
                   ValueError('private raw response')]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.payload = payload
                with self.assertRaises(RuntimeError) as raised:
                    await self.core.comment_page(WORK)
                self.assertNotIn('private', str(raised.exception))

    async def test_invalid_comment_rejects_entire_page_and_unsupported_attachment_is_visible(self):
        for item in [self.comment(cid='not-id'), self.comment(create_time='bad'), self.comment(digg_count=-1),
                     self.comment(reply_comment_total=-1), self.comment(user=1), self.comment(user={'nickname': 0})]:
            self.payload = {'status_code': 0, 'comments': [item], 'cursor': 1, 'has_more': 0}
            with self.assertRaises(RuntimeError):
                await self.core.comment_page(WORK)
        self.payload = {'status_code': 0, 'comments': [self.comment(image_list=[{}])], 'cursor': 1, 'has_more': 0}
        result = await self.core.comment_page(WORK)
        self.assertEqual(result['items'][0]['attachments'][0]['status'], 'unsupported')
        self.payload = {'status_code': 0, 'comments': [self.comment(image_list=[{'origin_url': {
            'url_list': ['file:///bad', 'https://p1.douyinpic.com/valid']}}])], 'cursor': 1, 'has_more': 0}
        result = await self.core.comment_page(WORK)
        self.assertEqual(result['items'][0]['attachments'][0]['source_url'], 'https://p1.douyinpic.com/valid')

    async def test_blocked_and_network_errors_are_safe(self):
        from app.core import CommentBlocked
        for status in (401, 403, 429):
            self.response.status_code = status
            with self.assertRaises(CommentBlocked):
                await self.core.comment_page(WORK)
        self.response.status_code = 200
        self.payload = {'status_code': 7, 'status_msg': '请登录后验证 private'}
        with self.assertRaises(CommentBlocked) as raised:
            await self.core.comment_page(WORK)
        self.assertNotIn('private', str(raised.exception))
        self.payload = ValueError('not json')
        self.response.text = '请完成验证'
        with self.assertRaises(CommentBlocked):
            await self.core.comment_page(WORK)
        self.client.get.side_effect = RuntimeError('private transport URL')
        with self.assertRaises(RuntimeError) as raised:
            await self.core.comment_page(WORK)
        self.assertNotIn('private', str(raised.exception))

    async def test_invalid_input_does_not_request_platform(self):
        for work, cursor, root in [('legacy-x', 0, None), (WORK, -1, None), (WORK, True, None), (WORK, 0, '../x')]:
            with self.assertRaises((ValueError, RuntimeError)):
                await self.core.comment_page(work, cursor, root)
        self.client.get.assert_not_awaited()

    async def test_missing_cookie_stops_without_contacting_platform(self):
        from app.core import CommentBlocked
        self.core.app.parameter.headers.pop('Cookie')
        with self.assertRaises(CommentBlocked):
            await self.core.comment_page(WORK)
        self.client.get.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
