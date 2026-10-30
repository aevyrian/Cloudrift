import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.core import Core, verified_files

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'TikTokDownloader'))
WORK_ID = '7682813070766825882'


class LivePhotoTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_preserves_saved_motion_and_still_pages(self):
        from src.downloader.download import Downloader
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            motion = root / f'{WORK_ID}_1.mp4'
            still = root / f'{WORK_ID}_2.jpeg'
            motion.write_bytes(b'saved-motion')
            still.write_bytes(b'saved-still')
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (motion, still)}
            downloader = Downloader.__new__(Downloader)
            downloader.recorder = SimpleNamespace(has_id=AsyncMock(return_value=False))
            downloader.log = Mock()
            tasks = []
            await downloader.download_image(tasks, WORK_ID, WORK_ID,
                {'downloads': ['motion-url', 'still-url', 'missing-url']}, set(),
                root / 'cache' / WORK_ID, root / WORK_ID, suffix='mp4', type_='实况')
            self.assertEqual([task[0] for task in tasks], ['missing-url'])
            self.assertEqual(tasks[0][2], root / f'{WORK_ID}_3.mp4')
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (motion, still)})

    async def test_gallery_retry_preserves_actual_image_suffix_but_repairs_empty_page(self):
        from src.downloader.download import Downloader
        for suffix in ('png', 'webp', 'jpg'):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                saved = root / f'{WORK_ID}_1.{suffix}'
                saved.write_bytes(b'saved-image')
                (root / f'{WORK_ID}_2.jpeg').write_bytes(b'')
                downloader = Downloader.__new__(Downloader)
                downloader.recorder = SimpleNamespace(has_id=AsyncMock(return_value=False))
                downloader.log = Mock()
                tasks = []
                await downloader.download_image(tasks, WORK_ID, WORK_ID,
                    {'downloads': ['saved-url', 'empty-url', 'missing-url']}, set(),
                    root / 'cache' / WORK_ID, root / WORK_ID)
                self.assertEqual([task[0] for task in tasks], ['empty-url', 'missing-url'])
                self.assertEqual(saved.read_bytes(), b'saved-image')

    async def test_live_photo_parses_and_requires_every_mixed_media_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = {'id': WORK_ID, 'type': '实况', 'downloads': ['https://example.com/motion', 'https://example.com/still']}
            core = Core(root, root / 'data', root / 'media')
            core.ready = True
            core.parser = SimpleNamespace(handle_detail=AsyncMock(return_value=SimpleNamespace(data=item)))
            self.assertEqual(await core.parse(WORK_ID, 'detail'), [item])
            folder = root / '作品' / WORK_ID
            folder.mkdir(parents=True)
            (folder / f'{WORK_ID}_1.mp4').write_bytes(b'motion')
            with self.assertRaises(RuntimeError):
                verified_files(root, item)
            (folder / f'{WORK_ID}_2.jpeg').write_bytes(b'still')
            self.assertEqual(verified_files(root, item), [f'作品/{WORK_ID}/{WORK_ID}_1.mp4', f'作品/{WORK_ID}/{WORK_ID}_2.jpeg'])
