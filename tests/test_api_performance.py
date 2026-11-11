import tempfile
import threading
import time
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import make_app
from test_comment_fixtures import setup_comment_accounts


@asynccontextmanager
async def no_worker(app):
    yield


class ApiPerformanceTests(unittest.TestCase):
    def test_comment_write_does_not_delay_health(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = make_app(root/'data', root/'media', token='synthetic-key')
            app.router.lifespan_context = no_worker
            store = app.state.store
            work = '7431234567890123456'
            store.save_work({'id': work, 'type': '视频'}, ['video.mp4'])
            setup_comment_accounts(store)
            entered, release = threading.Event(), threading.Event()
            original = app.state.service.comments.queue
            def slow_queue(*args):
                entered.set()
                release.wait(1)
                return original(*args)
            with TestClient(app) as client, patch.object(app.state.service.comments, 'queue', side_effect=slow_queue):
                responses = []
                def post():
                    responses.append(client.post(f'/api/works/{work}/comments',
                        headers={'Authorization': 'Bearer synthetic-key'},
                        json={'action': 'save', 'account_role': 'primary', 'allow_primary': True}))
                thread = threading.Thread(target=post)
                thread.start()
                try:
                    self.assertTrue(entered.wait(1))
                    start = time.monotonic()
                    client.get('/health')
                    self.assertLess(time.monotonic()-start, .5)
                finally:
                    release.set()
                    thread.join(3)
                self.assertEqual(responses[0].status_code, 202)
