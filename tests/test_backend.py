import asyncio
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from app.store import Store
from app.core import Core, download_directory, normalize_input, verified_files
from app.main import JobService, make_app
from fastapi.testclient import TestClient


WORK_ID = "7431234567890123456"


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_active_job_and_interrupted_restart(self):
        first = self.store.create_task("https://www.douyin.com/video/" + WORK_ID, "detail")
        again = self.store.create_task(first["link"], "detail")
        self.assertEqual(first["id"], again["id"])
        claimed = self.store.claim_task()
        self.assertEqual(claimed["status"], "parsing")
        self.store.recover()
        self.assertEqual(self.store.task(first["id"])["status"], "interrupted")
        self.assertIsNone(self.store.claim_task())

    def test_only_enabled_due_authors_are_scheduled(self):
        self.store.save_author("enabled", "https://www.douyin.com/user/abc", enabled=True, interval_minutes=5)
        self.store.save_author("disabled", "https://www.douyin.com/user/def", enabled=False, interval_minutes=5)
        due = self.store.due_authors()
        self.assertEqual([a["id"] for a in due], ["enabled"])
        self.store.mark_author_scheduled("enabled")
        self.assertEqual(self.store.due_authors(), [])

    def test_settings_survive_and_secrets_are_not_in_status(self):
        self.store.set_setting("cookie", "sessionid=private-test")
        self.assertEqual(Store(self.root).setting("cookie"), "sessionid=private-test")
        self.assertNotIn("private-test", str(self.store.stats()))


    def test_random_feed_pages_do_not_repeat_with_rowid_gaps(self):
        for i in range(25):
            self.store.save_work({'id':str(7431234567890123400+i),'nickname':'博主'}, ['v.mp4'])
        with self.store.connect() as db:
            db.execute('DELETE FROM works WHERE rowid IN (3,8,12)')
        found=[]; offset=0; ceiling=None
        for _ in range(10):
            page=self.store.library(offset=offset,limit=4,seed=123,ceiling=ceiling)
            found.extend(w['id'] for w in page['items']); offset=page['next_offset'];ceiling=page['ceiling']
            if offset>=ceiling: break
        self.assertEqual(len(found),22);self.assertEqual(len(set(found)),22)
        self.assertNotEqual(found[:4],[w['id'] for w in self.store.library(seed=456,limit=4)['items']])

    def test_favorites_survive_restart_and_resolve_old_work_alias(self):
        self.store.save_work({'id':'legacy-test','nickname':'博主'}, ['UID123_博主_发布作品/2025-12-01 12.30.00-视频-博主-作品.mp4'])
        self.store.link_work(WORK_ID,'legacy-test')
        self.store.set_favorite(WORK_ID,True)
        page=Store(self.root).library(favorite=True)
        self.assertEqual(page['total'],1);self.assertTrue(page['items'][0]['favorite'])
        self.store.set_favorite(WORK_ID,False)
        self.assertEqual(self.store.library(favorite=True)['total'],0)
        self.assertIsNotNone(self.store.work('legacy-test'))

    def test_author_history_includes_sender_downloads_without_subscribing(self):
        url='https://www.douyin.com/user/abc123'
        task=self.store.create_task(url,'author')
        self.store.update_task(task['id'],preview=[{'nickname':'真实昵称'}],status='completed')
        history=self.store.author_history()
        self.assertEqual(history[0]['name'],'真实昵称');self.assertFalse(history[0]['registered'])
        self.assertEqual(self.store.authors(),[])


class BoundaryTests(unittest.TestCase):
    def test_download_directory_stays_under_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertEqual(download_directory("new", root), root / "new")
            self.assertEqual(download_directory(str(root), root), root)
            with self.assertRaises(ValueError):
                download_directory("../outside", root)
        with self.assertRaises(ValueError):
            normalize_input("https://www.douyin.com/user/first https://www.douyin.com/user/second")

    def test_exact_id_and_share_text(self):
        self.assertEqual(normalize_input(WORK_ID, "auto"), ("detail", WORK_ID))
        kind, link = normalize_input("分享作品 https://www.douyin.com/note/" + WORK_ID, "auto")
        self.assertEqual(kind, "detail")
        self.assertIn(WORK_ID, link)
        self.assertEqual(normalize_input("https://www.douyin.com/user/abc123", "auto")[0], "author")

    def test_untrusted_host_credentials_and_partial_ids_rejected(self):
        for value in ["https://evil.example/video/" + WORK_ID,
                      "https://www.douyin.com.evil.example/video/" + WORK_ID,
                      "http://127.0.0.1:5666/", "12345",
                      "https://user:pass@www.douyin.com/video/" + WORK_ID]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_input(value, "auto")

    def test_incomplete_gallery_and_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "作品" / WORK_ID
            directory.mkdir(parents=True)
            (directory / (WORK_ID + "_1.jpg")).write_bytes(b"image")
            item = {"id": WORK_ID, "type": "图集", "downloads": ["a", "b"]}
            with self.assertRaises(RuntimeError):
                verified_files(root, item)
            (directory / (WORK_ID + "_2.jpg")).write_bytes(b"second")
            self.assertEqual(len(verified_files(root, item)), 2)


class FakeCore:
    ready = True
    error = ""

    def __init__(self, media):
        self.media = Path(media)
        self.download_calls = 0

    async def start(self, cookie="", download_dir=""):
        pass

    async def close(self):
        pass

    async def parse(self, link, kind, earliest=None):
        return [{"id": WORK_ID, "type": "视频", "desc": "测试<作品>", "nickname": "测试作者",
                 "sec_uid": "abc", "downloads": "https://media.example/video", "width": 1080,
                 "height": 1920, "create_time": "2026-10-22", "create_timestamp": 100}]

    async def download(self, item):
        self.download_calls += 1
        directory = self.media / "作品" / item["id"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (item["id"] + ".mp4")
        path.write_bytes(b"test-video")
        return [str(path.relative_to(self.media).as_posix())]


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "data")
        self.core = FakeCore(self.root / "media")
        self.service = JobService(self.store, self.core, self.root / "media")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_waits_for_quality_confirmation_and_records_only_saved_media(self):
        self.store.set_setting("quality_policy", "confirm")
        job = self.store.create_task(WORK_ID, "detail")
        await self.service.process_one()
        self.assertEqual(self.store.task(job["id"])["status"], "awaiting_confirmation")
        self.assertEqual(self.core.download_calls, 0)
        self.service.confirm(job["id"])
        await self.service.process_one()
        self.assertEqual(self.store.task(job["id"])["status"], "completed")
        self.assertEqual(self.store.stats()["works"], 1)
        self.assertEqual(self.core.download_calls, 1)
        self.store.create_task(WORK_ID, "detail", accept_quality=True)
        await self.service.process_one()
        self.assertEqual(self.core.download_calls, 1)

    async def test_download_failure_not_saved_and_can_retry(self):
        async def broken(item):
            raise RuntimeError("下载未完成")
        self.core.download = broken
        job = self.store.create_task(WORK_ID, "detail", accept_quality=True)
        await self.service.process_one()
        self.assertEqual(self.store.task(job["id"])["status"], "failed")
        self.assertEqual(self.store.stats()["works"], 0)
        self.service.retry(job["id"])
        self.assertEqual(self.store.task(job["id"])["status"], "queued")

    @patch('app.main.time.time', new=lambda: 1767229200)
    async def test_scheduled_update_adds_new_work_skips_existing_and_pause_survives_reload(self):
        from unittest.mock import patch
        self.store.set_setting('quality_policy', 'upstream')
        self.store.save_author('timer', 'https://www.douyin.com/user/timer', enabled=True, interval_minutes=5)
        self.service.schedule()
        await self.service.process_one()
        self.assertEqual(self.core.download_calls, 1)
        original_parse = self.core.parse
        async def new_post(link, kind, earliest=None):
            items = await original_parse(link, kind)
            return items + [{**items[0], 'id': '7431234567890123457'}]
        self.core.parse = new_post
        next_due = self.store.author('timer')['next_check']
        with patch('app.store.time.time', return_value=next_due - 1):
            self.service.schedule()
            self.assertFalse(await self.service.process_one())
        ready_at = max(next_due, self.store.setting('queue_resume_at')) + 1
        with patch('app.store.time.time', return_value=ready_at):
            self.service.schedule()
            await self.service.process_one()
        self.assertEqual(self.core.download_calls, 2)
        self.assertEqual(self.store.stats()['works'], 2)
        self.assertTrue(all(t['status'] == 'completed' for t in self.store.tasks()))
        self.store.patch_author('timer', enabled=False)
        reloaded = Store(self.root / 'data')
        self.assertFalse(reloaded.author('timer')['enabled'])
        with patch('app.store.time.time', return_value=next_due + 1000):
            self.service.schedule()
            self.assertFalse(await self.service.process_one())

    @patch('app.main.time.time', new=lambda: 1767229200)
    async def test_scheduling_still_runs_while_bulk_download_is_busy(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.core.download
        async def busy_download(item):
            started.set()
            await release.wait()
            return await original(item)
        self.core.download = busy_download
        self.store.create_task(WORK_ID, 'detail', accept_quality=True)
        worker = asyncio.create_task(self.service.run())
        try:
            await asyncio.wait_for(started.wait(), 2)
            self.store.save_author('due', 'https://www.douyin.com/user/due', enabled=True, interval_minutes=5)
            async with asyncio.timeout(1.5):
                while not any(t['author_id'] == 'due' for t in self.store.tasks()):
                    await asyncio.sleep(.01)
        finally:
            self.service.running = False
            release.set()
            await worker

    async def test_partial_core_gallery_retry_clears_stale_record(self):
        from types import SimpleNamespace
        deleted = []
        async def delete_id(work_id):
            deleted.append(work_id)
        async def download(items, **kwargs):
            if not deleted:
                return
            folder = self.root / "media" / "作品" / WORK_ID
            folder.mkdir(parents=True, exist_ok=True)
            for i in (1, 2):
                (folder / f"{WORK_ID}_{i}.jpg").write_bytes(b"image")
            return True
        core = Core(self.root, self.root / "data", self.root / "media")
        core.ready = True
        core.parser = SimpleNamespace(downloader=SimpleNamespace(recorder=SimpleNamespace(delete_id=delete_id), run=download))
        files = await core.download({"id": WORK_ID, "type": "图集", "downloads": ["one", "two"]})
        self.assertEqual(deleted, [WORK_ID])
        self.assertEqual(len(files), 2)


class ApiTests(unittest.TestCase):
    def test_sender_subscription_frequency_and_toggle_do_not_start_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=make_app(root/'data',root/'media',token='test-token',core=FakeCore(root/'media'))
            headers={'Authorization':'Bearer test-token'}
            with TestClient(app) as client:
                for frequency,minutes in [('daily',1440),('weekly',10080),('monthly',43200)]:
                    result=client.post('/api/authors',headers=headers,json={'url':'https://www.douyin.com/user/MS4wLjABAAAA_testauthor','name':'测试博主','frequency':frequency})
                    self.assertEqual(result.status_code,200);author=result.json()
                    self.assertTrue(author['enabled']);self.assertEqual(author['interval_minutes'],minutes)
                    self.assertGreater(author['next_check'],time.time())
                self.assertEqual(len(client.get('/api/authors',headers=headers).json()),1)
                updated=client.patch('/api/authors/'+author['id'],headers=headers,json={'frequency':'weekly'}).json()
                self.assertEqual(updated['interval_minutes'],10080);self.assertEqual(updated['name'],'测试博主');self.assertTrue(updated['enabled'])
                paused=client.patch('/api/authors/'+author['id'],headers=headers,json={'enabled':False}).json()
                self.assertFalse(paused['enabled']);self.assertEqual(app.state.store.tasks(),[])

    def test_playback_auth_cache_range_and_missing_original(self):
        from unittest.mock import AsyncMock, patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media';media.mkdir();original=media/'old.mp4';original.write_bytes(b'original')
            app=make_app(root/'data',media,token='test-token',core=FakeCore(media));store=app.state.store
            store.save_work({'id':WORK_ID,'type':'视频'},['old.mp4'])
            url=f'/api/media/{WORK_ID}/0';headers={'Authorization':'Bearer test-token'}
            with TestClient(app) as client:
                self.assertEqual(client.get(url+'/playback').status_code,401)
                self.assertEqual(client.get(url+'?compatible=true',headers=headers).status_code,409)
                cached=app.state.playback.target(original);cached.write_bytes(b'compatible-copy')
                with patch.object(app.state.playback,'prepare',AsyncMock(return_value={'status':'ready','mode':'compatible'})):
                    response=client.get(url+'/playback',headers=headers)
                    self.assertEqual(response.json()['url'],url+'?compatible=true')
                    self.assertEqual(response.headers['cache-control'],'no-store')
                response=client.get(url+'?compatible=true',headers={**headers,'Range':'bytes=0-3'})
                self.assertEqual(response.status_code,206);self.assertEqual(response.content,b'comp')
                with patch.object(app.state.playback,'prepare',AsyncMock(side_effect=RuntimeError('原文件无法读取'))):
                    response=client.get(url+'/playback',headers=headers)
                    self.assertEqual(response.json(),{'status':'failed','message':'原文件无法读取'})
                self.assertEqual(original.read_bytes(),b'original');self.assertEqual(store.stats()['works'],1)
                store.save_work({'id':'outside','type':'视频'},['../secret.mp4'])
                self.assertEqual(client.get('/api/media/outside/0/playback',headers=headers).status_code,404)
                original.unlink()
                self.assertEqual(client.get(url+'/playback',headers=headers).status_code,404)

    def test_auth_settings_and_media_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(root / "data", root / "media", token="test-only-token", core=FakeCore(root / "media"))
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                for resource in ('/','/app.js','/style.css','/douyin-send.user.js'):
                    self.assertEqual(client.get(resource).headers['cache-control'],'no-cache')
                self.assertEqual(client.get("/api/settings").status_code, 401)
                headers = {"Authorization": "Bearer test-only-token"}
                response = client.put("/api/settings", headers=headers, json={"cookie": "sessionid=private-test"})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("private-test", response.text)
                self.assertTrue(client.get("/api/settings", headers=headers).json()["cookie_configured"])
                self.assertEqual(client.get("/api/media/unknown/0?token=test-only-token").status_code, 404)
                self.assertEqual(client.post("/api/tasks", headers=headers, json={"link": "https://evil.example"}).status_code, 422)
                self.assertEqual(client.get("/api/status?token=test-only-token").status_code, 401)
                task = app.state.store.create_task(WORK_ID, "detail")
                app.state.store.update_task(task["id"], status="awaiting_confirmation")
                cancelled = client.post(f"/api/tasks/{task['id']}/cancel", headers=headers)
                self.assertEqual(cancelled.json()["status"], "cancelled")

    def test_directory_setting_preserves_existing_library_and_default_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = make_app(root / "data", root / "media", token="test-token", core=FakeCore(root / "media"))
            headers = {"Authorization": "Bearer test-token"}
            with TestClient(app) as client:
                folder = root / "media" / "作品" / WORK_ID
                folder.mkdir(parents=True)
                video = folder / "video.mp4"
                video.write_bytes(b"0123456789")
                app.state.store.save_work({"id": WORK_ID, "type": "视频"}, [video.relative_to(root / "media").as_posix()])
                response = client.put("/api/settings", headers=headers, json={"download_dir": "new", "interval_minutes": 15})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(Path(response.json()["download_dir"]), root / "media" / "new")
                media = client.get(f"/api/media/{WORK_ID}/0", headers={**headers, "Range": "bytes=0-3"})
                self.assertEqual(media.status_code, 206)
                self.assertEqual(media.content, b"0123")
                self.assertEqual(client.get("/api/library", headers=headers).json()["total"], 1)
                self.assertEqual(client.put(f'/api/favorites/{WORK_ID}',headers=headers,json={'favorite':True}).status_code,200)
                self.assertEqual(client.get('/api/library?favorite=true',headers=headers).json()['total'],1)
                self.assertEqual(client.put('/api/favorites/missing',headers=headers,json={'favorite':True}).status_code,404)
                self.assertIn(client.put(f'/api/favorites/{WORK_ID}',json={'favorite':False}).status_code,(401,403))
                self.assertEqual(client.get('/api/library?seed=-1',headers=headers).status_code,422)
                self.assertEqual(client.get('/api/authors/history',headers=headers).json(),[])
                self.assertEqual(client.put("/api/settings", headers=headers, json={"download_dir": "../outside"}).status_code, 422)
                author = client.post("/api/authors", headers=headers, json={"url": "https://www.douyin.com/user/testauthor"})
                self.assertEqual(author.json()["interval_minutes"], 15)


if __name__ == "__main__":
    unittest.main()
