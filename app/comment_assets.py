"""Local comment attachments and bounded, credential-free image transfers."""

import asyncio
import csv
import hashlib
import ipaddress
import json
import os
import socket
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession


# Observed 2026-10-30 in real comment-list DOM: p3-pc-sign.douyinpic.com
# serves static comment emoji. API image/animation downloads remain unverified.
# Unobserved CDN families fail closed; nickname/avatars are not attachments.
ALLOWED_CDN_SUFFIXES = ('douyinpic.com',)
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_OPERATION_BYTES = 200 * 1024 * 1024
MIME_EXTENSIONS = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/gif': 'gif',
                   'image/webp': 'webp', 'image/avif': 'avif'}


class AssetLimit(RuntimeError):
    """The current manual operation has no attachment byte budget left."""


class AssetStorageError(RuntimeError):
    """Attachment storage failed; the collector must stop, retaining saved data."""


def _directory(data_root, name):
    root = Path(data_root).resolve()
    directory = root / name
    if directory.is_symlink():
        raise RuntimeError('附件目录不可用。')
    directory.mkdir(parents=True, exist_ok=True)
    if directory.resolve() != directory:
        raise RuntimeError('附件目录不可用。')
    return directory


def asset_path(data_root, asset):
    """Resolve only an existing saved attachment, never an arbitrary local file."""
    relative = asset.get('relative_path')
    if asset.get('status') != 'saved' or not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise RuntimeError('本地附件不可用。')
    parts = PurePosixPath(relative).parts
    if len(parts) < 2 or parts[0] != 'comment-assets' or any(part in ('', '.', '..') for part in parts):
        raise RuntimeError('本地附件不可用。')
    root = Path(data_root).resolve()
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise RuntimeError('本地附件不可用。')
    if not path.resolve().is_relative_to(root / 'comment-assets') or not path.is_file():
        raise RuntimeError('本地附件不可用。')
    return path


async def _validated_target(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment
                or not host.isascii() or host.endswith('.')
                or not any(host == suffix or host.endswith('.' + suffix) for suffix in ALLOWED_CDN_SUFFIXES)):
            raise ValueError
        answers = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = []
        for answer in answers:
            address = ipaddress.ip_address(answer[4][0])
            mapped = isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
            if not address.is_global or address.is_multicast or mapped:
                raise ValueError
            addresses.append(str(address))
        if not addresses:
            raise ValueError
        return host, addresses[0]
    except (ValueError, OSError, TypeError):
        raise RuntimeError('附件来源未获允许或无法安全连接。') from None


def _matches_image(header, extension):
    if extension == 'jpg':
        return header.startswith(b'\xff\xd8\xff')
    if extension == 'png':
        return header.startswith(b'\x89PNG\r\n\x1a\n')
    if extension == 'gif':
        return header[:6] in (b'GIF87a', b'GIF89a')
    if extension == 'webp':
        return header[:4] == b'RIFF' and header[8:12] == b'WEBP'
    if extension == 'avif':
        return header[4:8] == b'ftyp' and any(header[i:i + 4] in (b'avif', b'avis') for i in (8, *range(16, len(header) - 3, 4)))
    return False


async def _download(source_url, data_root, remaining_bytes):
    temporary = None
    try:
        for hop in range(4):
            host, address = await _validated_target(source_url)
            pinned = '[' + address + ']' if ':' in address else address
            # Resolve pins the checked IP while preserving the original TLS hostname.
            async with AsyncSession(trust_env=False, timeout=15, retry=0, discard_cookies=True,
                                    curl_options={CurlOpt.RESOLVE: [f'{host}:443:{pinned}'],
                                                  CurlOpt.PROXY: '', CurlOpt.NOPROXY: '*'}) as client:
                async with client.stream('GET', source_url, allow_redirects=False, discard_cookies=True,
                                         headers={'Accept': 'image/jpeg,image/png,image/gif,image/webp,image/avif'},
                                         accept_encoding='identity') as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get('location')
                        if hop == 3 or not location:
                            raise RuntimeError('附件跳转过多或无效。')
                        source_url = urljoin(source_url, location)
                        continue
                    if response.status_code != 200:
                        raise RuntimeError('附件下载未成功。')
                    extension = MIME_EXTENSIONS.get(response.headers.get('content-type', '').split(';')[0].strip().lower())
                    if extension is None:
                        raise RuntimeError('附件格式不受支持。')
                    length = response.headers.get('content-length')
                    if length is not None:
                        try:
                            length = int(length)
                        except (TypeError, ValueError):
                            raise RuntimeError('附件大小无效。') from None
                        if length < 0 or length > MAX_IMAGE_BYTES:
                            raise RuntimeError('附件超过单文件大小限制。')
                        if length > remaining_bytes:
                            raise AssetLimit('本次附件保存额度已用完，请继续保存。')
                    directory = _directory(data_root, 'comment-assets')
                    digest, size, header = hashlib.sha256(), 0, b''
                    with tempfile.NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as output:
                        temporary = Path(output.name)
                        async for chunk in response.aiter_content():
                            size += len(chunk)
                            if size > MAX_IMAGE_BYTES:
                                raise RuntimeError('附件超过单文件大小限制。')
                            if size > remaining_bytes:
                                raise AssetLimit('本次附件保存额度已用完，请继续保存。')
                            header = (header + chunk[:64])[:64]
                            digest.update(chunk)
                            output.write(chunk)
                        if not size or not _matches_image(header, extension):
                            raise RuntimeError('附件实际格式与类型不符。')
                        if length is not None and size != length:
                            raise RuntimeError('附件传输不完整。')
                        output.flush()
                        os.fsync(output.fileno())
                    sha256 = digest.hexdigest()
                    destination = directory / f'{sha256}.{extension}'
                    if destination.is_symlink():
                        raise RuntimeError('本地附件路径不可用。')
                    os.replace(temporary, destination)
                    temporary = None
                    return {'relative_path': destination.relative_to(Path(data_root).resolve()).as_posix(),
                            'sha256': sha256, 'bytes': size}
        raise RuntimeError('附件跳转过多。')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def download_asset(source_url, data_root, remaining_bytes):
    if not isinstance(remaining_bytes, int) or remaining_bytes <= 0:
        raise AssetLimit('本次附件保存额度已用完，请继续保存。')
    try:
        return await asyncio.wait_for(_download(source_url, data_root, min(remaining_bytes, MAX_OPERATION_BYTES)), timeout=15)
    except AssetLimit:
        raise
    except asyncio.CancelledError:
        raise
    except OSError:
        raise AssetStorageError('附件写入失败，请检查磁盘空间和目录权限。') from None
    except Exception:
        # Network/library errors can include signed URLs; expose only fixed text.
        raise RuntimeError('附件保存失败，来源、格式、大小或连接可能不可用。') from None


def _safe_csv(value):
    text = '' if value is None else str(value)
    if text.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def _export_document(snapshot, data_root, zipped):
    status = snapshot.get('status') or {}
    document = {'format_version': 1, 'work_id': str(snapshot['work_id']),
                'notice': '本地累计保存，非当前线上完整镜像。',
                'status': {key: status[key] for key in ('operation', 'integrity', 'saved_at', 'platform_total') if key in status},
                'items': []}
    files, missing = {}, []
    ids = ('id', 'root_id', 'reply_to_id', 'reply_to_user')
    fields = ('nickname', 'text', 'created_at', 'likes', 'first_saved_at', 'last_saved_at',
              'reply_count', 'reply_target_missing')
    for source in snapshot.get('items', []):
        item = {key: str(source.get(key) or '') for key in ids}
        item.update({key: source[key] for key in fields if key in source})
        item['attachments'] = []
        for asset in source.get('attachments', []):
            attachment = {'id': str(asset.get('id', '')), 'kind': asset.get('kind', 'unsupported'),
                          'status': asset.get('status', 'missing'), 'relative_path': ''}
            try:
                path = asset_path(data_root, asset)
            except RuntimeError:
                attachment['status'] = 'unsupported' if asset.get('status') == 'unsupported' else 'missing'
                attachment['error'] = '附件未保存或当前不可用。'
                missing.append({'comment_id': item['id'], 'asset_id': attachment['id'], 'status': attachment['status']})
            else:
                name = 'assets/' + path.name if zipped else path.relative_to(Path(data_root).resolve()).as_posix()
                attachment['relative_path'] = name
                files[name] = path
            item['attachments'].append(attachment)
        document['items'].append(item)
    document['status']['total'] = len(document['items'])
    document['status']['replies'] = sum(bool(item['root_id']) for item in document['items'])
    document['missing_attachments'] = missing
    return document, files


def _write_csv(output, document):
    fields = ('work_id', 'id', 'root_id', 'reply_to_id', 'reply_to_user', 'nickname', 'text',
              'created_at', 'likes', 'first_saved_at', 'last_saved_at', 'attachments')
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in document['items']:
        row = {key: _safe_csv(item.get(key, '')) for key in fields}
        row['work_id'] = _safe_csv(document['work_id'])
        row['attachments'] = _safe_csv(';'.join(asset['relative_path'] for asset in item['attachments'] if asset['relative_path']))
        writer.writerow(row)


def export_snapshot(snapshot, data_root, format):
    """Write one immutable local snapshot; caller deletes the returned temporary file."""
    if format not in ('json', 'csv', 'zip'):
        raise ValueError('不支持的导出格式。')
    temporary = None
    try:
        document, files = _export_document(snapshot, data_root, format == 'zip')
        directory = _directory(data_root, 'comment-exports')
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.' + format, delete=False) as output:
            temporary = Path(output.name)
        if format == 'json':
            with temporary.open('w', encoding='utf-8') as output:
                json.dump(document, output, ensure_ascii=False, indent=2)
        elif format == 'csv':
            with temporary.open('w', encoding='utf-8-sig', newline='') as output:
                _write_csv(output, document)
        else:
            import io
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr('comments.json', json.dumps(document, ensure_ascii=False, indent=2))
                csv_output = io.StringIO(newline='')
                _write_csv(csv_output, document)
                archive.writestr('comments.csv', csv_output.getvalue().encode('utf-8-sig'))
                archive.writestr('README.txt', document['notice'] + '\nIDs are strings in JSON; import CSV IDs as text.\n'
                                 + 'missing attachments: ' + json.dumps(document['missing_attachments'], ensure_ascii=False))
                for name, path in files.items():
                    # Revalidate before ZipFile streams the file; no attachment is read into RAM.
                    checked = asset_path(data_root, {'status': 'saved', 'relative_path': path.relative_to(Path(data_root).resolve()).as_posix()})
                    archive.write(checked, name)
        mime = {'json': 'application/json', 'csv': 'text/csv; charset=utf-8', 'zip': 'application/zip'}[format]
        work_id = str(snapshot['work_id'])
        label = work_id if work_id.isascii() and work_id.isdigit() else 'archive'
        return temporary, mime, f'comments-{label}.{format}'
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise RuntimeError('评论导出失败，本地存档未修改。') from None
