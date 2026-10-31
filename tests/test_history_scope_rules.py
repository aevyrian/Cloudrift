import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import JobService, count_media_items, task_history_flags
from app.store import Store
from tests.test_backend import FakeCore


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


if __name__ == "__main__":
    unittest.main()
