import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from app.core import Core


SEC_UID = 'MS4wLjABAAAA_test_author'


class CoreCountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = str(Path('vendor/TikTokDownloader').resolve())
        sys.path.insert(0, source)
        self.addCleanup(sys.path.remove, source)
        from src.interface import template
        from src.custom import DATA_HEADERS, PARAMS_HEADERS
        self.core = Core(source, '.', '.')
        self.clients = []
        self.result = {'user': {'sec_uid': SEC_UID, 'aweme_count': 70}}
        self.error = None
        self.failure = None
        owner = self

        class Response:
            def __init__(self, stage):
                self.stage = stage
                self.status_code = 403 if owner.failure == stage + '_http' else 200
                self.headers = {'Set-Cookie': '' if owner.failure == 'missing_ttwid' else 'ttwid=visitor-only; Path=/; Secure; HttpOnly'}
                self.content = b'' if owner.failure == stage + '_empty' else b'body'
                self.text = '请完成验证' if owner.failure == stage + '_captcha' else 'body'

            def raise_for_status(self):
                if owner.error or self.status_code != 200:
                    raise owner.error or RuntimeError('http error')

            def json(self):
                return owner.result

        class Session:
            def __init__(self, **kwargs):
                self.options, self.calls, self.closed = kwargs, [], False
                self.cookies = SimpleNamespace(jar=[], set=self.set_cookie)
                owner.clients.append(self)

            def set_cookie(self, name, value, domain):
                self.cookies.jar.append(SimpleNamespace(name=name, value=value, domain=domain))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def get(self, url, **kwargs):
                self.calls.append(('get', url, kwargs))
                if owner.error:
                    raise owner.error
                stage = 'api' if '/aweme/' in url else 'home'
                if stage == 'home':
                    self.cookies.jar.append(SimpleNamespace(name='msToken', value='visitor-ms'))
                return Response(stage)

            async def post(self, url, **kwargs):
                self.calls.append(('post', url, kwargs))
                if owner.error:
                    raise owner.error
                return Response('register')

        self.wait = AsyncMock(side_effect=AssertionError('下载等待不应用于数量查询'))
        for patcher in (
            patch('curl_cffi.requests.AsyncSession', Session),
            patch.object(template, 'wait', self.wait),
            patch.dict(template.API.params, {'msToken': 'private-ms', 'uifid': 'private-uid', 'webid': 'private-web'}),
            patch.dict(DATA_HEADERS, {'Cookie': 'private-cookie', 'Authorization': 'private-auth', 'uifid': 'private-header', 'X-Identity': 'private-extra'}),
            patch.dict(PARAMS_HEADERS, {'Cookie': 'private-cookie', 'Authorization': 'private-auth', 'uifid': 'private-header', 'X-Identity': 'private-extra'}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_independent_anonymous_request_has_no_login_identity_or_download_delay(self):
        from src.interface.template import API
        self.assertEqual(await self.core.author_count(SEC_UID), 70)
        self.assertEqual(len(self.clients), 1)
        client = self.clients[0]
        self.assertTrue(client.closed)
        self.assertEqual(client.options['timeout'], 5)
        self.assertFalse(client.options['allow_redirects'])
        self.assertEqual([item[0] for item in client.calls], ['post', 'get', 'get'])
        _, registration, register_options = client.calls[0]
        self.assertEqual(urlsplit(registration).hostname, 'ttwid.bytedance.com')
        from src.encrypt import TtWid
        self.assertEqual(register_options['data'], TtWid.DATA)
        self.assertEqual(urlsplit(client.calls[1][1]).path, '/')
        _, url, options = client.calls[2]
        self.assertEqual(urlsplit(url).path, '/aweme/v1/web/user/profile/other/')
        self.assertEqual(parse_qs(urlsplit(url).query)['sec_user_id'], [SEC_UID])
        self.assertEqual(parse_qs(urlsplit(url).query)['msToken'], ['visitor-ms'])
        self.assertEqual([(c.name, c.value) for c in client.cookies.jar], [('ttwid', 'visitor-only'), ('msToken', 'visitor-ms')])
        self.assertNotIn('private-', url)
        for _, request_url, request_options in client.calls:
            self.assertNotIn('private-', request_url)
            self.assertFalse(any(key.lower() in {'cookie', 'authorization', 'uifid'} for key in request_options['headers']))
            self.assertNotIn('private-', str(request_options['headers']))
        self.assertEqual(API.params['msToken'], 'private-ms', '匿名查询不修改下载核心共享配置')
        self.assertEqual(API.params['uifid'], 'private-uid')
        self.wait.assert_not_awaited()

    async def test_zero_and_invalid_responses(self):
        self.result['user']['aweme_count'] = 0
        self.assertEqual(await self.core.author_count(SEC_UID), 0)
        for result in [None, {}, {'user': {}}, {'user': {'sec_uid': 'other', 'aweme_count': 1}},
                       *({'user': {'sec_uid': SEC_UID, 'aweme_count': value}} for value in [None, True, -1, 1.5, '7', 2**53])]:
            with self.subTest(result=result):
                self.result = result
                with self.assertRaises(RuntimeError):
                    await self.core.author_count(SEC_UID)
                self.assertTrue(self.clients[-1].closed)

    async def test_http_errors_are_not_parsed_or_exposed(self):
        self.error = RuntimeError('private-error')
        with self.assertRaises(RuntimeError) as raised:
            await self.core.author_count(SEC_UID)
        self.assertNotIn('private-', str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertTrue(self.clients[-1].closed)

    async def test_initialization_and_profile_fail_closed_without_retry(self):
        for failure, expected_calls in [('register_http', 1), ('register_empty', 1), ('register_captcha', 1), ('missing_ttwid', 1),
                                        ('home_http', 2), ('home_empty', 2), ('home_captcha', 2),
                                        ('api_http', 3), ('api_empty', 3), ('api_captcha', 3)]:
            with self.subTest(failure=failure):
                self.failure = failure
                with self.assertRaises(RuntimeError):
                    await self.core.author_count(SEC_UID)
                self.assertEqual(len(self.clients[-1].calls), expected_calls)
                self.assertTrue(self.clients[-1].closed)


if __name__ == '__main__':
    unittest.main()
