import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

from app.core import Core


SEC_UID = 'MS4wLjABAAAA_test_author'
COOKIE = 'sessionid=test-secret'


class CoreCountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.core = Core(Path('.'), Path('.'), Path('.'))
        self.clients = []
        self.users = []
        self.result = {'sec_uid': SEC_UID, 'aweme_count': 70}
        self.error = None
        owner = self

        class Session:
            def __init__(self, **kwargs):
                self.options = kwargs
                self.calls = 0
                self.closed = False
                owner.clients.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def get(self):
                self.calls += 1
                if owner.error:
                    raise owner.error
                return owner.result

        class User:
            def __init__(self, params, **kwargs):
                self.params, self.options = params, kwargs
                owner.users.append(self)

            async def run(self):
                return await self.params.client.get()

        def module(name, **values):
            value = ModuleType(name)
            value.__dict__.update(values)
            return value

        modules = {
            'curl_cffi.requests': module('curl_cffi.requests', AsyncSession=Session),
            'src.custom': module('src.custom', DATA_HEADERS={'User-Agent': 'test-agent'}, IMPERSONATE='chrome', USERAGENT='test-agent'),
            'src.encrypt': module('src.encrypt', DouYinParams=type('DouYinParams', (), {})),
            'src.interface.user': module('src.interface.user', User=User),
        }
        self.modules = patch.dict('sys.modules', modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def test_independent_single_request_and_session_close(self):
        self.assertEqual(await self.core.author_count(SEC_UID, COOKIE), 70)
        self.assertEqual(len(self.clients), 1)
        client = self.clients[0]
        self.assertTrue(client.closed)
        self.assertEqual(client.calls, 1)
        self.assertEqual(client.options['timeout'], 8)
        self.assertFalse(client.options['allow_redirects'])
        user = self.users[0]
        self.assertEqual(user.options, {'cookie': COOKIE, 'sec_user_id': SEC_UID})
        self.assertIs(user.params.client, client)
        self.assertEqual(user.params.max_retry, 0)
        self.assertEqual(user.params.user_agent, 'test-agent')
        self.assertEqual(user.params.impersonate, 'chrome')
        user.params.logger.warning('sensitive example')

    async def test_zero_and_invalid_responses(self):
        self.result['aweme_count'] = 0
        self.assertEqual(await self.core.author_count(SEC_UID, COOKIE), 0)
        for result in [None, {}, {'sec_uid': 'other', 'aweme_count': 1},
                       *({'sec_uid': SEC_UID, 'aweme_count': value} for value in [None, True, -1, 1.5, '7', 2**53])]:
            with self.subTest(result=result):
                self.result = result
                with self.assertRaises(RuntimeError):
                    await self.core.author_count(SEC_UID, COOKIE)
                self.assertTrue(self.clients[-1].closed)

    async def test_errors_do_not_expose_credentials(self):
        self.error = RuntimeError('secret-in-exception')
        with self.assertRaises(RuntimeError) as raised:
            await self.core.author_count(SEC_UID, COOKIE)
        self.assertNotIn('secret-in-exception', str(raised.exception))
        self.assertNotIn(COOKIE, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertTrue(self.clients[-1].closed)


class VendorCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_vendor_user_makes_one_request(self):
        source = str(Path('vendor/TikTokDownloader').resolve())
        sys.path.insert(0, source)
        self.addCleanup(sys.path.remove, source)
        from src.interface import template

        class Response:
            url = 'https://www.douyin.com/aweme/v1/web/user/profile/other/'
            status_code = 200
            headers = {}

            def raise_for_status(self):
                pass

            def json(self):
                return {'user': {'sec_uid': SEC_UID, 'aweme_count': 70}}

        class Session:
            instances = []

            def __init__(self, **kwargs):
                self.calls = []
                self.closed = False
                self.instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        core = Core(source, '.', '.')
        with patch('curl_cffi.requests.AsyncSession', Session), patch.object(template, 'wait', AsyncMock()):
            self.assertEqual(await core.author_count(SEC_UID, COOKIE), 70)
        self.assertEqual(len(Session.instances), 1)
        self.assertTrue(Session.instances[0].closed)
        self.assertEqual(len(Session.instances[0].calls), 1)
        url, options = Session.instances[0].calls[0]
        self.assertTrue(url.startswith(Response.url + '?'))
        self.assertEqual(options['headers']['Cookie'], COOKIE)


if __name__ == '__main__':
    unittest.main()
