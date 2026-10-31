import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "TikTokDownloader"))

from app.core import Core, RateLimited
from app.main import (RATE_LIMIT_COMPLETED_THRESHOLD, RATE_LIMIT_PAUSE_SECONDS, JobService,
                      make_app)
from app.store import Store
from fastapi.testclient import TestClient
from tests.test_backend import FakeCore, WORK_ID


AUTHOR_URL = "https://www.douyin.com/user/rate-limit"


def media_item(index):
    return {"id": str(7431234567890000000 + index), "type": "视频", "desc": f"作品{index}",
            "nickname": "测试作者", "downloads": "https://media.example/video",
            "create_time": "2026-10-22"}


def core_at(root):
    core = Core(root, root / "data", root / "media")
    core.ready = True
    return core


class CoreRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_converts_rate_limit_message_to_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = core_at(root)

            async def links_run(link, kind):
                return ["MS4wLjABAAAA_author"]

            async def handle_account(*args, **kwargs):
                raise RuntimeError("访问过于频繁，请稍后重试")

            core.parser = SimpleNamespace(links=SimpleNamespace(run=links_run),
                                          handle_account=handle_account)
            with self.assertRaises(RateLimited):
                await core.parse(AUTHOR_URL, "author")

    async def test_parse_keeps_unrelated_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = core_at(root)

            async def links_run(link, kind):
                return ["MS4wLjABAAAA_author"]

            async def handle_account(*args, **kwargs):
                raise RuntimeError("博主分页响应无效，未获取完整作品，可重试")

            core.parser = SimpleNamespace(links=SimpleNamespace(run=links_run),
                                          handle_account=handle_account)
            try:
                await core.parse(AUTHOR_URL, "author")
            except RateLimited:
                self.fail("普通错误不应被识别为限流")
            except RuntimeError:
                pass

    async def test_download_converts_rate_limit_error_to_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = core_at(root)

            class Downloader:
                last_error = ""

                def __init__(self):
                    self.recorder = SimpleNamespace(delete_id=self.delete_id)

                async def delete_id(self, work_id):
                    pass

                async def run(self, items, type_="detail", tiktok=False):
                    self.last_error = ("媒体服务器返回 HTTP 429，可能是地址过期、访问限制或请求过多，"
                                       "请稍后重试并检查Cookie")
                    return False

            core.parser = SimpleNamespace(downloader=Downloader())
            with self.assertRaises(RateLimited):
                await core.download({"id": WORK_ID, "type": "视频",
                                     "downloads": "https://media.example/video"})

    async def test_download_keeps_unrelated_failure_as_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = core_at(root)

            class Downloader:
                last_error = ""

                def __init__(self):
                    self.recorder = SimpleNamespace(delete_id=self.delete_id)

                async def delete_id(self, work_id):
                    pass

                async def run(self, items, type_="detail", tiktok=False):
                    self.last_error = "媒体网络连接失败（Timeout）"
                    return False

            core.parser = SimpleNamespace(downloader=Downloader())
            try:
                await core.download({"id": WORK_ID, "type": "视频",
                                     "downloads": "https://media.example/video"})
            except RateLimited:
                self.fail("普通下载失败不应被识别为限流")
            except RuntimeError:
                pass


class RateLimitCore(FakeCore):
    """Downloads successfully, then raises RateLimited at fail_after."""

    def __init__(self, media, items, fail_after):
        super().__init__(media)
        self.items = list(items)
        self.fail_after = fail_after

    async def parse(self, link, kind, earliest=None):
        return list(self.items)

    async def download(self, item):
        if self.download_calls >= self.fail_after:
            raise RateLimited("媒体服务器返回 HTTP 429，请求过多，请稍后重试")
        return await super().download(item)


class RateLimitTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "data")

    def service(self, items, fail_after):
        core = RateLimitCore(self.root / "media", items, fail_after)
        return JobService(self.store, core, self.root / "media"), core

    async def test_author_pauses_after_more_than_600_and_schedules_resume(self):
        items = [media_item(i) for i in range(RATE_LIMIT_COMPLETED_THRESHOLD + 2)]
        service, _ = self.service(items, RATE_LIMIT_COMPLETED_THRESHOLD + 1)
        job = self.store.create_task(AUTHOR_URL, "author")
        with patch("app.main.time.time", return_value=1000), \
                patch("app.main.secrets.randbelow", return_value=0):
            await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "paused")
        self.assertEqual(task["completed"], RATE_LIMIT_COMPLETED_THRESHOLD + 1)
        self.assertEqual(task["resume_at"], 1000 + RATE_LIMIT_PAUSE_SECONDS)
        self.assertIn("自动继续", task["message"])
        self.assertEqual(self.store.stats()["works"], RATE_LIMIT_COMPLETED_THRESHOLD + 1)

    async def test_rate_limit_at_600_completed_fails_without_pausing(self):
        items = [media_item(i) for i in range(RATE_LIMIT_COMPLETED_THRESHOLD + 1)]
        service, _ = self.service(items, RATE_LIMIT_COMPLETED_THRESHOLD)
        job = self.store.create_task(AUTHOR_URL, "author")
        with patch("app.main.time.time", return_value=1000), \
                patch("app.main.secrets.randbelow", return_value=0):
            await service.process_one()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["resume_at"], 0)
        self.assertEqual(task["completed"], RATE_LIMIT_COMPLETED_THRESHOLD)
        self.assertIn("请求过多", task["message"])

    async def test_scheduler_requeues_rate_limited_task_after_one_hour(self):
        service, _ = self.service([], 0)
        job = self.store.create_task(AUTHOR_URL, "author")
        resume_at = 1000 + RATE_LIMIT_PAUSE_SECONDS
        self.store.update_task(job["id"], status="paused", resume_at=resume_at,
                               completed=42, message="限流暂停")
        with patch("app.main.time.time", return_value=resume_at - 1):
            service.schedule()
        self.assertEqual(self.store.task(job["id"])["status"], "paused")
        with patch("app.main.time.time", return_value=resume_at):
            service.schedule()
        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["resume_at"], 0)
        self.assertEqual(task["completed"], 0)

    def test_global_queue_pause_keeps_rate_limited_task_paused(self):
        service, _ = self.service([], 0)
        job = self.store.create_task(AUTHOR_URL, "author")
        self.store.update_task(job["id"], status="paused", resume_at=1000)
        self.store.set_setting("queue_paused", True)
        self.assertEqual(self.store.resume_rate_limited(2000), [])
        self.assertEqual(self.store.task(job["id"])["status"], "paused")

    def test_explicit_pause_cancels_pending_auto_resume(self):
        service, _ = self.service([], 0)
        job = self.store.create_task(AUTHOR_URL, "author")
        self.store.update_task(job["id"], status="paused", resume_at=9999)
        paused = self.store.pause_task(job["id"])
        self.assertEqual(paused["resume_at"], 0)
        self.assertEqual(self.store.resume_rate_limited(10000), [])
        self.assertEqual(self.store.task(job["id"])["status"], "paused")

    def test_manual_resume_clears_rate_limit_resume_time(self):
        service, _ = self.service([], 0)
        job = self.store.create_task(AUTHOR_URL, "author")
        self.store.update_task(job["id"], status="paused", resume_at=9999)
        resumed = self.store.resume_task(job["id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["resume_at"], 0)

    def test_resume_queue_clears_rate_limit_resume_time(self):
        service, _ = self.service([], 0)
        job = self.store.create_task(AUTHOR_URL, "author")
        self.store.update_task(job["id"], status="paused", resume_at=9999)
        resumed_ids = self.store.resume_queue()
        self.assertEqual(resumed_ids, [job["id"]])
        resumed = self.store.task(job["id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["resume_at"], 0)

    async def test_resumed_task_skips_saved_works_and_completes(self):
        items = [media_item(i) for i in range(RATE_LIMIT_COMPLETED_THRESHOLD + 2)]
        service, core = self.service(items, RATE_LIMIT_COMPLETED_THRESHOLD + 1)
        job = self.store.create_task(AUTHOR_URL, "author")
        with patch("app.main.time.time", return_value=1000), \
                patch("app.main.secrets.randbelow", return_value=0):
            await service.process_one()
        self.assertEqual(self.store.task(job["id"])["status"], "paused")

        self.store.resume_rate_limited(1000 + RATE_LIMIT_PAUSE_SECONDS)
        self.assertEqual(self.store.task(job["id"])["status"], "queued")
        core.fail_after = len(items) + 1
        with patch("app.main.time.time", return_value=5000), \
                patch("app.main.secrets.randbelow", return_value=0):
            await service.process_one()

        task = self.store.task(job["id"])
        self.assertEqual(task["status"], "completed")
        self.assertEqual(self.store.stats()["works"], len(items))
        self.assertEqual(core.download_calls, len(items))


class RateLimitPauseApiTests(unittest.TestCase):
    def test_api_exposes_rate_limit_pause_message_and_resume_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(root / "data", root / "media", token="test-token",
                           core=FakeCore(root / "media"))
            store = app.state.store
            service = JobService(store, FakeCore(root / "media"), root / "media")
            job = store.create_task(AUTHOR_URL, "author")
            completed = RATE_LIMIT_COMPLETED_THRESHOLD + 1
            with patch("app.main.time.time", return_value=1000):
                resume_at = service.pause_rate_limited(
                    store.task(job["id"]), completed, RateLimited("访问过于频繁，请稍后重试"))
            self.assertEqual(resume_at, 1000 + RATE_LIMIT_PAUSE_SECONDS)

            headers = {"Authorization": "Bearer test-token"}
            client = TestClient(app)  # No lifespan: the download worker never starts.
            try:
                tasks = client.get("/api/tasks", headers=headers).json()
                exposed = next(item for item in tasks if item["id"] == job["id"])
                self.assertEqual(exposed["status"], "paused")
                self.assertEqual(exposed["completed"], completed)
                self.assertEqual(exposed["resume_at"], resume_at)
                self.assertIn(str(completed), exposed["message"])
                self.assertIn("自动继续", exposed["message"])
                self.assertIn("不会重复下载", exposed["message"])
            finally:
                client.close()
