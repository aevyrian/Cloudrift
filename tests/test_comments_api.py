import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import make_app


WORK = '7431234567890123456'


@asynccontextmanager
async def no_lifespan(app):
    yield


class CommentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = make_app(root / 'data', root / 'media', token='test-comments-key')
        self.app.router.lifespan_context = no_lifespan
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.auth = {'Authorization': 'Bearer test-comments-key'}
        self.store = self.app.state.store
        self.store.save_work({'id': WORK, 'desc': 'local work', 'type': '视频'}, ['video.mp4'])
        self.url = f'/api/works/{WORK}/comments'

    def test_all_comment_routes_require_header_auth_not_query_key(self):
        routes = [('get', self.url), ('post', self.url), ('post', self.url + '/cancel'),
                  ('get', self.url + '/export'), ('get', '/api/comment-assets/' + 'a' * 64)]
        for method, url in routes:
            kwargs = {'json': {'action': 'save'}} if method == 'post' and url == self.url else {}
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, params={'token': 'test-comments-key'}, **kwargs)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn('test-comments-key', response.text)

    def test_open_only_reads_and_manual_save_does_not_create_media_task(self):
        with patch.object(self.app.state.service.core, 'comment_page', new=AsyncMock()) as network:
            response = self.client.get(self.url, headers=self.auth)
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data['can_save'])
            self.assertEqual(data['status']['operation'], 'unsaved')
            self.assertEqual(data['items'], [])
            self.assertIn('no-store', response.headers['cache-control'])
            saved = self.client.post(self.url, headers=self.auth, json={'action': 'save'})
            self.assertEqual(saved.status_code, 202)
            self.assertEqual(saved.json()['operation'], 'queued')
            self.assertEqual(self.store.tasks(), [])
            network.assert_not_awaited()

    def test_unknown_legacy_work_can_be_read_but_not_captured(self):
        self.store.save_work({'id': 'legacy-unknown', 'type': '视频'}, ['old.mp4'])
        url = '/api/works/legacy-unknown/comments'
        response = self.client.get(url, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_save'])
        self.assertEqual(self.client.post(url, headers=self.auth, json={'action': 'save'}).status_code, 409)
        self.assertEqual(self.client.get('/api/works/missing/comments', headers=self.auth).status_code, 404)

    def test_inputs_and_missing_local_assets_fail_closed(self):
        for params in ({'offset': -1}, {'limit': 501}, {'root_id': '<script>'}):
            self.assertEqual(self.client.get(self.url, headers=self.auth, params=params).status_code, 422)
        for body in ({'action': 'all'}, {'action': 'save', 'url': 'https://private.example'}):
            self.assertEqual(self.client.post(self.url, headers=self.auth, json=body).status_code, 422)
        self.assertEqual(self.client.get('/api/comment-assets/' + 'a' * 64, headers=self.auth).status_code, 404)
        self.assertEqual(self.client.get(self.url + '/export?format=html', headers=self.auth).status_code, 422)

    def test_empty_export_is_local_and_temporary_file_is_cleaned(self):
        with patch.object(self.app.state.service.core, 'comment_page', new=AsyncMock()) as network:
            response = self.client.get(self.url + '/export?format=json', headers=self.auth)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['work_id'], WORK)
            self.assertIn('attachment;', response.headers['content-disposition'])
            network.assert_not_awaited()
            self.assertFalse(list((self.store.root / 'comment-exports').glob('*')))

    def test_export_invalid_or_unsatisfiable_ranges_leave_no_temporary_files(self):
        for value, status in [('bytes=invalid', 400), ('bytes=999999999999-', 416), ('bytes=0-9', 206)]:
            with self.subTest(value=value):
                response = self.client.get(self.url + '/export?format=zip', headers={**self.auth, 'Range': value})
                self.assertEqual(response.status_code, status)
                self.assertFalse(list((self.store.root / 'comment-exports').glob('*')))


if __name__ == '__main__':
    unittest.main()
