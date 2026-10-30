import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import JobService, make_app
from app.store import Store
from tests.test_backend import FakeCore, WORK_ID


class DownloadFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_download_finishes_without_confirmation_or_subscription(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "data")
            service = JobService(store, FakeCore(root / "media"), root / "media")
            task = store.create_task(WORK_ID, "detail")
            await service.process_one()
            self.assertEqual(store.task(task["id"])["status"], "completed")
            self.assertEqual(store.stats()["works"], 1)
            self.assertEqual(store.authors(), [])


class FollowupTests(unittest.TestCase):
    def test_author_frequency_is_opt_in_and_all_three_choices_are_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(root / "data", root / "media", token="test-admin-key", core=FakeCore(root / "media"))
            headers = {"Authorization": "Bearer test-admin-key"}
            with TestClient(app) as client:
                first = client.post("/api/authors", headers=headers, json={"url": "https://www.douyin.com/user/onlyonce"}).json()
                self.assertFalse(first["enabled"])
                self.assertEqual(app.state.store.due_authors(), [])
                for frequency, minutes in (("daily", 1440), ("weekly", 10080), ("monthly", 43200)):
                    response = client.patch(f"/api/authors/{first['id']}", headers=headers, json={"frequency": frequency})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.json()["enabled"])
                    self.assertEqual(response.json()["interval_minutes"], minutes)
                    self.assertEqual(app.state.store.due_authors(), [])
                response = client.patch(f"/api/authors/{first['id']}", headers=headers, json={"frequency": "once"})
                self.assertFalse(response.json()["enabled"])
                self.assertEqual(client.patch(f"/api/authors/{first['id']}", headers=headers, json={"frequency": "hourly"}).status_code, 422)

    def test_monthly_uses_next_calendar_month_and_handles_year_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.save_author("monthly", "https://www.douyin.com/user/monthly", enabled=True, interval_minutes=43200)
            for current, expected in ((datetime(2027, 1, 31, 10), datetime(2027, 2, 28, 10)),
                                      (datetime(2028, 1, 31, 10), datetime(2028, 2, 29, 10)),
                                      (datetime(2027, 12, 15, 10), datetime(2028, 1, 15, 10))):
                with patch("app.store.time.time", return_value=current.timestamp()):
                    store.mark_author_scheduled("monthly", reset=True)
                result=store.author('monthly')
                self.assertEqual(result['schedule_until'], expected.timestamp()+60)
                self.assertGreater(result['next_check'], current.timestamp())
                self.assertLess(result['next_check'], result['schedule_until'])

    def test_search_finds_description_author_and_work_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.save_work({"id": WORK_ID, "desc": "海边日落", "nickname": "Cloud Creator", "sec_uid": "creator"}, ["one.jpg"])
            store.save_work({"id": "7654321000000000000", "desc": "城市夜晚", "nickname": "Other"}, ["two.jpg"])
            for query in ("日落", "Cloud", WORK_ID):
                result = store.library(q=query)
                self.assertEqual([item["id"] for item in result["items"]], [WORK_ID])
            self.assertEqual(store.library(q="没有这个作品")["total"], 0)


if __name__ == "__main__":
    unittest.main()
