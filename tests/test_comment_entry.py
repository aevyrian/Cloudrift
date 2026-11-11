import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.comment_entry import register_comment_entry
from app.store import Store


class CommentEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        app = FastAPI()

        def authorize(request: Request):
            if request.headers.get('Authorization') != 'Bearer ok':
                raise HTTPException(401)

        register_comment_entry(app, self.store, authorize)
        self.client = TestClient(app, headers={'Authorization': 'Bearer ok'})
        self.url = 'https://www.douyin.com/user/SEC_user_123'
        self.author = self.store.save_author('a1', self.url, '同名')

    def test_channel_is_validated_and_requires_auth(self):
        self.assertEqual(TestClient(self.client.app).get('/api/comment-channel').status_code, 401)
        self.assertEqual(self.client.get('/api/comment-channel').json(), {'channel': 'browser'})
        self.assertEqual(self.client.put('/api/comment-channel', json={'channel': 'core', 'allow_primary': True}).status_code, 422)
        self.assertEqual(self.client.put('/api/comment-channel', json={'channel': 'core'}).json(), {'channel': 'core'})
        self.assertEqual(self.client.get('/api/comment-channel').json(), {'channel': 'core'})

    def test_targets_use_verified_ids_and_skip_unidentified_or_ambiguous(self):
        work1, work2 = '7431234567890123456', '7431234567890123457'
        self.store.save_works([
            ({'id': work1, 'sec_uid': 'SEC_user_123', 'nickname': '同名'}, ['a']),
            ({'id': 'legacy-ok', 'uid': '111', 'nickname': '同名'}, ['b']),
            ({'id': 'legacy-unknown', 'uid': '111', 'nickname': '同名'}, ['c']),
            ({'id': 'legacy-ambiguous', 'uid': '111', 'nickname': '同名'}, ['d']),
            ({'id': '7431234567890123458', 'sec_uid': 'OTHER_user', 'nickname': '同名'}, ['e']),
        ])
        task = self.store.create_task(self.url, 'author', author_id=self.author['id'])
        self.store.update_task(task['id'], preview=[{'sec_uid': 'SEC_user_123', 'uid': '111'}])
        with self.store.connect() as db:
            db.executemany('INSERT INTO work_aliases VALUES (?,?)', [(work2, 'legacy-ok'), ('7431234567890123459', 'legacy-ambiguous'), ('7431234567890123460', 'legacy-ambiguous')])
        result = self.client.get('/api/authors/a1/comment-targets').json()
        self.assertEqual({item['real_id'] for item in result['targets']}, {work1, work2})
        self.assertEqual((result['matched'], result['skipped'], result['identity_limited']), (2, 2, False))
        self.store.update_task(task['id'], preview=[{'sec_uid': 'SEC_user_123', 'uid': '111'}, {'sec_uid': 'SEC_user_123', 'uid': '222'}])
        limited = self.client.get('/api/authors/a1/comment-targets').json()
        self.assertEqual((limited['matched'], limited['identity_limited']), (1, True))

    def test_noncanonical_author_url_is_rejected(self):
        author = self.store.save_author('short', 'https://v.douyin.com/abcdef', '短链接')
        self.assertEqual(self.client.get(f"/api/authors/{author['id']}/comment-targets").status_code, 409)
        author = self.store.save_author('bad-port', 'https://www.douyin.com:bad/user/SEC_user_123', '异常地址')
        self.assertEqual(self.client.get(f"/api/authors/{author['id']}/comment-targets").status_code, 409)
