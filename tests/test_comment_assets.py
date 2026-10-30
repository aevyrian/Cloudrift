import asyncio
import csv
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch


class CommentAssetsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('app.comment_assets'), 'offline assets/export helper is missing')
        from app import comment_assets
        self.assets = comment_assets
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def snapshot(self):
        return {'work_id': '1234567890123456789', 'source_url': 'https://secret/?token=private',
                'status': {'operation': 'finished', 'integrity': 'partial', 'total': 2, 'replies': 1},
                'items': [{'id': '9876543210987654321', 'root_id': '', 'nickname': '=cmd',
                           'text': '\t+formula\n"emoji😀"', 'source_url': 'https://secret/?token=private',
                           'attachments': [{'id': 1, 'status': 'saved', 'kind': 'image',
                                            'relative_path': 'comment-assets/one.png', 'source_url': 'https://secret/?token=private'}]},
                          {'id': '2', 'root_id': '9876543210987654321', 'reply_to_id': 'missing',
                           'text': 'reply', 'attachments': [{'id': 2, 'status': 'pending', 'source_url': 'https://secret/?token=private'}]}]}

    def local_image(self):
        path = self.root / 'comment-assets' / 'one.png'
        path.parent.mkdir()
        path.write_bytes(b'\x89PNG\r\n\x1a\ncontent')
        return path

    async def test_exports_whitelist_ids_csv_and_zip_missing_manifest(self):
        self.local_image()
        original = self.snapshot()
        for fmt in ('json', 'csv', 'zip'):
            path, mime, filename = self.assets.export_snapshot(original, self.root, fmt)
            self.assertTrue(path.is_file())
            self.assertTrue(filename.endswith('.' + fmt))
            self.assertTrue(mime)
            if fmt == 'zip':
                with zipfile.ZipFile(path) as archive:
                    payload = archive.read('comments.json')
                    self.assertEqual(archive.read('assets/one.png'), b'\x89PNG\r\n\x1a\ncontent')
                    self.assertIn('missing', archive.read('README.txt').decode())
                    self.assertEqual(len(archive.namelist()), 4)
            else:
                payload = path.read_bytes()
            self.assertNotIn(b'token=private', payload)
            self.assertNotIn(b'source_url', payload)
            if fmt in ('json', 'zip'):
                doc = json.loads(payload)
                self.assertEqual(doc['format_version'], 1)
                self.assertEqual(doc['items'][0]['id'], '9876543210987654321')
                self.assertEqual(doc['items'][1]['root_id'], '9876543210987654321')
                self.assertEqual(doc['status']['total'], 2)
            else:
                self.assertTrue(payload.startswith(b'\xef\xbb\xbf'))
                rows = list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
                self.assertEqual(rows[0]['nickname'], "'=cmd")
                self.assertEqual(rows[0]['text'], "'\t+formula\n\"emoji😀\"")
        self.assertEqual(original, self.snapshot())

    async def test_path_requires_saved_and_rejects_traversal_and_symlink(self):
        path = self.local_image()
        self.assertEqual(self.assets.asset_path(self.root, {'status': 'saved', 'relative_path': 'comment-assets/one.png'}), path)
        for relative in ('../outside', 'comment-assets/../outside', 'comment-assets\\one.png', '/etc/passwd', 'C:/Windows/win.ini'):
            with self.subTest(relative=relative), self.assertRaises(RuntimeError):
                self.assets.asset_path(self.root, {'status': 'saved', 'relative_path': relative})
        with self.assertRaises(RuntimeError):
            self.assets.asset_path(self.root, {'status': 'pending', 'relative_path': 'comment-assets/one.png'})
        link = path.parent / 'link.png'
        try:
            link.symlink_to(path)
        except OSError:
            pass
        else:
            with self.assertRaises(RuntimeError):
                self.assets.asset_path(self.root, {'status': 'saved', 'relative_path': 'comment-assets/link.png'})

    async def test_download_rejects_unknown_and_private_dns_without_network(self):
        for url in ('http://p1.douyinpic.com/a', 'https://localhost/a', 'https://p1.douyinpic.com.evil.test/a',
                    'https://user:password@p1.douyinpic.com/a', 'https://p1.douyinpic.com:444/a'):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                await self.assets.download_asset(url, self.root, 100)
        with patch.object(self.assets, 'ALLOWED_CDN_SUFFIXES', ('douyinpic.com',)), patch.object(
                asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=[(2, 1, 6, '', ('127.0.0.1', 443))])):
            with self.assertRaises(RuntimeError):
                await self.assets.download_asset('https://p1.douyinpic.com/a', self.root, 100)
        self.assertFalse((self.root / 'comment-assets').exists())

    def fake_session(self, responses, seen):
        @asynccontextmanager
        async def stream(method, url, **kwargs):
            seen.append((url, kwargs))
            status, headers, chunks = responses.pop(0)
            class Response:
                status_code = status
                async def aiter_content(self):
                    for chunk in chunks:
                        yield chunk
            response = Response()
            response.headers = headers
            yield response
        class Session:
            def __init__(self, **kwargs):
                seen.append(kwargs)
                self.stream = stream
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        return Session

    async def test_download_pins_public_dns_and_saves_by_content_hash_without_credentials(self):
        png = b'\x89PNG\r\n\x1a\ncontent'
        seen = []
        response = [(200, {'content-type': 'image/png'}, [png[:4], png[4:]])]
        with patch.object(self.assets, 'ALLOWED_CDN_SUFFIXES', ('douyinpic.com',)), patch.object(
                asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])), patch.object(
                self.assets, 'AsyncSession', self.fake_session(response, seen)):
            result = await self.assets.download_asset('https://p1.douyinpic.com/a?signature=secret', self.root, 100)
        self.assertEqual((self.root / result['relative_path']).read_bytes(), png)
        self.assertEqual(result['bytes'], len(png))
        self.assertIn(result['sha256'], result['relative_path'])
        options = seen[0]
        self.assertFalse(options['trust_env'])
        self.assertIn('p1.douyinpic.com:443:93.184.216.34', str(options['curl_options']))
        request = seen[1][1]
        self.assertNotIn('Cookie', request['headers'])
        self.assertNotIn('Authorization', request['headers'])
        self.assertFalse(request['allow_redirects'])
        self.assertFalse(list((self.root / 'comment-assets').glob('*.tmp')))

    async def test_download_rejects_bad_magic_redirect_and_quota_and_cleans_partial(self):
        cases = [([(200, {'content-type': 'image/png'}, [b'<svg>bad</svg>'])], 100, RuntimeError),
                 ([(200, {'content-type': 'image/png'}, [b'\x89PNG\r\n\x1a\n', b'x' * 10])], 10, self.assets.AssetLimit),
                 ([(302, {'location': 'https://127.0.0.1/a'}, [])], 100, RuntimeError),
                 ([(200, {'content-type': 'image/png', 'content-length': str(21 * 1024 * 1024)}, [])], 30 * 1024 * 1024, RuntimeError)]
        for responses, remaining, error in cases:
            with self.subTest(responses=responses), patch.object(self.assets, 'ALLOWED_CDN_SUFFIXES', ('douyinpic.com',)), patch.object(
                    asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])), patch.object(
                    self.assets, 'AsyncSession', self.fake_session(responses, [])):
                with self.assertRaises(error) as caught:
                    await self.assets.download_asset('https://p1.douyinpic.com/a?signature=secret', self.root, remaining)
                self.assertNotIn('secret', str(caught.exception))
                self.assertFalse(list(self.root.rglob('*.tmp')))

    async def test_disk_failure_is_distinct_and_does_not_expose_paths(self):
        self.assertTrue(hasattr(self.assets, 'AssetStorageError'), 'disk errors must stop collection')
        response = [(200, {'content-type': 'image/png'}, [b'\x89PNG\r\n\x1a\ncontent'])]
        with patch.object(self.assets, 'ALLOWED_CDN_SUFFIXES', ('douyinpic.com',)), patch.object(
                asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])), patch.object(
                self.assets, 'AsyncSession', self.fake_session(response, [])), patch.object(
                self.assets.tempfile, 'NamedTemporaryFile', side_effect=OSError('secret private filesystem path')):
            with self.assertRaises(self.assets.AssetStorageError) as caught:
                await self.assets.download_asset('https://p1.douyinpic.com/a', self.root, 100)
            self.assertNotIn('secret', str(caught.exception))

    async def test_timeout_removes_partial_file_and_redirects_revalidate_dns(self):
        seen = []
        response = [(302, {'location': 'https://p2.douyinpic.com/b'}, []),
                    (200, {'content-type': 'image/png'}, [b'\x89PNG\r\n\x1a\ncontent'])]
        answers = [[(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('10.0.0.1', 443))]]
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(side_effect=answers)), patch.object(
                self.assets, 'AsyncSession', self.fake_session(response, seen)):
            with self.assertRaises(RuntimeError):
                await self.assets.download_asset('https://p1.douyinpic.com/a', self.root, 100)
        self.assertFalse((self.root / 'comment-assets').exists())
        wait_for = asyncio.wait_for
        async def brief_wait(awaitable, timeout):
            self.assertEqual(timeout, 15)
            return await wait_for(awaitable, .01)
        class SlowResponse:
            status_code = 200
            headers = {'content-type': 'image/png'}
            async def aiter_content(self):
                yield b'\x89PNG\r\n\x1a\n'
                await asyncio.sleep(.1)
        @asynccontextmanager
        async def slow_stream(*args, **kwargs):
            yield SlowResponse()
        session = self.fake_session([], [])
        def slow_session(**kwargs):
            instance = session(**kwargs)
            instance.stream = slow_stream
            return instance
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=answers[0])), patch.object(
                self.assets, 'AsyncSession', slow_session), patch.object(self.assets.asyncio, 'wait_for', brief_wait):
            with self.assertRaises(RuntimeError):
                await self.assets.download_asset('https://p1.douyinpic.com/a', self.root, 100)
        self.assertFalse(list(self.root.rglob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
