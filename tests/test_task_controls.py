import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import JobService, make_app
from app.store import Store, BEIJING, period_end
from test_backend import FakeCore, WORK_ID


class ScheduleTests(unittest.TestCase):
    def test_quarterly_migration_is_once_and_preserves_paused_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp)
            for i in range(60):
                store.save_author(str(i),f'https://www.douyin.com/user/{i}',enabled=i%2==0,interval_minutes=1440)
            store.patch_author('0',starred=True,favorite=True)
            now=datetime(2026,10,24,21,tzinfo=BEIJING).timestamp()
            with patch('app.store.time.time',return_value=now):store.migrate_subscription_schedule()
            authors=store.authors()
            self.assertEqual(sum(a['enabled'] for a in authors),30)
            self.assertTrue(store.author('0')['starred']);self.assertTrue(store.author('0')['favorite'])
            days=set()
            for a in authors:
                self.assertEqual(a['interval_minutes'],129600)
                self.assertLess(now,a['next_check']);self.assertLess(a['next_check'],a['schedule_until'])
                when=datetime.fromtimestamp(a['next_check'],BEIJING)
                self.assertTrue(8<=when.hour<18);days.add(when.date())
            self.assertGreater(len(days),10)
            store.patch_author('0',interval_minutes=4320)
            Store(tmp).migrate_subscription_schedule()
            self.assertEqual(store.author('0')['interval_minutes'],4320)

    def test_calendar_periods_and_next_window(self):
        start=datetime(2024,1,31,12,tzinfo=BEIJING).timestamp()
        self.assertEqual(datetime.fromtimestamp(period_end(start,43200),BEIJING).day,29)
        self.assertEqual(datetime.fromtimestamp(period_end(start,129600),BEIJING).month,4)
        self.assertEqual(datetime.fromtimestamp(period_end(start,525600),BEIJING).year,2025)
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(tmp);store.save_author('a','https://www.douyin.com/user/a',enabled=True,interval_minutes=4320)
            with patch('app.store.time.time',return_value=start):store.mark_author_scheduled('a',reset=True)
            first=store.author('a')
            with patch('app.store.time.time',return_value=first['next_check']):store.mark_author_scheduled('a')
            self.assertGreaterEqual(store.author('a')['next_check'],first['schedule_until'])

    def test_scheduler_limits_overdue_burst_and_never_runs_at_night(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'data');service=JobService(store,FakeCore(root/'media'),root/'media')
            for i in range(8):store.save_author(str(i),f'https://www.douyin.com/user/{i}',enabled=True)
            night=datetime(2026,10,24,22,tzinfo=BEIJING).timestamp()
            with patch('app.main.time.time',return_value=night):service.schedule()
            self.assertEqual(store.tasks(),[])
            day=datetime(2026,10,25,9,tzinfo=BEIJING).timestamp()
            with patch('app.main.time.time',return_value=day):
                for _ in range(10):service.schedule()
            self.assertEqual(len(store.tasks()),1)


class TaskControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_failure_keeps_actionable_sanitized_reason(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from app.core import Core
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);core=Core(root,root/'data',root/'media');core.ready=True
            downloader=SimpleNamespace(recorder=SimpleNamespace(delete_id=AsyncMock()))
            async def fail(*args,**kwargs):
                downloader.last_error='媒体服务器返回 HTTP 429';return False
            downloader.run=fail;core.parser=SimpleNamespace(downloader=downloader)
            with self.assertRaisesRegex(RuntimeError,'HTTP 429'):
                await core.download({'id':WORK_ID,'type':'视频','downloads':['one']})

    async def test_cancel_running_keeps_partial_and_queue_can_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'data');core=FakeCore(root/'media');service=JobService(store,core,root/'media')
            started=asyncio.Event()
            async def blocked(item):
                (root/'media'/'partial.part').write_bytes(b'partial')
                started.set();await asyncio.Event().wait()
            core.download=blocked
            task=store.create_task(WORK_ID,'detail',accept_quality=True)
            processing=asyncio.create_task(service.process_one());await asyncio.wait_for(started.wait(),2)
            await service.cancel(task['id']);await asyncio.wait_for(processing,2)
            self.assertEqual(store.task(task['id'])['status'],'cancelled')
            self.assertEqual((root/'media'/'partial.part').read_bytes(),b'partial')
            core.download=FakeCore(root/'media').download
            next_task=store.create_task('7431234567890123457','detail',accept_quality=True)
            await service.process_one();self.assertEqual(store.task(next_task['id'])['status'],'completed')

    async def test_one_failed_work_does_not_abort_rest_of_author(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'data');core=FakeCore(root/'media');service=JobService(store,core,root/'media')
            old_parse,old_download=core.parse,core.download
            async def many(link,kind):
                item=(await old_parse(link,kind))[0];return [item,{**item,'id':'7431234567890123457'}]
            async def partial(item):
                if item['id']==WORK_ID:raise RuntimeError('媒体服务器返回 HTTP 403')
                return await old_download(item)
            core.parse=many;core.download=partial
            task=store.create_task('https://www.douyin.com/user/a','author',accept_quality=True)
            await service.process_one();result=store.task(task['id'])
            self.assertEqual(result['status'],'failed');self.assertEqual(result['completed'],1)
            self.assertIn('HTTP 403',result['message']);self.assertEqual(store.stats()['works'],1)


class ControlApiTests(unittest.TestCase):
    def test_retry_expected_successor_auth_validation_and_completion_race(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=make_app(root/'data',root/'media',token='test-token',core=FakeCore(root/'media'));store=app.state.store
            store.set_setting('queue_paused',True)
            old=store.create_task(WORK_ID,'detail');store.update_task(old['id'],status='failed',message='old',completed=1)
            current=store.create_task(WORK_ID,'detail');store.update_task(current['id'],status='completed')
            headers={'Authorization':'Bearer test-token'}
            url=f"/api/tasks/{old['id']}/retry?existing_task_id={current['id']}"
            with TestClient(app) as client:
                self.assertEqual(client.post(url).status_code,401)
                self.assertEqual(client.post(url+'bad',headers=headers).status_code,422)
                self.assertEqual(store.task(old['id'])['status'],'failed')
                result=client.post(url,headers=headers)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['id'],current['id'])
                self.assertEqual(store.task(old['id'])['status'],'cancelled')
                self.assertEqual(store.task(old['id'])['completed'],1)
                self.assertEqual(len(store.tasks()),2)

    def test_bulk_delete_cancel_retry_auth_and_favorites_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=make_app(root/'data',root/'media',token='test-token',core=FakeCore(root/'media'));store=app.state.store
            headers={'Authorization':'Bearer test-token'}
            root.joinpath('media').mkdir(exist_ok=True);original=root/'media'/'keep.mp4';original.write_bytes(b'keep')
            with TestClient(app) as client:
                first=store.create_task(WORK_ID,'detail');store.update_task(first['id'],status='failed')
                duplicate=store.create_task(WORK_ID,'detail');store.update_task(duplicate['id'],status='failed')
                store.set_setting('queue_paused',True)
                self.assertEqual(client.post('/api/tasks/batch',json={'action':'delete','ids':[first['id']]}).status_code,401)
                cancelled=client.post('/api/tasks/batch',headers=headers,json={'action':'cancel','ids':[first['id'],duplicate['id']]}).json()
                self.assertEqual(len(cancelled['changed']),2)
                retried=client.post('/api/tasks/batch',headers=headers,json={'action':'retry','ids':[first['id'],duplicate['id']]}).json()
                self.assertEqual(len(retried['changed']),2);self.assertEqual(len(retried['skipped']),0)
                self.assertEqual(store.task(first['id'])['status'],'paused')
                self.assertEqual(store.task(duplicate['id'])['status'],'cancelled')
                self.assertIn('#'+first['id'],store.task(duplicate['id'])['message'])
                deleted=client.post('/api/tasks/batch',headers=headers,json={'action':'delete','ids':[first['id'],duplicate['id']]}).json()
                self.assertEqual(len(deleted['changed']),2);self.assertEqual(original.read_bytes(),b'keep')
                for frequency,minutes in [('three_days',4320),('weekly',10080),('monthly',43200),('quarterly',129600),('yearly',525600)]:
                    a=client.post('/api/authors',headers=headers,json={'url':'https://www.douyin.com/user/example','frequency':frequency,'name':'示例'}).json()
                    self.assertEqual(a['interval_minutes'],minutes)
                a=client.patch('/api/authors/'+a['id'],headers=headers,json={'favorite':True,'starred':True}).json()
                self.assertTrue(a['enabled']);self.assertTrue(a['favorite']);self.assertTrue(a['starred'])
                due=a['next_check']
                saved=client.post('/api/authors',headers=headers,json={'url':a['url'],'favorite':True}).json()
                self.assertTrue(saved['enabled']);self.assertEqual(saved['next_check'],due)
                self.assertEqual(saved['name'],'示例')
                new=client.post('/api/authors',headers=headers,json={'url':'https://www.douyin.com/user/only_favorite','favorite':True}).json()
                self.assertTrue(new['favorite']);self.assertFalse(new['enabled'])
