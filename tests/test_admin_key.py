import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import make_app
from app.store import Store
from tests.test_backend import FakeCore


class AdminKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def app(self, **kwargs):
        return make_app(self.root / "data", self.root / "media", core=FakeCore(self.root / "media"), **kwargs)

    @staticmethod
    def headers(key):
        return {"Authorization": f"Bearer {key}"}

    def test_first_setup_is_once_only_and_protects_existing_apis(self):
        with patch.dict(os.environ, {"APP_TOKEN": ""}), TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/auth").json(), {"setup_required": True, "requires_current_key": False})
            self.assertEqual(client.get("/api/settings").status_code, 401)
            self.assertEqual(client.post("/api/auth/setup", json={"key": "short"}).status_code, 422)
            for invalid in ("中文管理员密钥测试", "contains space", "control\ncharacter"):
                self.assertEqual(client.post("/api/auth/setup", json={"key": invalid}).status_code, 422)
            self.assertEqual(client.post("/api/auth/setup", json={"key": "My-first-admin-key"}).status_code, 200)
            self.assertEqual(client.post("/api/auth/setup", json={"key": "another-admin-key"}).status_code, 409)
            response = client.get("/api/settings", headers=self.headers("My-first-admin-key"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["script_token"], "My-first-admin-key")
            self.assertNotIn("My-first-admin-key", client.get("/api/auth").text)
            for asset in ("/style.css", "/app.js", "/favicon.svg"):
                self.assertEqual(client.get(asset).status_code, 200)
            self.assertIn("image/svg+xml", client.get("/favicon.svg").headers["content-type"])

    def test_existing_install_requires_its_original_key_to_claim(self):
        store = Store(self.root / "data")
        store.set_setting("access_token", "old-generated-key")
        (self.root / "data" / "access.key").write_text("old-generated-key")
        store.set_setting("cookie", "keep-cookie-private")
        store.save_work({"id": "sample", "type": "图集"}, ["sample.jpg"])
        with TestClient(self.app()) as client:
            self.assertEqual(client.get("/api/auth").json(), {"setup_required": True, "requires_current_key": True})
            self.assertEqual(client.post("/api/auth/setup", json={"key": "My-first-admin-key"}).status_code, 401)
            response = client.post("/api/auth/setup", headers=self.headers("old-generated-key"), json={"key": "My-first-admin-key"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get("/api/settings", headers=self.headers("old-generated-key")).status_code, 401)
            self.assertEqual(store.setting("cookie"), "keep-cookie-private")
            self.assertIsNotNone(store.work("sample"))
            self.assertFalse((self.root / "data" / "access.key").exists())

    def test_rotation_invalidates_old_key_and_survives_restart_and_env_seed(self):
        with patch.dict(os.environ, {"APP_TOKEN": "original-env-seed"}):
            with TestClient(self.app()) as client:
                self.assertFalse(client.get("/api/auth").json()["setup_required"])
                route = "/api/auth/key"
                self.assertEqual(client.put(route, json={"key": "replacement-admin"}).status_code, 401)
                self.assertEqual(client.put(route, headers=self.headers("wrong-key"), json={"key": "replacement-admin"}).status_code, 401)
                self.assertEqual(client.put(route, headers=self.headers("original-env-seed"), json={"key": "replacement-admin"}).status_code, 200)
                self.assertEqual(client.get("/api/status", headers=self.headers("original-env-seed")).status_code, 401)
                self.assertEqual(client.get("/api/status", headers=self.headers("replacement-admin")).status_code, 200)
            with TestClient(self.app()) as client:
                self.assertFalse(client.get("/api/auth").json()["setup_required"])
                self.assertEqual(client.get("/api/status", headers=self.headers("replacement-admin")).status_code, 200)
                self.assertEqual(client.get("/api/status", headers=self.headers("original-env-seed")).status_code, 401)

    def test_only_one_initial_claim_wins_and_failed_write_keeps_old_key(self):
        store = Store(self.root / "data")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda key: store.set_admin_key(key, "", first=True), ["first-admin-key", "second-admin-key"]))
        self.assertEqual(sum(results), 1)
        original = store.setting("access_token")
        with store.connect() as db:
            db.execute("CREATE TRIGGER no_key_update BEFORE UPDATE ON settings WHEN OLD.key='access_token' BEGIN SELECT RAISE(ABORT, 'disk-write-test'); END")
        with self.assertRaises(Exception):
            store.set_admin_key("replacement-admin", original)
        self.assertEqual(store.setting("access_token"), original)
        self.assertTrue(store.setting("admin_key_set"))


if __name__ == "__main__":
    unittest.main()
