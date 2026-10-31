import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import make_app
from app.store import Store

WORK = '7431234567890123456'
OTHER = '7431234567890123457'


def raw(cid, **extra):
    value = {'cid': cid, 'text': 'hello', 'create_time': 10, 'digg_count': 1,
             'reply_comment_total': 0, 'user': {'nickname': '名字'}}
    value.update(extra)
    return value


def page(comments, cursor, has_more=0, total=None):
    value = {'comments': comments, 'cursor': cursor, 'has_more': has_more}
    if total is not None:
        value['total'] = total
    return value


@asynccontextmanager
async def no_lifespan(app):
    yield


class AssistantCommentApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = make_app(root / 'data', root / 'media', token='test-assistant-key')
        self.app.router.lifespan_context = no_lifespan
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.auth = {'Authorization': 'Bearer test-assistant-key'}
        self.store = self.app.state.store
        self.store.save_work({'id': WORK, 'desc': 'local work', 'type': '视频'}, ['video.mp4'])
        self.url = f'/api/works/{WORK}/comments/assistant'

    def submit(self, payload, **body):
        return self.client.post(self.url, headers=self.auth, json={'page': payload, **body})

    def test_route_requires_bearer_auth_not_query_key(self):
        body = {'page': page([raw('111')], 0)}
        response = self.client.post(self.url, params={'token': 'test-assistant-key'}, json=body)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('test-assistant-key', response.text)

    def test_top_level_and_reply_pages_share_mapping_and_save_progress(self):
        first = self.submit(page([raw('111'), raw('222')], 20, has_more=1, total=2), cursor=0)
        self.assertEqual(first.status_code, 202)
        data = first.json()
        token = data['token']
        self.assertTrue(data['accepted'])
        self.assertEqual(data['new_comments'], 2)
        self.assertEqual(data['checkpoint'], {'channel': 'assistant', 'cursor': 20, 'root_done': False,
                                              'replies': [], 'direct': True})
        self.assertEqual(data['status']['operation'], 'running')
        self.assertEqual(self.store.comment_state(WORK)['total'], 2)
        self.assertEqual(self.store.comment_page(WORK)['items'][0]['text'], 'hello')

        second = self.submit(page([raw('333', reply_comment_total=1)], 40, has_more=0, total=3),
                             cursor=20, token=token)
        self.assertEqual(second.status_code, 202)
        data = second.json()
        self.assertEqual(data['checkpoint']['cursor'], 40)
        self.assertTrue(data['checkpoint']['root_done'])
        self.assertEqual(data['checkpoint']['replies'], [{'id': '333', 'cursor': 0}])

        reply = self.submit(page([raw('444', reply_to_reply_id='333', reply_to_user={'nickname': 'Bob'})], 0),
                            root_id='333', cursor=0, token=token)
        self.assertEqual(reply.status_code, 202)
        data = reply.json()
        self.assertEqual(data['checkpoint']['replies'], [])
        self.assertEqual(data['status']['operation'], 'finished')
        state = self.store.comment_state(WORK)
        self.assertEqual((state['total'], state['replies']), (4, 1))
        items = self.store.comment_page(WORK, '333')['items']
        self.assertEqual(items[0]['root_id'], '333')
        self.assertEqual(items[0]['reply_to_user'], 'Bob')

    def test_duplicate_comment_ids_are_deduplicated_and_replay_keeps_progress(self):
        first = self.submit(page([raw('111')], 20, has_more=1, total=1), cursor=0).json()
        token = first['token']
        replay = self.submit(page([raw('111')], 20, has_more=1, total=1), cursor=0, token=token)
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(replay.json()['new_comments'], 0)
        self.assertEqual(replay.json()['checkpoint']['cursor'], 20)
        self.assertEqual(self.store.comment_state(WORK)['total'], 1)

        again = self.submit(page([raw('111', text='updated')], 40, has_more=0, total=1),
                            cursor=20, token=token)
        self.assertEqual(again.status_code, 202)
        self.assertEqual(again.json()['new_comments'], 0)
        self.assertEqual(self.store.comment_page(WORK)['items'][0]['text'], 'updated')

    def test_invalid_pages_and_stale_sessions_fail_closed(self):
        for payload in ({}, {'comments': None, 'cursor': 0, 'has_more': 0},
                        {'comments': [], 'cursor': -1, 'has_more': 0},
                        {'comments': [raw('111')], 'cursor': 0, 'has_more': 1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.submit(payload, cursor=0).status_code, 422)

        started = self.submit(page([raw('111')], 20, has_more=1), cursor=0).json()
        self.assertEqual(self.submit(page([raw('222')], 40, has_more=0), cursor=99).status_code, 422)
        self.assertEqual(self.submit(page([raw('222')], 40, has_more=0), cursor=20,
                                     token='0' * 32).status_code, 422)
        self.assertEqual(self.submit(page([raw('222')], 40, has_more=0), root_id='999',
                                     cursor=0, token=started['token']).status_code, 422)
        self.assertEqual(self.store.comment_state(WORK)['total'], 1)

    def test_unknown_work_and_legacy_work_are_rejected(self):
        self.assertEqual(self.client.post('/api/works/missing/comments/assistant', headers=self.auth,
                                          json={'page': page([raw('111')], 0)}).status_code, 404)
        self.store.save_work({'id': 'legacy-unknown', 'type': '视频'}, ['old.mp4'])
        response = self.client.post('/api/works/legacy-unknown/comments/assistant', headers=self.auth,
                                    json={'page': page([raw('111')], 0)})
        self.assertEqual(response.status_code, 409)

    def test_finished_session_rejects_more_pages(self):
        result = self.submit(page([raw('111')], 0, has_more=0), cursor=0).json()
        self.assertEqual(result['status']['operation'], 'finished')
        response = self.submit(page([raw('222')], 0, has_more=0), cursor=0, token=result['token'])
        self.assertEqual(response.status_code, 422)

    def test_default_queue_selects_assistant_and_explicit_server_is_unchanged(self):
        response = self.client.post(f'/api/works/{WORK}/comments', headers=self.auth, json={'action': 'save'})
        self.assertEqual(response.status_code, 202)
        checkpoint = self.store.comment_job(WORK)['checkpoint']
        self.assertEqual((checkpoint['channel'], checkpoint['direct'] if 'direct' in checkpoint else False),
                         ('assistant', False))

        self.store.comment_cancel(WORK)
        response = self.client.post(f'/api/works/{WORK}/comments', headers=self.auth,
                                    json={'action': 'save', 'channel': 'server'})
        self.assertEqual(response.status_code, 202)
        checkpoint = self.store.comment_job(WORK)['checkpoint']
        self.assertEqual((checkpoint['channel'], checkpoint['direct'] if 'direct' in checkpoint else False),
                         ('server', False))

    def test_invalid_channel_is_rejected(self):
        response = self.client.post(f'/api/works/{WORK}/comments', headers=self.auth,
                                    json={'action': 'save', 'channel': 'other'})
        self.assertEqual(response.status_code, 422)

    def test_settings_expose_channel_copy_and_batch_switch_defaults_off(self):
        settings = self.client.get('/api/settings', headers=self.auth).json()
        self.assertIs(settings['assistant_batch'], False)
        self.assertIsNone(self.store.setting('assistant_batch'))

        response = self.client.put('/api/settings', headers=self.auth, json={'assistant_batch': True})
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()['assistant_batch'], True)
        self.assertIs(self.store.setting('assistant_batch'), True)
        self.assertIs(self.client.get('/api/settings', headers=self.auth).json()['assistant_batch'], True)

        response = self.client.put('/api/settings', headers=self.auth, json={'assistant_batch': False})
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()['assistant_batch'], False)

    def test_batch_switch_rejects_non_boolean_values(self):
        for value in ('yes', 1, []):
            with self.subTest(value=value):
                response = self.client.put('/api/settings', headers=self.auth,
                                           json={'assistant_batch': value})
                self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.store.setting('assistant_batch'))

    def test_comment_status_reports_active_channel(self):
        self.client.post(f'/api/works/{WORK}/comments', headers=self.auth, json={'action': 'save'})
        self.assertEqual(self.store.comment_state(WORK)['channel'], 'assistant')
        self.store.comment_cancel(WORK)
        self.client.post(f'/api/works/{WORK}/comments', headers=self.auth,
                         json={'action': 'save', 'channel': 'server'})
        self.assertEqual(self.store.comment_state(WORK)['channel'], 'server')


class AssistantDirectJobClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_background_worker_does_not_claim_direct_assistant_session(self):
        job = self.store.comment_assistant_start(WORK)
        self.assertIsNone(self.store.comment_claim())
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'running')
        self.assertEqual(self.store.comment_job(WORK)['token'], job['token'])

        server = self.store.comment_queue(OTHER, 'save')
        claimed = self.store.comment_claim()
        self.assertEqual(claimed['token'], server['token'])
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'running')

    def test_interrupted_session_resumes_with_token_but_cancelled_session_does_not(self):
        job = self.store.comment_assistant_start(WORK)
        self.store.comment_recover()
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'interrupted')
        resumed = self.store.comment_assistant_start(WORK, token=job['token'])
        self.assertEqual(resumed['token'], job['token'])
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'running')

        self.store.comment_cancel(WORK)
        with self.assertRaises(ValueError):
            self.store.comment_assistant_start(WORK, token=job['token'])

    def test_new_save_resets_progress_but_continue_resumes_it(self):
        job = self.store.comment_assistant_start(WORK)
        token = job['token']
        self.store.comment_commit_page(WORK, token, [], {'channel': 'assistant', 'cursor': 5,
                                                         'root_done': False, 'replies': [], 'direct': True},
                                       None, 0)
        self.store.comment_cancel(WORK)
        resumed = self.store.comment_assistant_start(WORK, 'continue')
        self.assertEqual(resumed['checkpoint']['cursor'], 5)
        reset = self.store.comment_assistant_start(WORK, 'save')
        self.assertEqual(reset['checkpoint'], {'channel': 'assistant', 'cursor': 0,
                                               'root_done': False, 'replies': [], 'direct': True})


if __name__ == '__main__':
    unittest.main()
