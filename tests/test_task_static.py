import os
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from app.main import make_app


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href') and value and 'task-view.' in value:
                self.urls.append(value)


class TaskStaticTests(unittest.TestCase):
    def test_index_task_assets_are_served_without_starting_worker(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'APP_TOKEN': '', 'APP_LEGACY': ''}):
            root = Path(folder)
            app = make_app(root / 'data', root / 'media', core=SimpleNamespace(ready=True, error=''))
            client = TestClient(app)  # No lifespan context: the download worker never starts.
            try:
                index = client.get('/')
                self.assertEqual(index.status_code, 200)
                assets = Assets()
                assets.feed(index.text)
                self.assertEqual({urlsplit(url).path for url in assets.urls}, {'/static/task-view.css', '/static/task-view.js'})
                for url in assets.urls:
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200, url)
                    self.assertTrue(response.content, url)
                self.assertIsNone(app.state.service.worker)
                self.assertFalse(app.state.store.setting('access_token', ''))
            finally:
                client.close()
