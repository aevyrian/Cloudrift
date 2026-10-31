import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import (CONFIRM_REASON_COUNT, CONFIRM_REASON_QUALITY, FIRST_HISTORY_COUNT_LIMIT,
                      QUALITY_CONFIRM_MESSAGE, JobService, count_media_items, make_app, task_history_flags)
from app.store import Store
from fastapi.testclient import TestClient
from tests.test_backend import FakeCore


AUTHOR_URL = "https://www.douyin.com/user/abc"


def media_item(index, type_="视频"):
    return {"id": str(7431234567890000000 + index), "type": type_, "desc": f"作品{index}",
            "nickname": "测试作者", "downloads": "https://media.example/video", "width": 1080,
            "height": 1920, "create_time": "2026-10-22", "create_timestamp": index}


class BulkCore(FakeCore):
    def __init__(self, media, items):
        super().__init__(media)
        self.items = list(items)

    async def parse(self, link, kind, earliest=None):
        return list(self.items)


class MediaCountTests(unittest.TestCase):
    def test_counts_video_album_and_live_only(self):
        items = [
            {"id": "7431234567890123456", "type": "视频"},
            {"id": "7431234567890123457", "type": "图集"},
            {"id": "7431234567890123458", "type": "实况"},
            {"id": "7431234567890123459", "type": "图文"},
            {"id": "7431234567890123460", "type": "直播"},
            {"id": "7431234567890123461"},
        ]
        self.assertEqual(count_media_items(items), 3)

    def test_empty_and_non_media_lists_count_zero(self):
        self.assertEqual(count_media_items([]), 0)
        self.assertEqual(count_media_items([{"id": "7431234567890123456", "type": "图文"}]), 0)


class HistoryFlagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "data")
        self.url = "https://www.douyin.com/user/abc"

    def test_first_author_task_is_first_history(self):
        task = self.store.create_task(self.url, "author")
        self.assertEqual(task_history_flags(task, self.store), (True, False))

    def test_completed_author_task_is_update(self):
        task = self.store.create_task(self.url, "author")
        self.store.update_task(task["id"], status="completed")
        again = self.store.create_task(self.url, "author")
        self.assertEqual(task_history_flags(again, self.store), (False, True))

    def test_detail_task_is_neither_first_history_nor_update(self):
        task = self.store.create_task("7431234567890123456", "detail")
        self.assertEqual(task_history_flags(task, self.store), (False, False))


class ProcessTaskHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_task_derives_update_flag_from_history_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "data")
            service = JobService(store, FakeCore(root / "media"), root / "media")
            store.create_task("https://www.douyin.com/user/abc", "author")
            with patch("app.main.task_history_flags", return_value=(False, True)) as flags, \
                    patch("app.main.time.time", return_value=1000), \
                    patch("app.main.secrets.randbelow", return_value=0):
                await service.process_one()
            flags.assert_called_once()
            # is_update=True selects the 2-5 minute update pause.
            self.assertEqual(store.setting("queue_resume_at"), 1120)


class FirstHistoryCountRuleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "data")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def service(self, items):
        core = BulkCore(self.root / "media", items)
        return JobService(self.store, core, self.root / "media"), core

    async def test_first_history_at_limit_downloads_without_count_confirmation(self):
        service, _ = self.service([media_item(i) for i in range(FIRST_HISTORY_COUNT_LIMIT)])
        service.saved = lambda item, legacy_cache=None: True
        job = self.store.create_task(AUTHOR_URL, "author")
        await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["confirm_reason"], "")
        self.assertFalse(task["accept_count"])

    async def test_unsupported_types_do_not_count_toward_limit(self):
        items = [media_item(i) for i in range(FIRST_HISTORY_COUNT_LIMIT)]
        items += [media_item(5000 + i, "图文") for i in range(20)]
        service, _ = self.service(items)
        service.saved = lambda item, legacy_cache=None: True
        job = self.store.create_task(AUTHOR_URL, "author")
        await service.process_one()
        self.assertEqual(self.store.task(job["id"])["status"], "completed")

    async def test_first_history_over_limit_awaits_distinct_count_confirmation(self):
        service, core = self.service(
            [media_item(i) for i in range(FIRST_HISTORY_COUNT_LIMIT + 1)])
        job = self.store.create_task(AUTHOR_URL, "author")
        await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "awaiting_confirmation")
        self.assertEqual(task["confirm_reason"], CONFIRM_REASON_COUNT)
        self.assertIn(str(FIRST_HISTORY_COUNT_LIMIT + 1), task["message"])
        self.assertIn("数量", task["message"])
        self.assertNotEqual(task["message"], QUALITY_CONFIRM_MESSAGE)
        self.assertFalse(task["accept_count"])
        self.assertEqual(core.download_calls, 0)

    async def test_count_confirmation_continues_same_task(self):
        service, core = self.service(
            [media_item(i) for i in range(FIRST_HISTORY_COUNT_LIMIT + 1)])
        job = self.store.create_task(AUTHOR_URL, "author")
        await service.process_one()
        service.confirm(job["id"])
        confirmed = self.store.task(job["id"])
        self.assertEqual(confirmed["status"], "queued")
        self.assertTrue(confirmed["accept_count"])
        self.assertEqual(confirmed["confirm_reason"], "")
        service.saved = lambda item, legacy_cache=None: True
        await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual([item["id"] for item in self.store.tasks()], [job["id"]])
        self.assertEqual(core.download_calls, 0)

    async def test_update_task_over_limit_is_not_gated(self):
        previous = self.store.create_task(AUTHOR_URL, "author")
        self.store.update_task(previous["id"], status="completed")
        service, _ = self.service(
            [media_item(i) for i in range(FIRST_HISTORY_COUNT_LIMIT + 1)])
        service.saved = lambda item, legacy_cache=None: True
        job = self.store.create_task(AUTHOR_URL, "author")
        await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["confirm_reason"], "")
        self.assertFalse(task["accept_count"])

    async def test_over_limit_with_existing_saved_items_is_treated_as_update(self):
        service, _ = self.service([media_item(i) for i in range(FIRST_HISTORY_COUNT_LIMIT + 1)])
        service.saved = lambda item, legacy_cache=None: True
        job = self.store.create_task(AUTHOR_URL, "author")
        await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["confirm_reason"], "")
        self.assertFalse(task["accept_count"])

    async def test_count_and_quality_confirmations_stay_distinct(self):
        self.store.set_setting("quality_policy", "confirm")
        service, core = self.service(
            [media_item(i) for i in range(FIRST_HISTORY_COUNT_LIMIT + 1)])
        job = self.store.create_task(AUTHOR_URL, "author")
        await service.process_one()
        self.assertEqual(self.store.task(job["id"])["confirm_reason"], CONFIRM_REASON_COUNT)

        service.confirm(job["id"])
        await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "awaiting_confirmation")
        self.assertEqual(task["confirm_reason"], CONFIRM_REASON_QUALITY)
        self.assertTrue(task["accept_count"])
        self.assertFalse(task["accept_quality"])
        self.assertEqual(task["message"], QUALITY_CONFIRM_MESSAGE)

        service.confirm(job["id"])
        task = self.store.task(job["id"])
        self.assertTrue(task["accept_quality"])
        self.assertEqual(task["confirm_reason"], "")
        service.saved = lambda item, legacy_cache=None: True
        await service.process_one()
        self.assertEqual(self.store.task(job["id"])["status"], "completed")
        self.assertEqual(core.download_calls, 0)


class CountConfirmationApiTests(unittest.TestCase):
    def test_api_exposes_count_confirmation_text_and_confirm_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(root / "data", root / "media", token="test-token",
                           core=FakeCore(root / "media"))
            store = app.state.store
            job = store.create_task(AUTHOR_URL, "author")
            message = (f"已解析首次历史作品：视频和图集共 {FIRST_HISTORY_COUNT_LIMIT + 1} 个，"
                       f"超过 {FIRST_HISTORY_COUNT_LIMIT} 个；请确认数量后继续下载")
            store.update_task(job["id"], status="awaiting_confirmation",
                              confirm_reason=CONFIRM_REASON_COUNT, message=message)
            headers = {"Authorization": "Bearer test-token"}
            client = TestClient(app)  # No lifespan: the download worker never starts.
            try:
                tasks = client.get("/api/tasks", headers=headers).json()
                exposed = next(item for item in tasks if item["id"] == job["id"])
                self.assertEqual(exposed["status"], "awaiting_confirmation")
                self.assertEqual(exposed["confirm_reason"], CONFIRM_REASON_COUNT)
                self.assertEqual(exposed["message"], message)
                self.assertIn(str(FIRST_HISTORY_COUNT_LIMIT), exposed["message"])
                self.assertIn("数量", exposed["message"])

                confirmed = client.post(f"/api/tasks/{job['id']}/confirm", headers=headers).json()
                self.assertEqual(confirmed["status"], "queued")
                self.assertTrue(confirmed["accept_count"])
                self.assertEqual(confirmed["confirm_reason"], "")
                self.assertIn("数量", confirmed["message"])
                self.assertNotEqual(confirmed["message"], QUALITY_CONFIRM_MESSAGE)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
