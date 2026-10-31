import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core import Core, ParsedItems
from app.main import JobService
from app.store import Store
from tests.test_backend import FakeCore, WORK_ID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'TikTokDownloader'))

OTHER_ID = '7431234567890123457'
LIVE_ID = '7431234567890123460'
UNSUPPORTED_ID = '7431234567890123458'


def core_at(root):
    core = Core(root, root / 'data', root / 'media')
    core.ready = True
    return core


class ParseSkipTests(unittest.IsolatedAsyncioTestCase):
    async def test_author_skips_unsupported_types_and_reports_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = core_at(root)
            items = [
                {'id': WORK_ID, 'type': '视频', 'downloads': ['video']},
                {'id': UNSUPPORTED_ID, 'type': '图文', 'downloads': ['note']},
                {'id': OTHER_ID, 'type': '图集', 'downloads': ['image']},
                {'id': LIVE_ID, 'type': '实况', 'downloads': ['live']},
                {'id': WORK_ID, 'type': '视频', 'downloads': ['video-again']},
                {'id': '7431234567890123459', 'type': '直播', 'downloads': ['live-unsupported']},
            ]
            core.parser = SimpleNamespace(
                links=SimpleNamespace(run=AsyncMock(return_value=['MS4wLjABAAAA_author'])),
                handle_account=AsyncMock(return_value=SimpleNamespace(data=items)),
            )

            parsed = await core.parse('https://www.douyin.com/user/author', 'author')

            self.assertIsInstance(parsed, ParsedItems)
            self.assertEqual([item['id'] for item in parsed], [WORK_ID, OTHER_ID, LIVE_ID])
            self.assertEqual(parsed.skipped, 2)
            self.assertEqual(json.loads(json.dumps(parsed)), parsed)

    async def test_detail_unsupported_type_is_skipped_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = core_at(root)
            core.parser = SimpleNamespace(
                handle_detail=AsyncMock(return_value=SimpleNamespace(
                    data={'id': UNSUPPORTED_ID, 'type': '图文', 'downloads': ['note']}))
            )

            parsed = await core.parse(UNSUPPORTED_ID, 'detail')

            self.assertEqual(list(parsed), [])
            self.assertEqual(parsed.skipped, 1)

    async def test_supported_type_still_requires_identifier_and_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            core = core_at(root)
            core.parser = SimpleNamespace(
                handle_detail=AsyncMock(return_value=SimpleNamespace(
                    data={'id': WORK_ID, 'type': '视频'}))
            )

            with self.assertRaisesRegex(RuntimeError, '缺少作品标识或可下载地址'):
                await core.parse(WORK_ID, 'detail')


class SkippedAuthorCore(FakeCore):
    async def parse(self, link, kind, earliest=None):
        items = await super().parse(link, kind, earliest)
        return ParsedItems(items, skipped=2)


class AuthorTaskSkipTests(unittest.IsolatedAsyncioTestCase):
    async def test_author_task_completes_and_reports_skipped_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / 'data')
            core = SkippedAuthorCore(root / 'media')
            service = JobService(store, core, root / 'media')
            task = store.create_task('https://www.douyin.com/user/skipped', 'author')

            await service.process_one()

            finished = store.task(task['id'])
            self.assertEqual(finished['status'], 'completed')
            self.assertIn('跳过 2 个暂不支持的作品', finished['message'])
            self.assertEqual(store.stats()['works'], 1)


if __name__ == '__main__':
    unittest.main()
