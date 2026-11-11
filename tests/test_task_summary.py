import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import make_app


class TaskSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = make_app(root / 'data', root / 'media', token='test-key')
        self.client = TestClient(self.app)
        self.auth = {'Authorization': 'Bearer test-key'}
        self.store = self.app.state.store
        self.task = self.store.create_task('https://www.douyin.com/user/test', 'author')
        self.preview = [dict(id=str(i), desc='长描述' * 500, nickname=f'作者{i}', type='视频', width=1080, height=1920, secret='hidden') for i in range(25)]
        self.store.update_task(self.task['id'], status='awaiting_confirmation', preview=self.preview)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_summary_keeps_title_and_all_search_names_while_reducing_payload(self):
        full = self.client.get('/api/tasks', headers=self.auth)
        summary = self.client.get('/api/tasks?summary=true', headers=self.auth)
        self.assertEqual(full.status_code, 200)
        self.assertEqual(summary.status_code, 200)
        full_task, small = full.json()[0], summary.json()[0]
        self.assertEqual(small['id'], full_task['id'])
        self.assertEqual(small['status'], full_task['status'])
        self.assertEqual(small['preview'], full_task['preview'][:5])
        self.assertEqual(small['preview_nicknames'], [f'作者{i}' for i in range(25)])
        self.assertLess(len(summary.content), len(full.content) / 3)
        self.assertNotIn('secret', str(small))
        self.assertNotIn('preview_nicknames', full_task)

    def test_full_detail_requires_auth_and_retains_public_preview_contract(self):
        url = '/api/tasks/' + self.task['id']
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get('/api/tasks?summary=true').status_code, 401)
        result = self.client.get(url, headers=self.auth)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), self.client.get('/api/tasks', headers=self.auth).json()[0])
        self.assertEqual(len(result.json()['preview']), 25)
        self.assertNotIn('secret', str(result.json()))
        self.assertEqual(self.client.get('/api/tasks/not-found', headers=self.auth).status_code, 404)

    def test_summary_preserves_empty_preview_missing_fields_and_nickname_order(self):
        blank = self.store.create_task('7431234567890123457', 'detail')
        self.store.update_task(self.task['id'], preview=[{'desc': '标题', 'nickname': None}, {'nickname': '同名'}, {'nickname': '同名'}, {'nickname': '末尾'}])
        results = {task['id']: task for task in self.client.get('/api/tasks?summary=true', headers=self.auth).json()}
        self.assertEqual(results[blank['id']]['preview'], [])
        self.assertEqual(results[blank['id']]['preview_nicknames'], [])
        self.assertEqual(results[self.task['id']]['preview_nicknames'], ['', '同名', '同名', '末尾'])
        self.assertEqual(results[self.task['id']]['preview'][0], {'id': None, 'desc': '标题', 'nickname': None, 'type': None, 'width': None, 'height': None})


if __name__ == '__main__':
    unittest.main()
