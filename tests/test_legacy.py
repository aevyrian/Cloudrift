import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.legacy import LegacyIndex
from app.legacy import directory_works
from app import legacy
from app.main import make_app
from app.main import JobService
from app.store import Store
from tests.test_backend import FakeCore, WORK_ID


class RecordedDownloadTests(unittest.TestCase):
    def test_batch_reads_only_requested_ids_with_one_read_only_connection(self):
        self.assertTrue(callable(getattr(legacy, 'recorded_downloads', None)), 'batch lookup is missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'DouK-Downloader.db'
            ids = [str(7431234567890123000 + i) for i in range(1001)]
            saved = {ids[0], ids[-1], '7431234567890999999'}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE download_data(ID TEXT PRIMARY KEY)')
                db.executemany('INSERT INTO download_data VALUES (?)', [(work_id,) for work_id in saved])
            db.close()
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            connect, queries = sqlite3.connect, []
            def read_only_connect(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
                connection.set_trace_callback(queries.append)
                with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                    connection.execute('INSERT INTO download_data VALUES (?)', ('write-must-fail',))
                return connection
            with patch('app.legacy.sqlite3.connect', side_effect=read_only_connect) as opened:
                self.assertEqual(legacy.recorded_downloads(root, ids + [ids[0], "' OR 1=1 --"]), {ids[0], ids[-1]})
                self.assertEqual(opened.call_count, 1)
            self.assertEqual(sum(query.startswith('SELECT ') for query in queries), 2)
            self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
            self.assertTrue(legacy.recorded_download(root, ids[0]))
            self.assertFalse(legacy.recorded_download(root, ids[1]))

    def test_empty_ids_and_missing_database_do_not_open_or_create_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('app.legacy.sqlite3.connect') as opened:
                self.assertEqual(legacy.recorded_downloads(root, []), set())
                self.assertEqual(legacy.recorded_downloads(root, [WORK_ID]), set())
                opened.assert_not_called()
            self.assertFalse((root / 'DouK-Downloader.db').exists())

    def test_invalid_database_stops_instead_of_claiming_no_saved_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'DouK-Downloader.db'
            path.write_bytes(b'not a database')
            with self.assertRaisesRegex(RuntimeError, '旧下载记录暂时无法读取'):
                legacy.recorded_downloads(root, [WORK_ID])
            self.assertEqual(path.read_bytes(), b'not a database')

    def test_database_resolving_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'original'
            root.mkdir()
            outside = Path(tmp) / 'outside.db'
            outside.write_bytes(b'outside')
            path = root / 'DouK-Downloader.db'
            path.write_bytes(b'link-target-placeholder')
            resolve = Path.resolve
            def redirected(candidate, *args, **kwargs):
                return outside if candidate == path else resolve(candidate, *args, **kwargs)
            with patch.object(Path, 'resolve', redirected), patch('app.legacy.sqlite3.connect') as opened:
                with self.assertRaisesRegex(RuntimeError, '旧下载记录路径异常'):
                    legacy.recorded_downloads(root, [WORK_ID])
                opened.assert_not_called()
            self.assertEqual(outside.read_bytes(), b'outside')


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'original'
        self.author = self.source / 'UID123_A-B_发布作品'
        self.author.mkdir(parents=True)
        self.store = Store(self.root / 'data')

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, name, content=b'0123456789'):
        path = self.author / name
        path.write_bytes(content)
        return path

    def test_grouping_page_order_author_and_read_only_originals(self):
        paths = [self.file(f'2025-12-01 12.30.00-图集-A-B-海边_假期_{i}.jpeg') for i in (10, 2, 1)]
        self.file('2025-12-02 12.30.00-实况-A-B-日落_1.mp4')
        self.file('2025-12-02 12.30.00-实况-A-B-日落_2.jpeg')
        before = {p.name:(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_mode) for p in self.author.iterdir()}
        index = LegacyIndex(self.store, self.source)
        index.scan()
        self.assertEqual(index.state['status'], 'completed')
        items = self.store.library()['items']
        self.assertEqual(len(items), 2)
        gallery = next(w for w in items if w['type'] == '图集')
        self.assertEqual(gallery['author'], 'A-B')
        self.assertEqual(gallery['description'], '海边_假期')
        self.assertEqual(gallery['source'], 'legacy')
        self.assertEqual([Path(f).stem.rsplit('_',1)[1] for f in gallery['files']], ['1','2','10'])
        self.assertEqual(next(w for w in items if w['type']=='实况')['files'][-1].rsplit('.',1)[1], 'jpeg')
        self.assertEqual(before, {p.name:(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_mode) for p in self.author.iterdir()})

    def test_rescan_deduplicates_skips_cache_and_only_prunes_after_success(self):
        path = self.file('2025-12-01 12.30.00-视频-A-B-海边.mp4')
        self.file('2025-12-02 12.30.00-视频-A-B-空文件.mp4', b'')
        cache = self.source/'Cache';cache.mkdir();(cache/'partial.mp4').write_bytes(b'partial')
        index = LegacyIndex(self.store, self.source)
        index.scan();first = self.store.library()['items'][0]['id']
        index.scan()
        self.assertEqual(self.store.stats()['works'], 1)
        self.assertEqual(self.store.library()['items'][0]['id'], first)
        with patch('app.legacy.os.walk', side_effect=OSError('disk temporarily unavailable')):
            index.scan()
        self.assertEqual(index.state['status'], 'failed')
        self.assertIsNotNone(self.store.work(first))
        self.file('2025-12-03 12.30.00-视频-A-B-新作品.mp4')
        path.unlink()
        index.scan()
        self.assertEqual(index.state['status'], 'completed')
        self.assertIsNone(self.store.work(first))
        self.assertEqual(self.store.stats()['works'], 1)

    def test_legacy_media_range_auth_and_escape_boundary(self):
        original = self.file('2025-12-01 12.30.00-视频-A-B-海边.mp4')
        index = LegacyIndex(self.store, self.source);index.scan()
        work = self.store.library()['items'][0]
        app = make_app(self.root/'data',self.root/'media',token='test-admin-key',core=FakeCore(self.root/'media'),legacy_dir=self.source)
        headers = {'Authorization':'Bearer test-admin-key'}
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/legacy').status_code,401)
            self.assertEqual(client.get(f"/api/media/{work['id']}/0").status_code,401)
            response = client.get(f"/api/media/{work['id']}/0",headers={**headers,'Range':'bytes=2-5'})
            self.assertEqual(response.status_code,206)
            self.assertEqual(response.content,b'2345')
            self.assertEqual(client.get('/api/library',headers=headers).json()['items'][0]['source'],'legacy')
            outside=self.root/'private.mp4';outside.write_bytes(b'private')
            try:
                original.unlink();original.symlink_to(outside)
            except OSError:
                return  # Windows may not permit symlinks; NAS exercises the read-only mount.
            self.assertEqual(client.get(f"/api/media/{work['id']}/0",headers=headers).status_code,404)

    def test_new_download_folder_is_not_imported_twice(self):
        self.file('2025-12-01 12.30.00-视频-A-B-海边.mp4')
        new = self.source/'Cloudrift'/'作品'/WORK_ID
        new.mkdir(parents=True)
        (new/(WORK_ID+'.mp4')).write_bytes(b'new-download')
        self.store.save_work({'id':WORK_ID,'type':'视频'}, ['Cloudrift/作品/'+WORK_ID+'/'+WORK_ID+'.mp4'])
        index = LegacyIndex(self.store,self.source);index.scan()
        self.assertEqual(index.state['indexed_works'],1)
        self.assertEqual(self.store.stats()['works'],2)
        self.assertEqual(self.store.work(WORK_ID)['source'],'download')

    def test_direct_volume_downloads_are_excluded_from_the_old_index(self):
        new=self.source/'作品'/WORK_ID
        new.mkdir(parents=True)
        (new/(WORK_ID+'.mp4')).write_bytes(b'already-saved')
        self.store.save_work({'id':WORK_ID,'type':'视频'},['作品/'+WORK_ID+'/'+WORK_ID+'.mp4'])
        index=LegacyIndex(self.store,self.source); index.scan()
        self.assertEqual(index.state['indexed_works'],0)
        self.assertEqual(self.store.stats()['works'],1)

    def test_background_index_keeps_health_alive_and_stops_unverified_downloads(self):
        self.file('2025-12-01 12.30.00-视频-A-B-海边.mp4')
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(5)
            yield from directory_works(*args)
        with patch('app.legacy.directory_works',slow):
            app = make_app(self.root/'data',self.root/'media',token='test-admin-key',core=FakeCore(self.root/'media'),legacy_dir=self.source)
            with TestClient(app) as client:
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertTrue(client.get('/health').json()['worker_alive'])
                    headers={'Authorization':'Bearer test-admin-key'}
                    job=client.post('/api/tasks',headers=headers,json={'link':WORK_ID}).json()
                    for _ in range(30):
                        if app.state.store.task(job['id'])['status']=='failed':
                            break
                        time.sleep(.05)
                    self.assertEqual(app.state.store.task(job['id'])['status'],'failed')
                    self.assertIn('扫描尚未完成',app.state.store.task(job['id'])['message'])
                    self.assertEqual(app.state.service.core.download_calls,0)
                    self.assertTrue(client.get('/health').json()['worker_alive'])
                    self.assertEqual(client.get('/api/legacy',headers=headers).json()['status'],'scanning')
                finally:
                    release.set()


class LegacyDedupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.media=self.root/'Volume'; self.author=self.media/'UID123_旧名字_发布作品'
        self.author.mkdir(parents=True); self.store=Store(self.root/'data')
        self.core=FakeCore(self.media)
        self.item={'id':WORK_ID,'uid':'123','sec_uid':'new-sec-uid','nickname':'改过的新名字','type':'视频','create_time':'2025-12-01 12:30:00','desc':'改过的文字','downloads':'https://media.example/v'}
        async def parse(*args): return [self.item]
        self.core.parse=parse
        self.service=JobService(self.store,self.core,self.media,legacy_root=self.media)

    async def asyncTearDown(self): self.tmp.cleanup()

    def original(self,kind='视频',page='',suffix='.mp4'):
        p=self.author/('2025-12-01 12.30.00-'+kind+'-旧名字-旧描述'+page+suffix)
        p.write_bytes(b'original'); return p

    async def run_job(self):
        job=self.store.create_task(WORK_ID,'detail',accept_quality=True)
        await self.service.process_one(); return self.store.task(job['id'])

    async def test_existing_old_video_is_skipped_and_alias_survives_rescan(self):
        p=self.original(); before=(p.stat().st_size,p.stat().st_mtime_ns)
        index=LegacyIndex(self.store,self.media); index.scan()
        job=await self.run_job()
        self.assertEqual(job['status'],'completed'); self.assertIn('跳过本库已有 1',job['message'])
        self.assertEqual(self.core.download_calls,0); self.assertEqual(self.store.stats()['works'],1)
        local_id=self.store.work(WORK_ID)['id']; self.assertTrue(local_id.startswith('legacy-'))
        self.assertEqual(Store(self.root/'data').work(WORK_ID)['id'],local_id)
        self.assertEqual(self.store.library(q=WORK_ID)['total'],1)
        index.scan(); self.assertEqual(self.store.work(WORK_ID)['id'],local_id)
        self.assertEqual(before,(p.stat().st_size,p.stat().st_mtime_ns))

    async def test_second_work_is_downloaded_without_rewriting_the_old_alias(self):
        self.original(); LegacyIndex(self.store,self.media).scan()
        first=await self.run_job()
        self.assertEqual(first['status'],'completed')
        local_id=self.store.work(WORK_ID)['id']
        self.store.link_work(WORK_ID,local_id)
        self.item['id']='7431234567890123457'
        second=self.store.create_task(self.item['id'],'detail',accept_quality=True)
        await self.service.process_one()
        self.assertEqual(self.store.task(second['id'])['status'],'completed')
        self.assertEqual(self.store.work(self.item['id'])['id'], self.item['id'])
        self.assertEqual(self.store.work(WORK_ID)['id'],local_id)
        self.assertEqual(self.core.download_calls,1)
        self.assertEqual((self.media/'作品'/self.item['id']/(self.item['id']+'.mp4')).read_bytes(),b'test-video')
        with self.store.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,work_id FROM work_aliases')], [(WORK_ID,local_id)])

    async def test_existing_conflicting_aliases_download_separately_without_rewriting_old_rows(self):
        original=self.original(); LegacyIndex(self.store,self.media).scan()
        local_id=self.store.library()['items'][0]['id']
        aliases=[(WORK_ID,local_id),('7431234567890123457',local_id)]
        with self.store.connect() as db:
            db.executemany('INSERT INTO work_aliases VALUES (?,?)',aliases)
        for work_id,_ in aliases:
            with self.subTest(work_id=work_id):
                self.item['id']=work_id
                task=self.store.create_task(work_id,'detail',accept_quality=True)
                await self.service.process_one()
                self.assertEqual(self.store.task(task['id'])['status'],'completed')
                self.assertEqual(self.store.work(work_id)['id'], work_id)
                self.assertEqual((self.media/'作品'/work_id/(work_id+'.mp4')).read_bytes(),b'test-video')
        with self.store.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,work_id FROM work_aliases ORDER BY id')],aliases)
        self.assertEqual(self.store.work(local_id)['id'],local_id)
        self.assertEqual(original.read_bytes(),b'original')
        self.assertEqual(self.core.download_calls,2)

    async def test_existing_alias_to_invalid_date_fails_without_rewriting_old_rows(self):
        original=self.author/'2025-02-30 12.30.00-视频-旧名字-旧描述.mp4'
        original.write_bytes(b'original')
        before=(original.read_bytes(),original.stat().st_mtime_ns)
        LegacyIndex(self.store,self.media).scan()
        local_id=self.store.library()['items'][0]['id']
        with self.store.connect() as db:
            db.execute('INSERT INTO work_aliases VALUES (?,?)',(WORK_ID,local_id))
        job=await self.run_job()
        self.assertEqual(job['status'],'failed')
        self.assertIn('无法识别时间',job['message'])
        with self.store.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,work_id FROM work_aliases')],[(WORK_ID,local_id)])
        self.assertEqual(self.store.work(local_id)['id'],local_id)
        self.assertEqual(before,(original.read_bytes(),original.stat().st_mtime_ns))
        self.assertEqual(self.core.download_calls,0)

    async def test_invalid_date_alias_stays_blocked_if_current_author_uid_changed(self):
        original=self.author/'2025-02-30 12.30.00-视频-旧名字-旧描述.mp4'
        original.write_bytes(b'original')
        LegacyIndex(self.store,self.media).scan()
        local_id=self.store.library()['items'][0]['id']
        with self.store.connect() as db:
            db.execute('INSERT INTO work_aliases VALUES (?,?)',(WORK_ID,local_id))
        self.item['uid']='different-author'
        job=await self.run_job()
        self.assertEqual(job['status'],'failed')
        self.assertIn('无法识别时间',job['message'])
        self.assertEqual(self.core.download_calls,0)
        self.assertEqual(original.read_bytes(),b'original')

    async def test_invalid_calendar_date_in_existing_index_blocks_download_and_alias(self):
        original=self.author/'2025-02-30 12.30.00-视频-旧名字-旧描述.mp4'
        original.write_bytes(b'original')
        modified=datetime(2025,12,1,12,30).timestamp()
        os.utime(original,(modified,modified))
        before=(original.read_bytes(),original.stat().st_mtime_ns)
        LegacyIndex(self.store,self.media).scan()
        self.service.store=Store(self.root/'data')
        self.assertEqual(self.store.library()['items'][0]['created_at'],'2025-12-01T12:30:00')
        for created in ('2025-12-01 12:30:00','2025-12-02 12:30:00'):
            with self.subTest(created=created):
                self.item['create_time']=created
                job=await self.run_job()
                self.assertEqual(job['status'],'failed')
                self.assertIn('无法识别时间',job['message'])
                self.assertIsNone(self.store.work(WORK_ID))
        self.assertEqual(self.core.download_calls,0)
        self.assertEqual(before,(original.read_bytes(),original.stat().st_mtime_ns))

    async def test_incomplete_old_gallery_downloads_separately_without_changing_original(self):
        self.item.update(type='图集',downloads=['one','two'])
        original=self.original('图集','_1','.jpeg'); LegacyIndex(self.store,self.media).scan()
        async def download(item):
            self.core.download_calls += 1
            directory=self.media/'作品'/item['id'];directory.mkdir(parents=True,exist_ok=True)
            files=[]
            for index in (1,2):
                path=directory/f'{item["id"]}_{index}.jpeg';path.write_bytes(f'new-{index}'.encode())
                files.append(path.relative_to(self.media).as_posix())
            return files
        self.core.download=download
        job=await self.run_job()
        self.assertEqual(job['status'],'completed')
        self.assertEqual(self.core.download_calls,1)
        self.assertEqual(original.read_bytes(),b'original')
        self.assertEqual(len(self.store.work(WORK_ID)['files']),2)
        self.assertEqual(self.store.work(WORK_ID)['id'],WORK_ID)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM work_aliases').fetchone()[0],0)

    async def test_extra_old_gallery_files_are_kept_and_do_not_force_redownload(self):
        self.item.update(type='图集',downloads=['one','two'])
        originals=[self.original('图集',f'_{index}', '.jpeg') for index in (1,2,3)]
        LegacyIndex(self.store,self.media).scan()
        job=await self.run_job()
        self.assertEqual(job['status'],'completed')
        self.assertEqual(self.core.download_calls,0)
        self.assertEqual(len(self.store.work(WORK_ID)['files']),3)
        self.assertTrue(all(path.read_bytes()==b'original' for path in originals))

    async def test_indexed_old_missing_file_downloads_separately(self):
        original=self.original()
        LegacyIndex(self.store,self.media).scan()
        local_id=self.store.library()['items'][0]['id']
        self.store.link_work(WORK_ID,local_id)
        original.unlink()
        job=await self.run_job()
        self.assertEqual(job['status'],'completed')
        self.assertEqual(self.core.download_calls,1)
        self.assertEqual((self.media/'作品'/WORK_ID/(WORK_ID+'.mp4')).read_bytes(),b'test-video')
        self.assertFalse(original.exists())
        with self.store.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,work_id FROM work_aliases')],[(WORK_ID,local_id)])

    async def test_unfinished_index_and_unlocatable_old_record_do_not_redownload(self):
        self.original()
        job=await self.run_job(); self.assertEqual(job['status'],'failed')
        self.assertEqual(self.core.download_calls,0)
        LegacyIndex(self.store,self.media).scan(); self.item['create_time']='2025-12-02 12:30:00'
        import sqlite3
        with sqlite3.connect(self.media/'DouK-Downloader.db') as db:
            db.execute('CREATE TABLE download_data(ID TEXT PRIMARY KEY)')
            db.execute('INSERT INTO download_data VALUES (?)',(WORK_ID,))
        db.close()
        self.service.retry(job['id']); await self.service.process_one()
        self.assertEqual(self.store.task(job['id'])['status'],'failed')
        self.assertIn('旧下载记录',self.store.task(job['id'])['message'])
        self.assertEqual(self.core.download_calls,0)

    async def test_actual_new_work_is_saved_under_volume_root(self):
        self.original(); LegacyIndex(self.store,self.media).scan()
        self.item['create_time']='2025-12-02 12:30:00'
        job=await self.run_job()
        self.assertEqual(job['status'],'completed'); self.assertEqual(self.core.download_calls,1)
        self.assertTrue((self.media/'作品'/WORK_ID/(WORK_ID+'.mp4')).is_file())
        self.assertFalse((self.media/'Cloudrift').exists())

    async def test_ambiguous_or_malformed_old_names_stop_without_redownloading(self):
        self.original()
        other=self.media/'UID123_另一个名字_发布作品'; other.mkdir()
        (other/'2025-12-01 12.30.00-视频-另一个名字-不同作品.mp4').write_bytes(b'other')
        LegacyIndex(self.store,self.media).scan()
        job=await self.run_job(); self.assertEqual(job['status'],'failed')
        self.assertIn('多个旧作品',job['message']); self.assertEqual(self.core.download_calls,0)
        self.item['create_time']='2025-12-03 12:30:00'
        (self.author/'日期错误-图集-旧名字-描述_1.jpeg').write_bytes(b'unknown')
        LegacyIndex(self.store,self.media).scan()
        self.service.retry(job['id']); await self.service.process_one()
        self.assertIn('无法识别时间',self.store.task(job['id'])['message'])
        self.assertEqual(self.core.download_calls,0)

    async def test_byte_identical_legacy_copies_are_folded_without_touching_either(self):
        first=self.original()
        other=self.media/'UID123_另一个名字_发布作品';other.mkdir()
        second=other/'2025-12-01 12.30.00-视频-另一个名字-不同作品.mp4'
        second.write_bytes(first.read_bytes())
        LegacyIndex(self.store,self.media).scan()
        before=[(p.read_bytes(),p.stat().st_mtime_ns) for p in (first,second)]
        job=await self.run_job()
        self.assertEqual(job['status'],'completed')
        self.assertEqual(self.core.download_calls,0)
        self.assertTrue(self.store.work(WORK_ID)['id'].startswith('legacy-'))
        self.assertEqual([(p.read_bytes(),p.stat().st_mtime_ns) for p in (first,second)],before)
        self.assertEqual(self.store.library()['total'],2)


if __name__ == '__main__':
    unittest.main()
