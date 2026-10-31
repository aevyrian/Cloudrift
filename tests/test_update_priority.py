import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.main import UPDATE_EARLIEST_PADDING_DAYS, JobService, update_earliest
from app.store import Store
from tests.test_backend import FakeCore, WORK_ID


AUTHOR_A = "https://www.douyin.com/user/aaa"
AUTHOR_B = "https://www.douyin.com/user/bbb"


class ClaimOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "data")

    def complete(self, task_id):
        self.store.update_task(task_id, status="completed")

    def test_update_author_task_is_claimed_before_older_first_history(self):
        first = self.store.create_task(AUTHOR_A, "author")
        self.complete(first["id"])
        older_history = self.store.create_task(AUTHOR_B, "author")
        update = self.store.create_task(AUTHOR_A, "author")
        self.assertEqual(self.store.claim_task()["id"], update["id"])
        self.assertEqual(self.store.claim_task()["id"], older_history["id"])

    def test_first_history_tasks_keep_fifo_order(self):
        first = self.store.create_task(AUTHOR_A, "author")
        second = self.store.create_task(AUTHOR_B, "author")
        self.assertEqual(self.store.claim_task()["id"], first["id"])
        self.assertEqual(self.store.claim_task()["id"], second["id"])

    def test_author_last_completed_returns_latest_completed_check(self):
        first = self.store.create_task(AUTHOR_A, "author")
        self.complete(first["id"])
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?",
                       ("2026-10-01T00:00:00+00:00", first["id"]))
        self.assertEqual(self.store.author_last_completed(AUTHOR_A), "2026-10-01T00:00:00+00:00")
        self.assertIsNone(self.store.author_last_completed(AUTHOR_B))


class EarliestCore(FakeCore):
    def __init__(self, media):
        super().__init__(media)
        self.earliest = []

    async def parse(self, link, kind, earliest=None):
        self.earliest.append(earliest)
        return await super().parse(link, kind, earliest)


class UpdateEarliestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "data")
        self.core = EarliestCore(self.root / "media")
        self.service = JobService(self.store, self.core, self.root / "media")

    async def run_task(self, task, now):
        with patch("app.main.time.time", return_value=now), \
                patch("app.main.secrets.randbelow", return_value=0):
            await self.service.process_one()
        return self.store.task(task["id"])

    async def test_first_history_scans_without_earliest(self):
        job = self.store.create_task(AUTHOR_A, "author")
        await self.run_task(job, 1000)
        self.assertEqual(self.core.earliest, [None])

    async def test_detail_task_scans_without_earliest(self):
        job = self.store.create_task(WORK_ID, "detail")
        await self.run_task(job, 1000)
        self.assertEqual(self.core.earliest, [None])

    async def test_update_uses_elapsed_days_with_padding(self):
        first = self.store.create_task(AUTHOR_A, "author")
        self.store.update_task(first["id"], status="completed")
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?",
                       ("2026-10-01T00:00:00+00:00", first["id"]))
        update = self.store.create_task(AUTHOR_A, "author")
        now = datetime(2026, 10, 11, tzinfo=timezone.utc).timestamp()
        await self.run_task(update, now)
        self.assertEqual(self.core.earliest, [10 + UPDATE_EARLIEST_PADDING_DAYS])

    async def test_update_with_unknown_last_check_scans_full_history(self):
        first = self.store.create_task(AUTHOR_A, "author")
        self.store.update_task(first["id"], status="completed")
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?", ("not-a-time", first["id"]))
        update = self.store.create_task(AUTHOR_A, "author")
        await self.run_task(update, 1000)
        self.assertEqual(self.core.earliest, [None])

    def test_helper_returns_none_for_non_author_task(self):
        job = self.store.create_task(WORK_ID, "detail")
        self.assertIsNone(update_earliest(job, self.store))

    def test_helper_never_returns_a_zero_day_window(self):
        first = self.store.create_task(AUTHOR_A, "author")
        self.store.update_task(first["id"], status="completed")
        with self.store.connect() as db:
            db.execute("UPDATE tasks SET updated_at=? WHERE id=?",
                       ("2026-10-11T00:00:00+00:00", first["id"]))
        update = self.store.create_task(AUTHOR_A, "author")
        now = datetime(2026, 10, 11, tzinfo=timezone.utc).timestamp()
        self.assertGreaterEqual(update_earliest(update, self.store, now=now), 1)
