import tempfile
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import make_app
from app.comment_accounts import AccountError, cookie_fingerprint


WORK = '7431234567890123456'
OTHER_WORK = '7431234567890123457'
PRIMARY_COOKIE = 'uid_tt=synthetic-primary; sessionid=synthetic-session'
SECONDARY = 'b' * 64
CLIENT = 'c' * 32
OTHER_CLIENT = 'd' * 32


@asynccontextmanager
async def no_lifespan(app):
    yield


def item(cid, replies=0):
    return {'cid': cid, 'user': {'nickname': '测试'}, 'text': '评论', 'create_time': 10,
            'digg_count': 1, 'reply_comment_total': replies}


def payload(items, cursor=0, more=0):
    return {'status_code': 0, 'comments': items, 'has_more': more, 'cursor': cursor}


class CommentAccountsApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.auth = {'Authorization': 'Bearer synthetic-account-key'}
        self.client, self.app = self.make_client()
        self.primary = cookie_fingerprint(PRIMARY_COOKIE, self.app.state.store.comment_accounts.status()['identity_salt'])
        for work in (WORK, OTHER_WORK):
            self.app.state.store.save_work({'id': work, 'type': '视频'}, ['video.mp4'])

    def make_client(self):
        app = make_app(self.root / 'data', self.root / 'media', token='synthetic-account-key')
        app.router.lifespan_context = no_lifespan
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client, app

    def request(self, method, path, body=None, headers=None, client=None):
        kwargs = {'headers': {**self.auth, **(headers or {})}}
        if body is not None:
            kwargs['json'] = body
        response = getattr(client or self.client, method)(path, **kwargs)
        self.assertIn('no-store', response.headers.get('cache-control', ''))
        return response

    def ok(self, response):
        self.assertTrue(200 <= response.status_code < 300, response.text)
        return response.json()

    def rejected(self, response):
        self.assertTrue(400 <= response.status_code < 500, response.text)

    def bind(self, role, fingerprint):
        created = self.ok(self.request('post', '/api/comment-accounts/bind', {'role': role}))
        self.assertRegex(created['nonce'], r'^[A-Za-z0-9_-]{43}$')
        url = '/api/comment-accounts/bind/' + created['nonce']
        self.assertEqual(self.ok(self.request('get', url))['status'], 'waiting')
        result = self.ok(self.request('post', url, {'fingerprint': fingerprint}))
        self.assertEqual((result['status'], result['role']), ('bound', role))

    def configured(self):
        self.app.state.store.set_setting('cookie', PRIMARY_COOKIE)
        self.bind('secondary', SECONDARY)
        state = self.ok(self.request('post', '/api/comment-accounts/resume', {'acknowledge': True}))
        self.assertFalse(state['paused'])

    def start(self, work=WORK, role='secondary', allow_primary=False):
        response = self.request('post', f'/api/works/{work}/comments/browser',
                                {'action': 'save', 'account_role': role, 'allow_primary': allow_primary})
        session = self.ok(response)
        self.assertEqual(session['account_role'], role)
        return f'/api/works/{work}/comments/browser/{session["token"]}'

    def headers(self, fingerprint=SECONDARY, client=CLIENT):
        return {'X-Cloudrift-Comment-Client': client, 'X-Cloudrift-Comment-Identity': fingerprint}

    def claim(self, url, fingerprint=SECONDARY, client=CLIENT):
        return self.request('post', url + '/claim', {'fingerprint': fingerprint, 'client_id': client})

    def permit(self, url, root='', cursor=0, headers=None):
        return self.request('post', url + '/permit', {'root_id': root, 'cursor': cursor},
                            headers or self.headers())

    def ready_permit(self, url, root='', cursor=0):
        deadline = time.monotonic() + 25
        while True:
            result = self.ok(self.permit(url, root, cursor))
            if result['ready']:
                self.assertRegex(result['permit'], r'^[a-f0-9]{32}$')
                return result['permit']
            self.assertLess(time.monotonic(), deadline, '评论许可等待超时')
            time.sleep(max(0.05, min(float(result['retry_after']), 1)))

    def page(self, url, permit, data, root='', cursor=0, headers=None):
        return self.request('post', url + '/page', {'root_id': root, 'cursor': cursor,
                            'payload': data, 'permit': permit}, headers or self.headers())

    def test_binding_order_privacy_and_explicit_resume(self):
        self.assertEqual(self.client.get('/api/comment-accounts').status_code, 401)
        self.assertEqual(self.client.post('/api/comment-accounts/pause', json={}).status_code, 401)
        state = self.ok(self.request('get', '/api/comment-accounts'))
        self.assertEqual(state['default_role'], 'secondary')
        self.assertTrue(state['paused'])
        self.assertRegex(state['identity_salt'], r'^[a-f0-9]{64}$')
        self.rejected(self.request('post', '/api/comment-accounts/resume', {'acknowledge': True}))
        premature = self.request('post', '/api/comment-accounts/bind', {'role': 'secondary'})
        if 200 <= premature.status_code < 300:
            url = '/api/comment-accounts/bind/' + premature.json()['nonce']
            self.rejected(self.request('post', url, {'fingerprint': SECONDARY}))
        else:
            self.rejected(premature)
        self.rejected(self.request('post', '/api/comment-accounts/bind', {'role': 'primary'}))
        self.app.state.store.set_setting('cookie', PRIMARY_COOKIE)
        nonce = self.ok(self.request('post', '/api/comment-accounts/bind', {'role': 'secondary'}))['nonce']
        self.rejected(self.request('post', '/api/comment-accounts/bind/' + nonce, {'fingerprint': self.primary}))
        state = self.ok(self.request('get', '/api/comment-accounts'))
        self.assertFalse(state['secondary']['bound'])
        self.assertNotIn(self.primary, str(state))
        self.bind('secondary', SECONDARY)
        self.assertTrue(self.ok(self.request('get', '/api/comment-accounts'))['paused'])
        self.rejected(self.request('post', '/api/comment-accounts/resume', {'acknowledge': False}))
        self.assertFalse(self.ok(self.request('post', '/api/comment-accounts/resume', {'acknowledge': True}))['paused'])

    def test_legacy_core_and_primary_cannot_bypass_policy(self):
        self.configured()
        url = f'/api/works/{WORK}/comments'
        for body in ({'action': 'save'}, {'action': 'save', 'account_role': 'secondary'},
                     {'action': 'save', 'account_role': 'primary', 'allow_primary': False}):
            self.rejected(self.request('post', url, body))
        self.rejected(self.request('post', url + '/browser',
                                   {'action': 'save', 'account_role': 'primary', 'allow_primary': False}))

    def test_claim_is_single_tab_and_single_identity_across_works(self):
        self.configured()
        url = self.start()
        self.rejected(self.claim(url, self.primary, OTHER_CLIENT))
        self.ok(self.claim(url))
        self.ok(self.claim(url))
        self.rejected(self.claim(url, SECONDARY, OTHER_CLIENT))
        self.rejected(self.request('get', url))
        self.rejected(self.request('get', url, headers=self.headers(SECONDARY, OTHER_CLIENT)))
        self.rejected(self.request('get', url, headers=self.headers(self.primary, CLIENT)))
        self.ok(self.request('get', url, headers=self.headers()))
        self.rejected(self.request('post', url + '/permit', {'root_id': '', 'cursor': 0}))
        self.rejected(self.request('post', url + '/page', {'root_id': '', 'cursor': 0,
                                   'payload': payload([]), 'permit': 'f' * 32}))
        self.rejected(self.request('post', url + '/finish', {'status': 'stopped'}))
        change = self.request('post', '/api/comment-accounts/bind', {'role': 'primary'})
        if 200 <= change.status_code < 300:
            bind_url = '/api/comment-accounts/bind/' + change.json()['nonce']
            self.rejected(self.request('post', bind_url, {'fingerprint': 'e' * 64}))
        else:
            self.rejected(change)
        second = self.request('post', f'/api/works/{OTHER_WORK}/comments/browser',
                              {'action': 'save', 'account_role': 'secondary', 'allow_primary': False})
        if 200 <= second.status_code < 300:
            other = f'/api/works/{OTHER_WORK}/comments/browser/{second.json()["token"]}'
            self.rejected(self.claim(other, SECONDARY, OTHER_CLIENT))
        else:
            self.rejected(second)

    def test_pause_persists_after_late_block_and_restart(self):
        self.configured()
        url = self.start()
        self.ok(self.claim(url))
        self.ok(self.request('post', url + '/finish', {'status': 'stopped'}, self.headers()))
        self.rejected(self.request('post', '/api/comment-accounts/blocked', {'fingerprint': 'invalid'}))
        self.assertTrue(self.ok(self.request('post', '/api/comment-accounts/blocked',
                                             {'fingerprint': 'e' * 64}))['paused'])
        self.assertFalse(self.ok(self.request('post', '/api/comment-accounts/resume',
                                              {'acknowledge': True}))['paused'])
        state = self.ok(self.request('post', '/api/comment-accounts/blocked', {'fingerprint': SECONDARY}))
        self.assertTrue(state['paused'])
        self.assertNotIn(SECONDARY, str(state))
        self.rejected(self.request('post', f'/api/works/{OTHER_WORK}/comments/browser',
                                   {'action': 'save', 'account_role': 'primary', 'allow_primary': True}))
        client, _ = self.make_client()
        state = self.ok(self.request('get', '/api/comment-accounts', client=client))
        self.assertTrue(state['paused'])
        self.rejected(self.request('post', f'/api/works/{OTHER_WORK}/comments',
                                   {'action': 'save', 'account_role': 'primary', 'allow_primary': True}, client=client))
        self.assertFalse(self.ok(self.request('post', '/api/comment-accounts/resume',
                                               {'acknowledge': True}, client=client))['paused'])

    def test_old_and_unbound_valid_fingerprints_pause_all_comments(self):
        self.configured()
        self.app.state.store.set_setting('cookie', 'uid_tt=other-primary; sessionid=synthetic-session')
        self.ok(self.request('post', '/api/comment-accounts/resume', {'acknowledge': True}))
        for fingerprint in (self.primary, 'f' * 64):
            state = self.ok(self.request('post', '/api/comment-accounts/blocked', {'fingerprint': fingerprint}))
            self.assertTrue(state['paused'])
            self.assertNotIn(fingerprint, str(state))
            self.rejected(self.request('post', f'/api/works/{OTHER_WORK}/comments/browser',
                                       {'action': 'save', 'account_role': 'secondary', 'allow_primary': False}))
            self.ok(self.request('post', '/api/comment-accounts/resume', {'acknowledge': True}))

    def test_download_account_upgrade_refresh_switch_and_missing(self):
        store = self.app.state.store
        self.configured()
        status = store.comment_accounts.status()
        self.assertEqual(status['primary']['source'], 'download')
        self.assertTrue(status['primary']['bound'])
        store.set_setting('cookie', 'uid_tt=synthetic-primary; uid_tt_ss=synthetic-primary; sessionid=new-session')
        self.assertFalse(store.comment_accounts.status()['paused'])
        url = self.start()
        self.ok(self.claim(url))
        store.set_setting('cookie', 'uid_tt=new-primary; sessionid=new-session')
        self.assertTrue(store.comment_accounts.status()['paused'])
        self.assertEqual(store.comment_state(WORK)['operation'], 'interrupted')
        store.set_setting('cookie', 'sessionid=synthetic-session')
        self.assertFalse(store.comment_accounts.status()['primary']['bound'])
        self.assertTrue(store.comment_accounts.status()['paused'])
        store.set_setting('cookie', PRIMARY_COOKIE)
        with store.connect() as db:
            store.comment_accounts._set(db, 'comment_account_primary', 'a' * 64)
        client, _ = self.make_client()
        upgraded = self.ok(self.request('get', '/api/comment-accounts', client=client))
        self.assertTrue(upgraded['primary']['bound'])
        self.assertEqual(upgraded['primary']['source'], 'download')
        self.assertTrue(upgraded['paused'])
        self.assertNotIn(self.primary, str(upgraded))

    def test_secondary_cookie_binding_rejects_invalid_and_does_not_store_cookie(self):
        self.app.state.store.set_setting('cookie', PRIMARY_COOKIE)
        store = self.app.state.store
        accounts = store.comment_accounts
        for cookie in ('sessionid=s', 'uid_tt=other', 'uid_tt=one; uid_tt=two; sessionid=s',
                       'uid_tt=one; uid_tt_ss=two; sessionid=s', PRIMARY_COOKIE):
            with self.subTest(cookie=cookie), self.assertRaises(AccountError) as caught:
                accounts.bind_cookie(cookie)
            self.assertNotIn(cookie, str(caught.exception))
        secret = 'uid_tt=synthetic-secondary; sessionid=secondary-private-value'
        result = accounts.bind_cookie(secret)
        self.assertTrue(result['secondary']['bound'])
        self.assertNotIn(secret, str(result))
        with store.connect() as db:
            saved = str([tuple(row) for row in db.execute('SELECT key,value FROM settings')])
        self.assertNotIn('secondary-private-value', saved)
        accounts.resume(True)
        url = self.start()
        with self.assertRaises(AccountError):
            accounts.bind_cookie('uid_tt=another-secondary; sessionid=s')
        self.ok(self.request('post', f'/api/works/{WORK}/comments/cancel'))
        store.set_setting('cookie', secret)
        status = accounts.status()
        self.assertTrue(status['paused'])
        self.assertFalse(status['secondary']['bound'])

    def test_old_primary_nonce_cannot_override_download_identity(self):
        self.configured()
        accounts = self.app.state.store.comment_accounts
        nonce = 'x' * 43
        accounts.bindings[nonce] = {'role': 'primary', 'expires_at': time.time() + 600, 'status': 'waiting'}
        self.rejected(self.request('post', '/api/comment-accounts/bind/' + nonce, {'fingerprint': 'e' * 64}))
        self.assertTrue(accounts.status()['primary']['bound'])

    def test_binding_submission_cleans_expired_browser_session(self):
        self.configured()
        with patch('time.time', return_value=1000000) as now:
            self.start()
            now.return_value = 1001199
            binding = self.ok(self.request('post', '/api/comment-accounts/bind', {'role': 'secondary'}))
            bind_url = '/api/comment-accounts/bind/' + binding['nonce']
            self.rejected(self.request('post', bind_url, {'fingerprint': 'e' * 64}))
            now.return_value = 1001201
            result = self.ok(self.request('post', bind_url, {'fingerprint': 'e' * 64}))
            self.assertEqual((result['status'], result['role']), ('bound', 'secondary'))
        status = self.ok(self.request('get', f'/api/works/{WORK}/comments'))['status']
        self.assertEqual(status['operation'], 'interrupted')

    def test_comment_status_exposes_role_without_fingerprint(self):
        self.configured()
        url = self.start()
        self.ok(self.claim(url))
        result = self.ok(self.request('get', f'/api/works/{WORK}/comments'))
        self.assertEqual(result['status']['account_role'], 'secondary')
        self.assertNotIn(SECONDARY, str(result))
        self.assertNotIn('fingerprint', str(result))
        self.ok(self.request('post', url + '/finish', {'status': 'stopped'}, self.headers()))
        stopped = self.ok(self.request('get', f'/api/works/{WORK}/comments'))['status']
        self.assertEqual(stopped['account_role'], 'secondary')
        self.assertNotIn(SECONDARY, str(stopped))

    def test_permit_is_required_once_and_scoped_to_checkpoint(self):
        self.configured()
        url = self.start()
        self.ok(self.claim(url))
        self.rejected(self.page(url, 'f' * 32, payload([item('111')], 1, 1)))
        self.rejected(self.permit(url, cursor=1))
        permit = self.ready_permit(url)
        repeat = self.ok(self.permit(url))
        if repeat['ready']:
            self.assertEqual(repeat['permit'], permit)
        first = self.ok(self.page(url, permit, payload([item('111')], 1, 1)))
        self.assertEqual(first['next'], {'root_id': '', 'cursor': 1})
        self.rejected(self.page(url, permit, payload([item('222')], 2), cursor=1))
        wait = self.ok(self.permit(url, cursor=1))
        self.assertFalse(wait['ready'])
        self.assertGreater(wait['retry_after'], 0)

    def test_pacing_is_shared_across_successive_works(self):
        self.configured()
        first = self.start()
        self.ok(self.claim(first))
        self.ready_permit(first)
        self.ok(self.request('post', first + '/finish', {'status': 'stopped'}, self.headers()))
        second = self.start(OTHER_WORK)
        self.ok(self.claim(second, SECONDARY, OTHER_CLIENT))
        waiting = self.ok(self.permit(second, headers=self.headers(SECONDARY, OTHER_CLIENT)))
        self.assertFalse(waiting['ready'])
        self.assertGreater(waiting['retry_after'], 0)

    def test_root_conflict_rejects_whole_page_without_losing_checkpoint(self):
        self.configured()
        url = self.start()
        self.ok(self.claim(url))
        permit = self.ready_permit(url)
        originals = [item(str(111 + i), 1 if i == 0 else 0) for i in range(20)]
        first = self.ok(self.page(url, permit, payload(originals, 1)))
        self.assertEqual(first['next'], {'root_id': '111', 'cursor': 0})
        self.ok(self.request('post', url + '/finish', {'status': 'stopped'}, self.headers()))
        resumed = self.ok(self.request('post', f'/api/works/{WORK}/comments/browser',
                                       {'action': 'continue', 'account_role': 'secondary', 'allow_primary': False}))
        url = f'/api/works/{WORK}/comments/browser/{resumed["token"]}'
        self.ok(self.claim(url))
        self.assertEqual(resumed['next'], {'root_id': '111', 'cursor': 0})
        before = self.ok(self.request('get', f'/api/works/{WORK}/comments'))
        permit = self.ready_permit(url, '111', 0)
        self.rejected(self.page(url, permit, payload([item('999'), item('111')], 1), '111', 0))
        session = self.ok(self.request('get', url, headers=self.headers()))
        self.assertEqual(session['next'], {'root_id': '111', 'cursor': 0})
        result = self.ok(self.request('get', f'/api/works/{WORK}/comments'))
        self.assertEqual(result['status']['total'], 20)
        self.assertEqual(result['items'], before['items'])
        self.assertEqual({entry['id'] for entry in result['items']}, {entry['cid'] for entry in originals})

    def test_restart_interrupts_legacy_unmarked_jobs_and_preserves_comments(self):
        with self.app.state.store.connect() as db:
            for work, operation in ((WORK, 'queued'), (OTHER_WORK, 'running')):
                db.execute('''INSERT INTO comment_jobs
                              (work_id,operation,integrity,reason,token,checkpoint,updated_at)
                              VALUES (?,?,?, ?,?,?,?)''',
                           (work, operation, 'partial', '', 'a' * 32,
                            '{"cursor":0,"root_done":false,"replies":[]}', 'synthetic'))
                db.execute('INSERT INTO comments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                           (work, '111', '', '', '', '测试', '已有评论', 1, 0, 0, 'synthetic', 'synthetic'))
        client, _ = self.make_client()
        for work in (WORK, OTHER_WORK):
            result = self.ok(self.request('get', f'/api/works/{work}/comments', client=client))
            self.assertEqual(result['status']['operation'], 'interrupted')
            self.assertEqual(result['status']['total'], 1)
            self.assertEqual(result['items'][0]['text'], '已有评论')


if __name__ == '__main__':
    unittest.main()
