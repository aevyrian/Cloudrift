import ast
import asyncio
import sys
import sqlite3
import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "vendor" / "TikTokDownloader"
sys.path.insert(0, str(SOURCE))


class CertificateTests(unittest.TestCase):
    def test_all_request_paths_verify_certificates(self):
        from src.tools.session import create_client
        with patch("src.tools.session.AsyncSession") as session:
            create_client()
            self.assertIs(session.call_args.kwargs["verify"], True)
        for path in (SOURCE / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                if isinstance(node, ast.keyword) and node.arg == "verify":
                    self.assertFalse(isinstance(node.value, ast.Constant) and node.value.value is False, str(path))


def account_with_pages(pages):
    from src.interface.account import Account
    from contextlib import nullcontext
    account = Account.__new__(Account)
    account.finished, account.cursor, account.pages = False, 0, 20
    account.response, account.text, account.api = [], "测试博主", "https://example.com"
    account.log = Mock()
    account.progress_object = lambda: nullcontext(Mock())
    account.generate_params = lambda: {"msToken": "test"}
    account.request_data = AsyncMock(side_effect=pages)
    return account


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_failure_raises_but_empty_last_page_succeeds(self):
        from src.interface.template import API
        first = {"aweme_list": [{"aweme_id": "123"}], "max_cursor": 10, "has_more": 1}
        for failure in (None, {"status_code": 0}, {"aweme_list": [], "has_more": 1}):
            account = account_with_pages([first, failure])
            with self.assertRaises(RuntimeError):
                await API.run_batch(account, "aweme_list", cursor="max_cursor")
            self.assertEqual(len(account.response), 1)
        account = account_with_pages([first, {"aweme_list": [], "has_more": 0}])
        await API.run_batch(account, "aweme_list", cursor="max_cursor")
        self.assertTrue(account.finished)
        self.assertEqual(len(account.response), 1)

    async def test_repeated_cursor_or_three_stale_pages_abort(self):
        from src.interface.account import Account
        cases = [
            [{"aweme_list": [{"aweme_id": "1"}], "max_cursor": 10, "has_more": 1},
             {"aweme_list": [{"aweme_id": "2"}], "max_cursor": 10, "has_more": 1}],
            [{"aweme_list": [{"aweme_id": "1"}], "max_cursor": i, "has_more": 1} for i in range(1, 5)],
        ]
        for pages in cases:
            account = account_with_pages(pages)
            with self.assertRaisesRegex(RuntimeError, "游标|新作品"):
                await Account.run_batch(account, callback=AsyncMock())
            self.assertLessEqual(account.request_data.await_count, 4)
        account = account_with_pages([{"aweme_list": [{"aweme_id": "1"}], "max_cursor": 0, "has_more": 1}])
        async def date_stop():
            account.finished = True
        with self.assertRaisesRegex(RuntimeError, "游标"):
            await Account.run_batch(account, callback=date_stop)


class StreamResponse:
    url = "https://media.example/video"

    def __init__(self, status, body, headers=None):
        self.status_code, self.body = status, body
        self.headers = {"Content-Length": str(len(body)), "Content-Type": "video/mp4", **(headers or {})}

    def raise_for_status(self):
        pass

    async def aiter_content(self, size=None):
        yield self.body

    async def aclose(self):
        pass


def downloader_for(response):
    from src.downloader.download import Downloader
    downloader = Downloader.__new__(Downloader)
    downloader.client = SimpleNamespace(request=AsyncMock(return_value=response))
    downloader.headers, downloader.max_retry, downloader.max_size = {}, 0, 0
    downloader.chunk, downloader.truncate = 1024, 100
    downloader.log, downloader.console = Mock(), Mock()
    downloader.semaphore = asyncio.Semaphore(1)
    downloader.recorder = SimpleNamespace(update_id=AsyncMock(), delete_id=AsyncMock())
    return downloader


async def fetch_file(downloader, temp, actual):
    count = SimpleNamespace(downloaded_video=set(), downloaded_image=set(), downloaded_live=set())
    return await downloader.request_file("https://media.example/video", temp, actual, "【视频】测试", "123", "mp4", count, Mock())


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_requires_exact_range_and_complete_length(self):
        cases = [
            (200, b"abcdef", {}, True),
            (206, b"def", {"Content-Range": "bytes 3-5/6"}, True),
            (206, b"def", {"Content-Range": "bytes 2-4/6"}, False),
            (206, b"def", {}, False),
            (200, b"abc", {"Content-Length": "6"}, False),
        ]
        for status, body, headers, expected in cases:
            with self.subTest(status=status, headers=headers), tempfile.TemporaryDirectory() as tmp:
                temp, actual = Path(tmp) / ".video.mp4.part", Path(tmp) / "video.mp4"
                temp.write_bytes(b"abc")
                downloader = downloader_for(StreamResponse(status, body, headers))
                self.assertEqual(await fetch_file(downloader, temp, actual), expected)
                if expected:
                    self.assertEqual(actual.read_bytes(), b"abcdef")
                else:
                    self.assertFalse(actual.exists())
                    downloader.recorder.update_id.assert_not_awaited()

    async def test_staging_is_in_target_directory_and_failures_propagate(self):
        from src.downloader.download import Downloader
        from app.core import Core
        from contextlib import nullcontext
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "media" / "video.mp4"
            actual.parent.mkdir()
            old_cache = root / "other-filesystem" / "old-cache"
            downloader = downloader_for(StreamResponse(200, b"abcdef"))
            replace = Path.replace
            def atomic(cache, destination):
                self.assertEqual(cache.parent, actual.parent)
                self.assertTrue(cache.name.endswith(".part"))
                self.assertFalse(actual.exists())
                return replace(cache, destination)
            with patch.object(Path, "replace", autospec=True, side_effect=atomic) as renamed:
                self.assertIs(await fetch_file(downloader, old_cache, actual), True)
                renamed.assert_called_once()
            self.assertEqual(actual.read_bytes(), b"abcdef")
            self.assertFalse(old_cache.exists())
            downloader.request_file = AsyncMock(return_value=False)
            self.assertIs(await downloader.downloader_chart([("fake",)], SimpleNamespace(), nullcontext(Mock())), False)
            core = Core(root, root / "data", root / "media")
            core.ready = True
            async def failed_download(*args, **kwargs):
                directory = root / "media" / "作品" / "7431234567890123456"
                directory.mkdir(parents=True)
                (directory / "video.mp4").write_bytes(b"untrusted-output")
                return False
            core.parser = SimpleNamespace(downloader=SimpleNamespace(recorder=SimpleNamespace(delete_id=AsyncMock()), run=failed_download))
            with self.assertRaisesRegex(RuntimeError, "下载"):
                await core.download({"id": "7431234567890123456", "type": "视频", "downloads": "url"})


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_locked_database_and_full_disk_do_not_kill_queue(self):
        from app.store import Store
        from app.main import JobService
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "data")
            task = store.create_task("7431234567890123456", "detail")
            core = SimpleNamespace(start=AsyncMock(), ready=True, error="")
            service = JobService(store, core, Path(tmp) / "media")
            service.retry_delay = 0
            calls = []
            async def process_one():
                calls.append(1)
                if len(calls) == 1:
                    store.claim_task()
                    raise sqlite3.OperationalError("database is locked")
                if len(calls) == 2:
                    raise OSError(errno.ENOSPC, "No space left on device")
                service.running = False
                return True
            service.process_one = process_one
            await service.run()
            self.assertEqual(len(calls), 3)
            self.assertEqual(store.task(task["id"])["status"], "interrupted")

    async def test_health_detects_dead_worker(self):
        from fastapi.testclient import TestClient
        from app.main import make_app
        with tempfile.TemporaryDirectory() as tmp:
            core = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), ready=True, error="")
            app = make_app(Path(tmp) / "data", Path(tmp) / "media", token="test", core=core)
            with TestClient(app) as client:
                self.assertTrue(client.get("/health").json()["worker_alive"])
                async def stop():
                    worker = app.state.service.worker
                    worker.cancel()
                    try:
                        await worker
                    except asyncio.CancelledError:
                        pass
                client.portal.call(stop)
                self.assertEqual(client.get("/health").status_code, 503)


class PermissionTests(unittest.TestCase):
    def test_private_data_repairs_existing_and_new_file_modes(self):
        from app.main import make_app
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            core_dir = root / "core"
            core_dir.mkdir(parents=True)
            cookie_file = core_dir / "settings.json"
            cookie_file.write_text('{"cookie":"test-only"}', encoding="utf-8")
            original = Path.chmod
            with patch.object(Path, "chmod", autospec=True, side_effect=original) as chmod:
                core = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), ready=True, error="")
                make_app(root, Path(tmp) / "media", token="test", core=core)
                calls = [(call.args[0], call.args[1]) for call in chmod.call_args_list]
                self.assertIn((root, 0o700), calls)
                self.assertIn((core_dir, 0o700), calls)
                self.assertIn((cookie_file, 0o600), calls)
                self.assertIn((root / "library.sqlite3", 0o600), calls)
            if os.name == "posix":
                for path in root.rglob("*"):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)


class TaskHistoryTests(unittest.TestCase):
    def test_history_limit_never_hides_actionable_tasks(self):
        from app.store import Store
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            pending = ["queued", "failed", "awaiting_confirmation", "interrupted"]
            rows = [(status, "detail", status, status, "2026", "2026") for status in pending]
            rows += [(str(i), "detail", str(i), "completed", "2026", "2026") for i in range(301)]
            with store.connect() as db:
                db.executemany("INSERT INTO tasks(id,kind,link,status,created_at,updated_at) VALUES (?,?,?,?,?,?)", rows)
            tasks = store.tasks()
            self.assertTrue(set(pending) <= {task["id"] for task in tasks})
            self.assertEqual(sum(task["status"] == "completed" for task in tasks), 300)


class DownloadHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_parameter_refresh_keeps_large_login_cookie_out_of_media_headers(self):
        from src.config.parameter import Parameter
        parameter = Parameter.__new__(Parameter)
        parameter.headers_download, parameter.headers_download_tiktok = {}, {}
        parameter.cookie_dict_tiktok, parameter.cookie_str_tiktok = {"tt_chain_token": "test-token"}, ""
        cookie = "sessionid=" + "test" * 3000
        parameter._Parameter__add_cookie = Mock(return_value=cookie)
        api_headers = {}
        for _ in range(2):
            await parameter._Parameter__update_cookie((), (api_headers, parameter.headers_download), {}, cookie)
            self.assertEqual(api_headers["Cookie"], cookie)
            self.assertEqual(parameter.headers_download["Cookie"], "dy_swidth=1536; dy_sheight=864")
            self.assertEqual(parameter.headers_download_tiktok["Cookie"], "tt_chain_token=test-token")
