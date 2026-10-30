import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import make_app
from app.store import Store


@asynccontextmanager
async def no_lifespan(app):
    yield


def event(work_id='7431234567890123456', viewed_at=None):
    return {'event_id': str(uuid4()), 'work_id': work_id,
            'url': f'https://www.douyin.com/video/{work_id}', 'title': '测试作品',
            'author_name': '测试作者', 'author_url': 'https://www.douyin.com/user/valid_user',
            'viewed_at': viewed_at or datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}


class BrowseHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = make_app(root / 'data', root / 'media', token='test-admin-key')
        self.app.router.lifespan_context = no_lifespan
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.auth = {'Authorization': 'Bearer test-admin-key'}

    def test_events_persist_retry_idempotently_and_do_not_create_downloads(self):
        item = event()
        second = {**item, 'event_id': str(uuid4()), 'author_url': '',
                  'url': item['url'].replace('/video/', '/note/')}
        self.assertEqual(self.client.post('/api/browse-history', json={'items': [item]}).status_code, 401)
        for batch in ([item, second], [item, second]):
            response = self.client.post('/api/browse-history', headers=self.auth, json={'items': batch})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'accepted': 2})
        self.assertEqual(self.client.get('/api/browse-history').status_code, 401)
        store = Store(self.app.state.store.root)
        self.assertEqual(store.browse_history(50)['total'], 2)
        self.assertEqual(store.tasks(), [])
        self.assertEqual(store.library()['total'], 0)
        self.assertEqual(store.authors(), [])

    def test_cursor_pagination_is_stable_for_identical_timestamps(self):
        stamp = '2025-01-01T00:00:00.000Z'
        items = [event(str(7431234567890123400 + n), stamp) for n in range(5)]
        self.assertEqual(self.client.post('/api/browse-history', headers=self.auth, json={'items': items}).status_code, 200)
        got, cursor = [], None
        while True:
            params = {'limit': 2}
            if cursor:
                params['before'] = cursor
            data = self.client.get('/api/browse-history', headers=self.auth, params=params).json()
            self.assertEqual(data['total'], 5)
            got.extend(row['event_id'] for row in data['items'])
            cursor = data['next_cursor']
            if cursor is None:
                break
        self.assertEqual(got, sorted((row['event_id'] for row in items), reverse=True))
        self.assertEqual(self.client.get('/api/browse-history', headers=self.auth, params={'before': 'bad'}).status_code, 422)
        self.assertEqual(self.client.get('/api/browse-history', headers=self.auth, params={'limit': 101}).status_code, 422)

    def test_adds_history_table_without_changing_existing_data(self):
        store = self.app.state.store
        store.set_setting('cookie', 'sessionid=test-private')
        store.save_work({'id': '7431234567890123456', 'nickname': '原作者'}, ['v.mp4'])
        task = store.create_task('7431234567890123456', 'detail')
        author = store.save_author('original', 'https://www.douyin.com/user/valid_user')
        with store.connect() as db:
            db.execute('DROP TABLE browse_history')
        upgraded = Store(store.root)
        self.assertEqual(upgraded.setting('cookie'), 'sessionid=test-private')
        self.assertEqual(upgraded.work('7431234567890123456')['author'], '原作者')
        self.assertEqual(upgraded.task(task['id'])['status'], 'queued')
        self.assertEqual(upgraded.author(author['id'])['url'], author['url'])
        self.assertEqual(upgraded.browse_history(50)['total'], 0)

    def test_late_metadata_uses_revision_and_cannot_change_identity(self):
        original = {**event(), 'title': '', 'author_name': '', 'author_url': '', 'metadata_revision': 0}
        newer = {**original, 'title': '新标题', 'author_name': '新作者', 'metadata_revision': 2}
        older = {**original, 'title': '旧标题', 'author_name': '旧作者', 'metadata_revision': 1}
        newest = {**original, 'title': '', 'author_name': '', 'author_url': 'https://www.douyin.com/user/new_author', 'metadata_revision': 3}
        for item in (original, newer, older, newest, original, newest):
            response = self.client.post('/api/browse-history', headers=self.auth, json={'items': [item]})
            self.assertEqual(response.status_code, 200)
        saved = self.client.get('/api/browse-history', headers=self.auth).json()['items'][0]
        self.assertEqual((saved['title'], saved['author_name'], saved['author_url'], saved['metadata_revision']),
                         ('新标题', '新作者', newest['author_url'], 3))
        for changed in ({'work_id': '7431234567890123457', 'url': 'https://www.douyin.com/video/7431234567890123457'},
                        {'url': original['url'].replace('/video/', '/note/')},
                        {'viewed_at': '2025-01-01T00:00:00.000Z'}):
            with self.subTest(changed=changed):
                response = self.client.post('/api/browse-history', headers=self.auth,
                                            json={'items': [event(), {**newest, **changed, 'metadata_revision': 4}]})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.client.get('/api/browse-history', headers=self.auth).json()['total'], 1)
                self.assertEqual(self.client.get('/api/browse-history', headers=self.auth).json()['items'][0], saved)

    def test_simultaneous_revisions_do_not_replace_new_with_old(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        original = {**event(), 'title': '', 'metadata_revision': 0}
        self.assertEqual(self.client.post('/api/browse-history', headers=self.auth, json={'items': [original]}).status_code, 200)
        barrier = Barrier(2)
        def send(revision):
            barrier.wait(timeout=3)
            return self.client.post('/api/browse-history', headers=self.auth,
                                    json={'items': [{**original, 'title': f'版本{revision}', 'metadata_revision': revision}]})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, (1, 2)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        item = self.client.get('/api/browse-history', headers=self.auth).json()['items'][0]
        self.assertEqual((item['title'], item['metadata_revision']), ('版本2', 2))

    def test_invalid_events_are_rejected_without_partial_writes(self):
        good = event()
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        changes = [
            {'event_id': 'not-a-uuid'}, {'work_id': '123'}, {'work_id': '１２３４５６７８９０１２３４５６７８９'},
            {'url': 'https://evil.example/video/' + good['work_id']},
            {'url': good['url'] + '?sessionid=secret'},
            {'url': 'https://www.douyin.com/video/7431234567890123457'},
            {'author_url': 'https://www.douyin.com/user/valid_user?token=secret'},
            {'author_url': 'https://www.douyin.com.evil.example/user/valid_user'},
            {'title': 'x' * 501}, {'author_name': 'x' * 101},
            {'viewed_at': future}, {'viewed_at': '2025-02-30T00:00:00.000Z'},
            {'viewed_at': '2025-01-01T00:00:00+08:00'},
            {'extra': 'untrusted'},
        ]
        for change in changes:
            with self.subTest(change=change):
                response = self.client.post('/api/browse-history', headers=self.auth,
                                            json={'items': [good, {**event(), **change}]})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('secret', response.text)
        self.assertEqual(self.client.post('/api/browse-history', headers=self.auth, json={'items': []}).status_code, 422)
        self.assertEqual(self.client.post('/api/browse-history', headers=self.auth, json={'items': [good] * 101}).status_code, 422)
        self.assertEqual(self.client.get('/api/browse-history', headers=self.auth).json()['total'], 0)


if __name__ == '__main__':
    unittest.main()
