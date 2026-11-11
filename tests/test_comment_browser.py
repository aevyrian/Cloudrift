import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.comment_browser import BrowserCommentService, BrowserError
from app.comments import CommentService
from app.main import make_app
from app.store import Store
from test_comment_fixtures import CLIENT, setup_comment_accounts


WORK = '7431234567890123456'


def comment(cid, replies=0):
    return {'cid': cid, 'user': {'nickname': '测试'}, 'text': '评论', 'create_time': 10,
            'digg_count': 1, 'reply_comment_total': replies}


def payload(items, cursor=0, more=0):
    return {'status_code': 0, 'comments': items, 'has_more': more, 'cursor': cursor}


class BrowserCommentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.identity = setup_comment_accounts(self.store)['secondary']
        self.service = BrowserCommentService(self.store)

    def start(self, action='save'):
        session = self.service.start(WORK, action, 'secondary')
        self.service.claim(WORK, session['token'], self.identity, CLIENT)
        return session

    def permit(self, token, root='', cursor=0):
        return self.service.permit(WORK, token, CLIENT, self.identity, root, cursor)

    def page(self, token, root, cursor, data, permit):
        return self.service.page(WORK, token, CLIENT, self.identity, root, cursor, data, permit)

    def test_traversal_checkpoint_and_finish(self):
        with patch('app.comment_accounts.time.time', return_value=100) as now:
            session = self.start()
            token = session['token']
            self.assertRegex(token, r'^[a-f0-9]{32}$')
            self.assertEqual(session['next'], {'root_id': '', 'cursor': 0})
            with self.assertRaises(BrowserError):
                self.service.finish(WORK, token, CLIENT, self.identity, 'complete')
            permit = self.permit(token)['permit']
            session = self.page(token, '', 0, payload([comment('111', 1)], 1), permit)
            self.assertEqual(session['next'], {'root_id': '111', 'cursor': 0})
            self.assertFalse(self.permit(token, '111')['ready'])
            now.return_value = 103
            permit = self.permit(token, '111')['permit']
            session = self.page(token, '111', 0, payload([comment('222')]), permit)
            self.assertIsNone(session['next'])
            state = self.service.finish(WORK, token, CLIENT, self.identity, 'complete')
        self.assertEqual((state['operation'], state['channel'], state['total'], state['replies']),
                         ('queued', 'browser', 2, 1))
        self.assertTrue(self.store.comment_job(WORK)['checkpoint']['root_done'])

    def test_invalid_page_and_stale_session_never_commit(self):
        token = self.start()['token']
        permit = self.permit(token)['permit']
        for root, cursor, data in [('', 1, payload([comment('111')])),
                                   ('', 0, {}),
                                   ('', 0, payload([comment('111')], 0, 1)),
                                   ('', 0, payload([comment(str(i + 1)) for i in range(101)]))]:
            with self.subTest(root=root, cursor=cursor, data_len=len(data.get('comments', []))):
                with self.assertRaises(BrowserError):
                    self.page(token, root, cursor, data, permit)
        self.assertEqual(self.store.comment_state(WORK)['total'], 0)
        self.store.comment_cancel(WORK)
        with self.assertRaises(BrowserError):
            self.page(token, '', 0, payload([comment('111')]), permit)
        self.assertEqual(self.store.comment_state(WORK)['total'], 0)

    def test_conflict_recovery_and_resume(self):
        token = self.start()['token']
        self.page(token, '', 0, payload([comment('111')], 1, 1), self.permit(token)['permit'])
        with self.assertRaises(BrowserError) as error:
            self.service.start(WORK, 'save', 'secondary')
        self.assertEqual(error.exception.status_code, 409)
        self.store.comment_recover()
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'interrupted')
        with self.assertRaises(BrowserError):
            self.service.get(WORK, token, CLIENT, self.identity)
        resumed = self.start('continue')
        self.assertNotEqual(token, resumed['token'])
        self.assertEqual(resumed['next'], {'root_id': '', 'cursor': 1})
        self.assertEqual(self.store.comment_state(WORK)['total'], 1)

    def test_work_id_mismatch_never_commits(self):
        token = self.start()['token']
        permit = self.permit(token)['permit']
        wrong = comment('111') | {'aweme_id': '7431234567890123457'}
        with self.assertRaises(BrowserError):
            self.page(token, '', 0, payload([wrong]), permit)
        self.assertEqual(self.store.comment_state(WORK)['total'], 0)
        right = comment('111') | {'aweme_id': int(WORK)}
        self.page(token, '', 0, payload([right]), permit)
        self.assertEqual(self.store.comment_state(WORK)['total'], 1)

    def test_cancel_clears_session_and_core_queue_resets_channel(self):
        token = self.start()['token']
        self.service.cancel(WORK)
        self.assertNotIn(token, self.service.sessions)
        self.assertEqual(self.store.comment_queue(WORK, 'continue', account_role='primary', allow_primary=True)['channel'], 'core')


class BrowserCommentAssetHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_pages_and_reply_then_asset_worker_finishes_without_core_request(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            identity = setup_comment_accounts(store)['secondary']
            browser = BrowserCommentService(store)
            core = AsyncMock()
            worker = CommentService(store, core)
            with patch('app.comment_accounts.time.time', return_value=100) as now:
                token = browser.start(WORK, 'save', 'secondary')['token']
                browser.claim(WORK, token, identity, CLIENT)
                first = comment('111', 2) | {'image_list': [{'origin_url': {'url_list': ['https://p1.douyinpic.com/a']}}]}
                permit = browser.permit(WORK, token, CLIENT, identity, '', 0)['permit']
                result = browser.page(WORK, token, CLIENT, identity, '', 0, payload([first], 1, 1), permit)
                self.assertEqual(result['next'], {'root_id': '111', 'cursor': 0})
                now.return_value = 103
                permit = browser.permit(WORK, token, CLIENT, identity, '111', 0)['permit']
                result = browser.page(WORK, token, CLIENT, identity, '111', 0, payload([comment('222')], 1, 1), permit)
                self.assertEqual(result['next'], {'root_id': '111', 'cursor': 1})
                now.return_value = 106
                permit = browser.permit(WORK, token, CLIENT, identity, '111', 1)['permit']
                result = browser.page(WORK, token, CLIENT, identity, '111', 1, payload([comment('333')], 2), permit)
                self.assertEqual(result['next'], {'root_id': '', 'cursor': 1})
                now.return_value = 109
                permit = browser.permit(WORK, token, CLIENT, identity, '', 1)['permit']
                result = browser.page(WORK, token, CLIENT, identity, '', 1, payload([comment('444')], 2), permit)
                self.assertIsNone(result['next'])
                browser.finish(WORK, token, CLIENT, identity, 'complete')
            saved = {'relative_path': 'comment-assets/fake.png', 'sha256': 'a' * 64, 'bytes': 10}
            with patch('app.comment_assets.download_asset', new=AsyncMock(return_value=saved)) as download:
                self.assertTrue(await worker.step())
                self.assertTrue(await worker.step())
                download.assert_awaited_once()
            core.comment_page.assert_not_awaited()
            state = store.comment_state(WORK)
            self.assertEqual((state['operation'], state['integrity'], state['total'], state['replies']),
                             ('finished', 'finished-visible', 4, 2))
            self.assertEqual(store.comment_page(WORK)['items'][0]['attachments'][0]['status'], 'saved')


@asynccontextmanager
async def no_lifespan(app):
    yield


class BrowserCommentApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        app = make_app(root / 'data', root / 'media', token='browser-test-key')
        app.router.lifespan_context = no_lifespan
        app.state.store.save_work({'id': WORK, 'desc': 'local work', 'type': '视频'}, ['video.mp4'])
        self.store = app.state.store
        self.identity = setup_comment_accounts(self.store)['secondary']
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.auth = {'Authorization': 'Bearer browser-test-key'}
        self.headers = {**self.auth, 'X-Cloudrift-Comment-Client': CLIENT,
                        'X-Cloudrift-Comment-Identity': self.identity}
        self.url = f'/api/works/{WORK}/comments/browser'

    def test_auth_no_store_and_request_limit(self):
        body = {'action': 'save', 'account_role': 'secondary'}
        self.assertEqual(self.client.post(self.url, json=body).status_code, 401)
        response = self.client.post(self.url, headers=self.auth, json=body)
        self.assertTrue(200 <= response.status_code < 300)
        self.assertIn('no-store', response.headers['cache-control'])
        session_url = self.url + '/' + response.json()['token']
        claim = {'fingerprint': self.identity, 'client_id': CLIENT}
        self.assertEqual(self.client.post(session_url + '/claim', headers=self.auth, json=claim).status_code, 200)
        for method, url, body in [('get', session_url, None),
                                  ('post', session_url + '/page', {'root_id': '', 'cursor': 0, 'payload': payload([]), 'permit': 'a' * 32}),
                                  ('post', session_url + '/finish', {'status': 'stopped'})]:
            result = getattr(self.client, method)(url, **({'json': body} if body else {}))
            self.assertEqual(result.status_code, 401)
        oversized = comment('111')
        oversized['text'] = 'x' * (2 * 1024 * 1024)
        result = self.client.post(session_url + '/page', headers=self.headers,
                                  json={'root_id': '', 'cursor': 0, 'payload': payload([oversized]), 'permit': 'a' * 32})
        self.assertTrue(400 <= result.status_code < 500)
        self.assertEqual(self.store.comment_state(WORK)['total'], 0)

    def test_poll_expires_session_while_worker_idle(self):
        with patch('app.comment_browser.time.time', return_value=100) as now:
            response = self.client.post(self.url, headers=self.auth, json={'action': 'save', 'account_role': 'secondary'})
            self.assertEqual(response.status_code, 200)
            now.return_value = 1300
            response = self.client.get(f'/api/works/{WORK}/comments', headers=self.auth)
        self.assertEqual(response.json()['status']['operation'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
