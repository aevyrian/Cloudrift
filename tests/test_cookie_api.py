import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import make_app


class FakeCore:
    ready = False
    error = ""

    async def start(self, cookie="", download_dir=""):
        self.ready = True

    async def close(self):
        self.ready = False


class CookieSyncApiTests(unittest.TestCase):
    def test_authenticated_request_and_redacted_status(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_app(root / "data", root / "media", token="test-admin-key", core=FakeCore())
            headers = {"Authorization": "Bearer test-admin-key"}
            with TestClient(app) as client:
                self.assertEqual(client.post("/api/cookie-sync").status_code, 401)
                response = client.post("/api/cookie-sync", headers=headers)
                self.assertEqual(response.status_code, 200)
                job = response.json()
                self.assertRegex(job["id"], r"^[A-Za-z0-9_-]{43}$")
                self.assertEqual(job["status"], "waiting")
                self.assertEqual(response.headers.get("cache-control"), "no-store")
                path = "/api/cookie-sync/" + job["id"]
                self.assertEqual(client.get(path).status_code, 401)
                status = client.get(path, headers=headers)
                self.assertEqual(status.status_code, 200)
                self.assertNotIn("test-admin-key", status.text)
                self.assertNotIn("cookie", status.json())
                denied = client.post(path, headers=headers, json={"error": "permission_denied"})
                self.assertEqual(denied.status_code, 202)
                self.assertEqual(denied.json()["status"], "failed")
                self.assertEqual(denied.json()["reason"], "permission_denied")
                self.assertFalse(app.state.store.setting("cookie", ""))

    def test_candidate_validation_and_token_rotation_do_not_echo_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_app(root / "data", root / "media", token="test-admin-key", core=FakeCore())
            headers = {"Authorization": "Bearer test-admin-key"}
            candidate = "sessionid=secret-candidate-for-test"
            with TestClient(app) as client:
                job = client.post("/api/cookie-sync", headers=headers).json()
                path = "/api/cookie-sync/" + job["id"]
                bad = client.post(path, headers=headers, json={"cookie": candidate, "error": "read_failed"})
                self.assertEqual(bad.status_code, 422)
                self.assertNotIn(candidate, bad.text)
                self.assertEqual(client.post(path, headers=headers, json={"unexpected": candidate}).status_code, 422)
                self.assertEqual(client.post(path, json={"cookie": candidate}).status_code, 401)
                with patch.object(app.state.cookie_sync, "submit", return_value={"id": job["id"], "status": "validating"}) as submit:
                    accepted = client.post(path, headers=headers, json={"cookie": candidate})
                    self.assertEqual(accepted.status_code, 202)
                    self.assertNotIn(candidate, accepted.text)
                    self.assertEqual(submit.call_args.kwargs["cookie"], candidate)
                app.state.store.set_setting("access_token", "new-test-admin-key")
                self.assertEqual(client.get(path, headers=headers).status_code, 401)
                self.assertEqual(client.get(path, headers={"Authorization": "Bearer new-test-admin-key"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
