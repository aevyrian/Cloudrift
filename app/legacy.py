import asyncio
import hashlib
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path


VIDEO = {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}
IMAGE = {'.jpg', '.jpeg', '.png', '.webp', '.avif'}
EXCLUDED = {'Cache', 'Data', '@eaDir', '@Recycle'}
FILENAME = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2})-(视频|图集|实况)-(.+)$')
LEGACY_COMPARISON_BYTES = 512 * 1024 * 1024


def recorded_download(root, work_id):
    return bool(recorded_downloads(root, [work_id]))


def recorded_downloads(root, work_ids):
    work_ids = tuple(dict.fromkeys(work_ids))
    if not work_ids:
        return set()
    path = Path(root)/'DouK-Downloader.db'
    if not path.exists():
        return set()
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise RuntimeError('旧下载记录路径异常，已停止以免重复下载')
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            found = set()
            for start in range(0, len(work_ids), 900):
                batch = work_ids[start:start + 900]
                placeholders = ','.join('?' for _ in batch)
                found.update(row[0] for row in db.execute(
                    f'SELECT ID FROM download_data WHERE ID IN ({placeholders})', batch))
            return found
    except sqlite3.Error as exc:
        raise RuntimeError('旧下载记录暂时无法读取，已停止以免重复下载，恢复后可重试') from exc


def identical_legacy_copies(root, matches):
    if len(matches) < 2 or not matches[0]['files']:
        return matches
    root = Path(root).resolve()
    try:
        paths = [[root / name for name in work['files']] for work in matches]
        if any(len(files) != len(paths[0]) for files in paths):
            return matches
        total = 0
        signatures = []
        for files in paths:
            copies = []
            for index, path in enumerate(files):
                if (not path.resolve().is_relative_to(root) or path.is_symlink()
                        or path.suffix.lower() != paths[0][index].suffix.lower()):
                    return matches
                before = path.stat()
                if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                    return matches
                total += before.st_size
                # shortcut: large duplicate candidates remain unresolved; use a background comparison if needed.
                if total > LEGACY_COMPARISON_BYTES:
                    return matches
                digest = hashlib.sha256()
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
                with os.fdopen(fd, 'rb') as source:
                    opened = os.fstat(source.fileno())
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                        return matches
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(block)
                    after = os.fstat(source.fileno())
                final = path.stat()
                if not path.resolve().is_relative_to(root) or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns):
                    return matches
                copies.append((opened.st_size, digest.digest()))
            signatures.append(copies)
        return [matches[0]] if all(value == signatures[0] for value in signatures[1:]) else matches
    except (OSError, ValueError):
        return matches


def directory_works(root, folder, names, state, stopped):
    groups = {}
    parent = re.fullmatch(r'UID([^_]+)_(.+)_(?:发布|喜欢|收藏)作品', folder.name)
    for name in sorted(names):
        if stopped.is_set():
            return
        path = folder / name
        if name.startswith('.') or path.suffix.lower() not in VIDEO | IMAGE or path.is_symlink():
            continue
        stat = path.stat()
        state['scanned_files'] += 1
        if not stat.st_size:
            state['skipped_files'] += 1
            continue
        state['total_bytes'] += stat.st_size
        match = FILENAME.fullmatch(path.stem)
        kind = match[2] if match else ('视频' if path.suffix.lower() in VIDEO else '图集')
        base = re.sub(r'_\d+$', '', path.stem) if kind in {'图集', '实况'} else path.stem
        key = (folder.relative_to(root) / base).as_posix()
        page = re.search(r'_(\d+)$', path.stem) if kind in {'图集', '实况'} else None
        if key not in groups:
            tail = match[3] if match else base
            author = parent[2] if parent else tail.split('-', 1)[0] if match else folder.name
            description = tail[len(author)+1:] if tail.startswith(author+'-') else tail
            if kind in {'图集', '实况'}:
                description = re.sub(r'_\d+$', '', description)
            try:
                created = datetime.strptime(match[1], '%Y-%m-%d %H.%M.%S').isoformat() if match else datetime.fromtimestamp(stat.st_mtime).isoformat()
            except ValueError:
                created = datetime.fromtimestamp(stat.st_mtime).isoformat()
            groups[key] = ({'id': 'legacy-'+hashlib.sha256(key.encode()).hexdigest()[:24],
                            'nickname': author, 'uid': parent[1] if parent else author,
                            'desc': description, 'type': kind, 'create_time': created}, [])
        groups[key][1].append((int(page[1]) if page else 0, name, path.relative_to(root).as_posix()))
    for item, pages in groups.values():
        yield item, [page[2] for page in sorted(pages)]


class LegacyIndex:
    def __init__(self, store, root=None):
        self.store = store
        self.root = Path(root).resolve() if root else None
        self.stopped = threading.Event()
        self.task = None
        self.state = store.setting('legacy_scan', {'status': 'idle', 'scanned_files': 0, 'indexed_works': 0})

    def status(self):
        return {**self.state, 'configured': self.root is not None, 'root': str(self.root or '')}

    def start(self):
        if not self.root:
            raise ValueError('旧库目录尚未挂载')
        if self.task and not self.task.done():
            return self.status()
        self.stopped.clear()
        self.state = {**self.state, 'status': 'scanning', 'error': ''}
        self.task = asyncio.create_task(asyncio.to_thread(self.scan))
        return self.status()

    async def close(self):
        self.stopped.set()
        if self.task:
            await self.task

    def scan(self):
        state = {'status': 'scanning', 'scanned_files': 0, 'indexed_works': 0, 'skipped_files': 0,
                 'directories': 0, 'total_bytes': 0, 'started_at': time.time(), 'error': ''}
        scan_id = secrets.token_hex(12)
        batch = []
        published = 0

        def publish():
            self.state = dict(state)
            self.store.set_setting('legacy_scan', self.state)

        def walk_error(exc):
            raise exc

        try:
            if not self.root or not self.root.is_dir():
                raise ValueError('旧库不可访问，请检查只读挂载和磁盘连接')
            publish()
            for folder, dirs, names in os.walk(self.root, followlinks=False, onerror=walk_error):
                folder = Path(folder)
                excluded = EXCLUDED | ({'Cloudrift','作品'} if folder == self.root else set())
                dirs[:] = sorted(d for d in dirs if d not in excluded and not d.startswith('.') and not (folder/d).is_symlink())
                if self.stopped.is_set():
                    state['status'] = 'interrupted'
                    break
                state['directories'] += 1
                for item, files in directory_works(self.root, folder, names, state, self.stopped):
                    item['_legacy_scan'] = scan_id
                    batch.append((item, files))
                    state['indexed_works'] += 1
                    if len(batch) >= 500:
                        self.store.save_works(batch)
                        batch.clear()
                if time.monotonic() - published > 2:
                    publish()
                    published = time.monotonic()
            if batch:
                self.store.save_works(batch)
            if self.stopped.is_set():
                state['status'] = 'interrupted'
            else:
                if not state['indexed_works'] and self.store.legacy_count():
                    raise ValueError('旧库为空或未挂载，保留现有索引，请检查磁盘后重试')
                self.store.prune_legacy(scan_id)
                state['status'] = 'completed'
        except Exception as exc:
            state.update(status='failed', error=f'扫描中止（{type(exc).__name__}）：{str(exc)[:250]}；已建目录保留，可重新扫描')
        finally:
            state['updated_at'] = time.time()
            self.state = dict(state)
            try:
                self.store.set_setting('legacy_scan', self.state)
            except (OSError, sqlite3.Error):
                self.state.update(status='failed', error='索引状态保存失败，请检查磁盘后重新扫描')
