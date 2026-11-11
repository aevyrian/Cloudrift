import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import make_app


@asynccontextmanager
async def no_lifespan(app):
    yield


class CookieBindingApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.app = make_app(root / 'data', root / 'media', token='synthetic-key')
        self.app.router.lifespan_context = no_lifespan
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.store = self.app.state.store
        self.primary = 'uid_tt=synthetic-main; sessionid=synthetic-main-session'
        self.secondary = 'uid_tt=synthetic-small; sessionid=synthetic-small-session'
        self.store.set_setting('cookie', self.primary)
        self.headers = {'Authorization': 'Bearer synthetic-key'}

    def submit(self, body):
        response = self.client.post('/api/comment-accounts/secondary-cookie', headers=self.headers, json=body)
        self.assertIn('no-store', response.headers.get('cache-control', ''))
        self.assertNotIn(self.secondary, response.text)
        return response

    def test_cookie_binding_has_immediate_result_and_preserves_download_cookie(self):
        response = self.submit({'cookie': self.secondary})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['primary']['bound'])
        self.assertTrue(response.json()['secondary']['bound'])
        self.assertTrue(response.json()['paused'])
        self.assertEqual(self.store.setting('cookie'), self.primary)
        with self.store.connect() as db:
            self.assertFalse(any('synthetic-small' in row[0] for row in db.execute('SELECT value FROM settings')))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM comment_jobs').fetchone()[0], 0)

    def test_invalid_and_same_account_have_safe_visible_errors(self):
        for body, expected in (({'cookie': 'sessionid=private-no-uid'}, 422), ({'cookie': self.primary}, 409),
                               ({'cookie': []}, 422), ({'cookie': self.secondary, 'role': 'primary'}, 422)):
            with self.subTest(expected=expected):
                response = self.submit(body)
                self.assertEqual(response.status_code, expected)
                self.assertIsInstance(response.json()['detail'], str)
                self.assertNotIn('private-no-uid', response.text)
        self.assertFalse(self.store.comment_accounts.status()['secondary']['bound'])

    def test_endpoint_requires_admin_and_does_not_echo_malformed_input(self):
        response = self.client.post('/api/comment-accounts/secondary-cookie', json={'cookie': self.secondary})
        self.assertEqual(response.status_code, 401)
        response = self.client.post('/api/comment-accounts/secondary-cookie', headers=self.headers, json=self.secondary)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('synthetic-small', response.text)

    def test_previous_primary_browser_binding_is_rejected(self):
        response = self.client.post('/api/comment-accounts/bind', headers=self.headers, json={'role': 'primary'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.setting('cookie'), self.primary)


if __name__ == '__main__':
    unittest.main()
