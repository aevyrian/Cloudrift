import asyncio
import csv
import importlib.util
import io
import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
                self.options = kwargs
                self.stream = stream
            async def request(self, method, url, **kwargs):
                from curl_cffi import CurlOpt
                async with self.stream(method, url, **kwargs) as response:
                    if int(response.headers.get('content-length', '0')) > self.options['curl_options'][CurlOpt.MAXFILESIZE_LARGE]:
                        error = RuntimeError('file size exceeded')
                        error.code = 63
                        raise error
                    async for chunk in response.aiter_content():
                        if kwargs['content_callback'](chunk) != len(chunk):
                            raise RuntimeError('transfer aborted')
                    return response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        return Session

    async def test_download_pins_public_dns_and_saves_by_content_hash_without_credentials(self):
        from curl_cffi import CurlOpt
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
        self.assertGreater(options['curl_options'][CurlOpt.TIMEOUT_MS], 0)
        self.assertLessEqual(options['curl_options'][CurlOpt.TIMEOUT_MS], 15000)
        self.assertEqual(options['curl_options'][CurlOpt.MAXFILESIZE_LARGE], 100)
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
        self.assertFalse(list(self.root.rglob('*.tmp')))
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
            async def slow_request(*args, **kwargs):
                async with slow_stream() as response:
                    async for chunk in response.aiter_content():
                        kwargs['content_callback'](chunk)
                    return response
            instance.request = slow_request
            return instance
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=answers[0])), patch.object(
                self.assets, 'AsyncSession', slow_session), patch.object(self.assets.asyncio, 'wait_for', brief_wait):
            with self.assertRaises(RuntimeError):
                await self.assets.download_asset('https://p1.douyinpic.com/a', self.root, 100)
        self.assertFalse(list(self.root.rglob('*.tmp')))

    async def test_real_curl_aborts_announced_and_unannounced_oversize_and_timeout(self):
        from curl_cffi import CurlOpt
        from curl_cffi.requests import AsyncSession as RealSession
        openssl = shutil.which('openssl') or r'C:\Program Files\Git\usr\bin\openssl.exe'
        if not Path(openssl).is_file():
            self.skipTest('openssl is needed for the local trusted TLS fixture')
        cert, key = self.root / 'cert.pem', self.root / 'key.pem'
        subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(cert), '-days', '1', '-subj', '/CN=p1.douyinpic.com',
                        '-addext', 'subjectAltName=DNS:p1.douyinpic.com'], check=True, capture_output=True)
        sent, done = {}, {}
        png = b'\x89PNG\r\n\x1a\ncontent'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                path = self.path
                sent[path] = 0
                done[path] = threading.Event()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    if path != '/unknown':
                        self.send_header('Content-Length', str(64 * 1024 * 1024 if path == '/huge' else 100 if path == '/slow' else len(png)))
                    self.end_headers()
                    chunks = [png] if path == '/ok' else [b'x'] * 100 if path == '/slow' else [b'x' * 65536] * 1024
                    for chunk in chunks:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        sent[path] += len(chunk)
                        if path != '/ok':
                            time.sleep(.01 if path == '/slow' else .001)
                except OSError:
                    pass
                finally:
                    done[path].set()
        try:
            server = ThreadingHTTPServer(('127.0.0.1', 443), Handler)
        except OSError:
            self.skipTest('local TLS fixture requires an available loopback port 443')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        session_timeout = None
        class LocalSession(RealSession):
            def __init__(self, **kwargs):
                kwargs['verify'] = str(cert)
                if session_timeout is not None:
                    kwargs['curl_options'][CurlOpt.TIMEOUT_MS] = min(kwargs['curl_options'][CurlOpt.TIMEOUT_MS], session_timeout)
                super().__init__(**kwargs)
        try:
            with patch.object(self.assets, '_validated_target', AsyncMock(return_value=('p1.douyinpic.com', '127.0.0.1'))), patch.object(
                    self.assets, 'AsyncSession', LocalSession):
                saved = await self.assets.download_asset('https://p1.douyinpic.com/ok', self.root, 100)
                self.assertEqual((self.root / saved['relative_path']).read_bytes(), png)
                with self.assertRaises(self.assets.AssetLimit):
                    await self.assets.download_asset('https://p1.douyinpic.com/ok', self.root, 10)
                for endpoint, remaining in (('/huge', 100 * 1024 * 1024), ('/unknown', 65536)):
                    with self.assertRaises(RuntimeError) as caught:
                        await self.assets.download_asset('https://p1.douyinpic.com' + endpoint, self.root, remaining)
                    self.assertNotIsInstance(caught.exception, self.assets.AssetStorageError)
                    await asyncio.to_thread(done[endpoint].wait, 2)
                    self.assertLess(sent[endpoint], 4 * 1024 * 1024, 'rejected body must stop real transfer, not collect it in a queue')
                    self.assertFalse(list(self.root.rglob('*.tmp')))
                session_timeout = 50
                wait_for = asyncio.wait_for
                async def brief_wait(awaitable, timeout):
                    return await wait_for(awaitable, .05)
                with patch.object(self.assets.asyncio, 'wait_for', brief_wait):
                    start = time.monotonic()
                    with self.assertRaises(RuntimeError) as caught:
                        await self.assets.download_asset('https://p1.douyinpic.com/slow', self.root, 100)
                    self.assertNotIsInstance(caught.exception, self.assets.AssetStorageError)
                    self.assertLess(time.monotonic() - start, .3)
                    self.assertIn('/slow', sent)
                self.assertFalse(list(self.root.rglob('*.tmp')))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
