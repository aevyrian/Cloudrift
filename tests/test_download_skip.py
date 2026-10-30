import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'TikTokDownloader'))

WORK_ID = '7682813070766825882'


class DownloadSkipTests(unittest.IsolatedAsyncioTestCase):
    async def queue_video(self, root, recorded=False):
        from src.downloader.download import Downloader
        downloader = Downloader.__new__(Downloader)
        downloader.recorder = SimpleNamespace(has_id=AsyncMock(return_value=recorded))
        downloader.log = Mock()
        tasks, skipped = [], set()
        await downloader.download_video(tasks, WORK_ID, WORK_ID,
            {'downloads': 'https://media.example/video'}, skipped,
            root / 'cache' / WORK_ID, root / WORK_ID)
        return tasks, skipped

    async def test_retry_queues_zero_byte_video_without_deleting_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / f'{WORK_ID}.mp4'
            video.touch()
            before = video.stat().st_mtime_ns
            tasks, skipped = await self.queue_video(root)
            self.assertEqual([task[2] for task in tasks], [video])
            self.assertEqual(skipped, set())
            self.assertEqual(video.read_bytes(), b'')
            self.assertEqual(video.stat().st_mtime_ns, before)

    async def test_existing_nonempty_video_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / f'{WORK_ID}.mp4'
            video.write_bytes(b'saved-video')
            before = video.stat().st_mtime_ns
            self.assertEqual(await self.queue_video(root), ([], {WORK_ID}))
            self.assertEqual(video.read_bytes(), b'saved-video')
            self.assertEqual(video.stat().st_mtime_ns, before)

    async def test_existing_other_video_format_prevents_retry_overwrite(self):
        for suffix in ('mov', 'm4v', 'mkv', 'webm'):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                video = root / f'{WORK_ID}.{suffix}'
                video.write_bytes(b'saved-video')
                (root / f'{WORK_ID}.mp4').touch()
                self.assertEqual(await self.queue_video(root), ([], {WORK_ID}))
                self.assertEqual(video.read_bytes(), b'saved-video')

    async def test_other_video_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / f'{WORK_ID}.mov'
            directory.mkdir()
            (directory / 'keep').write_bytes(b'keep')
            self.assertEqual(await self.queue_video(root), ([], {WORK_ID}))
            self.assertEqual((directory / 'keep').read_bytes(), b'keep')

    async def test_other_video_symlink_is_not_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(Path, 'is_symlink', autospec=True,
                              side_effect=lambda path: path.name == f'{WORK_ID}.mov'):
                self.assertEqual(await self.queue_video(root), ([], {WORK_ID}))

    async def test_other_zero_byte_video_does_not_block_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / f'{WORK_ID}.mov'
            other.touch()
            tasks, skipped = await self.queue_video(root)
            self.assertEqual([task[2] for task in tasks], [root / f'{WORK_ID}.mp4'])
            self.assertEqual(skipped, set())
            self.assertEqual(other.read_bytes(), b'')

    async def test_missing_video_is_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks, skipped = await self.queue_video(root)
            self.assertEqual([task[2] for task in tasks], [root / f'{WORK_ID}.mp4'])
            self.assertEqual(skipped, set())

    async def test_download_record_still_skips_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(await self.queue_video(Path(tmp), recorded=True), ([], {WORK_ID}))

    async def test_directory_is_not_queued_or_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / f'{WORK_ID}.mp4'
            video.mkdir()
            keep = video / 'keep.txt'
            keep.write_bytes(b'keep')
            self.assertEqual(await self.queue_video(root), ([], {WORK_ID}))
            self.assertEqual(keep.read_bytes(), b'keep')

    async def test_dangling_symlink_marker_is_rejected(self):
        from src.downloader.download import Downloader
        downloader = Downloader.__new__(Downloader)
        downloader.recorder = SimpleNamespace(has_id=AsyncMock(return_value=False))
        path = Mock(spec=Path)
        path.is_symlink.return_value = True
        path.exists.return_value = False
        self.assertTrue(await downloader.is_skip(WORK_ID, path))

    async def test_symlink_is_not_queued_even_if_target_is_empty_or_missing(self):
        for target_exists in (True, False):
            with self.subTest(target_exists=target_exists), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target, video = root / 'target.mp4', root / f'{WORK_ID}.mp4'
                if target_exists:
                    target.touch()
                try:
                    video.symlink_to(target)
                except OSError as error:
                    self.skipTest(f'Windows symlink permission unavailable: {error}')
                self.assertEqual(await self.queue_video(root), ([], {WORK_ID}))
                self.assertTrue(video.is_symlink())
                self.assertEqual(target.exists(), target_exists)
                if target_exists:
                    self.assertEqual(target.read_bytes(), b'')
