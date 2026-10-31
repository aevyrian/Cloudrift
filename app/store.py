import hmac
import hashlib
import math
import json
import re
import secrets
import sqlite3
import time
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .legacy import FILENAME

BEIJING = timezone(timedelta(hours=8))
FREQUENCIES = {"once": 129600, "daily": 1440, "three_days": 4320, "weekly": 10080,
               "monthly": 43200, "quarterly": 129600, "yearly": 525600}


class AmbiguousAliasError(RuntimeError):
    pass


def period_end(start, minutes):
    current = datetime.fromtimestamp(start, BEIJING)
    months = {43200: 1, 129600: 3, 525600: 12}.get(minutes)
    if months:
        year, month = divmod(current.year * 12 + current.month - 1 + months, 12)
        return current.replace(year=year, month=month + 1, day=min(current.day, monthrange(year, month + 1)[1])).timestamp()
    return start + minutes * 60


def random_daytime(start, end):
    day = datetime.fromtimestamp(start, BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    windows = []
    while day.timestamp() < end:
        lower, upper = max(start, day.replace(hour=8).timestamp()), min(end, day.replace(hour=18).timestamp())
        if upper > lower:
            windows.append((int(lower) + 1, int(upper)))
        day += timedelta(days=1)
    if not windows:
        following = datetime.fromtimestamp(start, BEIJING).replace(hour=8, minute=0, second=0, microsecond=0)
        if following.timestamp() <= start:
            following += timedelta(days=1)
        return following.timestamp() + secrets.randbelow(600)
    lower, upper = windows[secrets.randbelow(len(windows))]
    return lower + secrets.randbelow(max(1, upper - lower))


def _legacy_created(file):
    match = FILENAME.fullmatch(Path(file).stem) if file else None
    if match:
        try:
            return datetime.strptime(match[1], '%Y-%m-%d %H.%M.%S').isoformat()
        except ValueError:
            pass
    return None


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        for path in self.root.rglob("*"):
            if not path.is_symlink():
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.path = self.root / "library.sqlite3"
        self.path.touch(mode=0o600, exist_ok=True)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, link TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    accept_quality INTEGER NOT NULL DEFAULT 0,
                    accept_count INTEGER NOT NULL DEFAULT 0,
                    confirm_reason TEXT NOT NULL DEFAULT '',
                    preview TEXT NOT NULL DEFAULT '[]', author_id TEXT);
                CREATE INDEX IF NOT EXISTS task_state ON tasks(status,created_at);
                CREATE TABLE IF NOT EXISTS works (
                    id TEXT PRIMARY KEY, description TEXT, author TEXT, author_id TEXT,
                    type TEXT, created_at TEXT, files TEXT NOT NULL, metadata TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS work_author ON works(author_id);
                CREATE INDEX IF NOT EXISTS work_identity ON works(author_id,created_at,type);
                CREATE TABLE IF NOT EXISTS work_aliases (id TEXT PRIMARY KEY, work_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS favorites (work_id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS browse_history (
                    event_id TEXT PRIMARY KEY, work_id TEXT NOT NULL, url TEXT NOT NULL,
                    title TEXT NOT NULL, author_name TEXT NOT NULL, author_url TEXT NOT NULL,
                    viewed_at TEXT NOT NULL, metadata_revision INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS browse_history_time ON browse_history(viewed_at DESC,event_id DESC);
                CREATE TABLE IF NOT EXISTS comment_jobs (
                    work_id TEXT PRIMARY KEY, operation TEXT NOT NULL, integrity TEXT NOT NULL,
                    reason TEXT NOT NULL, token TEXT NOT NULL, checkpoint TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0, new_comments INTEGER NOT NULL DEFAULT 0,
                    active_seconds REAL NOT NULL DEFAULT 0, asset_bytes INTEGER NOT NULL DEFAULT 0,
                    platform_total INTEGER, saved_at TEXT, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS comments (
                    work_id TEXT NOT NULL, id TEXT NOT NULL, root_id TEXT NOT NULL,
                    reply_to_id TEXT NOT NULL, reply_to_user TEXT NOT NULL, nickname TEXT NOT NULL,
                    text TEXT NOT NULL, created_at INTEGER NOT NULL, likes INTEGER NOT NULL,
                    platform_reply_count INTEGER NOT NULL, first_saved_at TEXT NOT NULL, last_saved_at TEXT NOT NULL,
                    PRIMARY KEY(work_id,id));
                CREATE INDEX IF NOT EXISTS comment_order ON comments(work_id,root_id);
                CREATE TABLE IF NOT EXISTS comment_assets (
                    id TEXT PRIMARY KEY, work_id TEXT NOT NULL, comment_id TEXT NOT NULL,
                    kind TEXT NOT NULL, source_url TEXT NOT NULL, relative_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '',
                    bytes INTEGER NOT NULL DEFAULT 0, refresh_pending INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS comment_assets_work ON comment_assets(work_id,comment_id,status);
                CREATE TABLE IF NOT EXISTS authors (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 0, interval_minutes INTEGER NOT NULL DEFAULT 60,
                    last_checked REAL, next_check REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');
            """)
            columns = {row[1] for row in db.execute('PRAGMA table_info(authors)')}
            for name, kind in (("starred", "INTEGER NOT NULL DEFAULT 0"), ("favorite", "INTEGER NOT NULL DEFAULT 0"), ("schedule_until", "REAL NOT NULL DEFAULT 0")):
                if name not in columns:
                    db.execute(f'ALTER TABLE authors ADD COLUMN {name} {kind}')
            if 'metadata_revision' not in {row[1] for row in db.execute('PRAGMA table_info(browse_history)')}:
                db.execute('ALTER TABLE browse_history ADD COLUMN metadata_revision INTEGER NOT NULL DEFAULT 0')
            task_columns = {row[1] for row in db.execute('PRAGMA table_info(tasks)')}
            for name, kind in (("accept_count", "INTEGER NOT NULL DEFAULT 0"),
                               ("confirm_reason", "TEXT NOT NULL DEFAULT ''")):
                if name not in task_columns:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {kind}')
            if 'refresh_pending' not in {row[1] for row in db.execute('PRAGMA table_info(comment_assets)')}:
                db.execute('ALTER TABLE comment_assets ADD COLUMN refresh_pending INTEGER NOT NULL DEFAULT 0')
                db.execute("UPDATE comment_assets SET status='saved',refresh_pending=1 WHERE status='pending' AND relative_path!='' AND sha256!=''")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        self.set_settings({key: value})

    def set_settings(self, values):
        with self.connect() as db:
            db.executemany("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()])

    def set_admin_key(self, key, expected_key, first=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            settings = {row["key"]: json.loads(row["value"]) for row in db.execute(
                "SELECT key,value FROM settings WHERE key IN ('access_token','admin_key_set')")}
            if (first and settings.get("admin_key_set")) or not hmac.compare_digest(
                    settings.get("access_token", "").encode(), expected_key.encode()):
                return False
            for name, value in (("access_token", key), ("admin_key_set", True)):
                db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (name, json.dumps(value)))
        return True

    @staticmethod
    def task_dict(row):
        if not row:
            return None
        result = dict(row)
        result["preview"] = json.loads(result["preview"])
        result["accept_quality"] = bool(result["accept_quality"])
        result["accept_count"] = bool(result["accept_count"])
        result["confirm_reason"] = result["confirm_reason"] or ""
        return result

    def create_task(self, link, kind, accept_quality=False, author_id=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tasks WHERE link=? AND kind=? AND status IN ('queued','parsing','downloading','paused','awaiting_confirmation') ORDER BY created_at LIMIT 1",
                             (link, kind)).fetchone()
            if row:
                return self.task_dict(row)
            task_id = secrets.token_hex(8)
            now = stamp()
            paused = db.execute("SELECT value FROM settings WHERE key='queue_paused'").fetchone()
            paused = bool(json.loads(paused[0])) if paused else False
            db.execute("INSERT INTO tasks (id,kind,link,status,message,created_at,updated_at,accept_quality,author_id) VALUES (?,?,?,?,?,?,?,?,?)",
                       (task_id, kind, link, 'paused' if paused else 'queued', '已暂停，已保存内容保留' if paused else '', now, now, int(accept_quality), author_id))
        return self.task(task_id)

    def task(self, task_id):
        with self.connect() as db:
            return self.task_dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def tasks(self):
        with self.connect() as db:
            return [self.task_dict(row) for row in db.execute("""
                SELECT * FROM tasks WHERE status!='completed' OR id IN (
                    SELECT id FROM tasks WHERE status='completed' ORDER BY created_at DESC,rowid DESC LIMIT 300
                ) ORDER BY created_at DESC,rowid DESC
            """)]

    def update_task(self, task_id, pause_seconds=0, **values):
        allowed = {"status", "message", "completed", "total", "accept_quality", "accept_count",
                   "confirm_reason", "preview"}
        if not values or not set(values) <= allowed:
            raise ValueError("任务更新字段无效")
        if "preview" in values:
            values["preview"] = json.dumps(values["preview"], ensure_ascii=False)
        values["updated_at"] = stamp()
        with self.connect() as db:
            db.execute("UPDATE tasks SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                       (*values.values(), task_id))
            if pause_seconds:
                db.execute("INSERT INTO settings VALUES ('queue_resume_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (json.dumps(time.time() + pause_seconds),))

    def author_downloaded(self, link):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM tasks WHERE link=? AND kind='author' AND status='completed' LIMIT 1", (link,)).fetchone() is not None

    def author_queue_busy(self):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM tasks WHERE kind='author' AND status IN ('queued','parsing','downloading','paused') LIMIT 1").fetchone() is not None

    def claim_task(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = {row['key']: json.loads(row['value']) for row in db.execute("SELECT key,value FROM settings WHERE key IN ('queue_paused','queue_resume_at')")}
            if values.get('queue_paused', False) or time.time() < values.get('queue_resume_at', 0):
                return None
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE tasks SET status='parsing',message='正在解析作品',updated_at=? WHERE id=?", (stamp(), row["id"]))
        return self.task(row["id"])

    def delete_task(self, task_id):
        with self.connect() as db:
            db.execute('DELETE FROM tasks WHERE id=?', (task_id,))

    def pause_task(self, task_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row or row['status'] not in ('queued', 'parsing', 'downloading', 'paused'):
                raise ValueError('只有排队或进行中的任务可以暂停')
            db.execute("UPDATE tasks SET status='paused',message='已暂停，已保存内容保留',updated_at=? WHERE id=?", (stamp(), task_id))
            return self.task_dict(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def resume_task(self, task_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row or row['status'] != 'paused':
                raise ValueError('只有暂停的任务可以继续')
            duplicate = db.execute("SELECT 1 FROM tasks WHERE link=? AND kind=? AND id!=? AND status IN ('queued','parsing','downloading','paused','awaiting_confirmation') LIMIT 1", (row['link'], row['kind'], task_id)).fetchone()
            if duplicate:
                raise ValueError('同一链接已有进行中的任务，请等待它完成')
            db.execute("INSERT INTO settings VALUES ('queue_paused','false') ON CONFLICT(key) DO UPDATE SET value='false'")
            db.execute("UPDATE tasks SET status='queued',message='已继续，等待队列处理',updated_at=? WHERE id=?", (stamp(), task_id))
            return self.task_dict(db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())

    def pause_queue(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ids = [row[0] for row in db.execute("SELECT id FROM tasks WHERE status IN ('queued','parsing','downloading') ORDER BY rowid")]
            db.execute("INSERT INTO settings VALUES ('queue_paused','true') ON CONFLICT(key) DO UPDATE SET value='true'")
            db.execute("UPDATE tasks SET status='paused',message='已暂停，已保存内容保留',updated_at=? WHERE status IN ('queued','parsing','downloading')", (stamp(),))
            return ids

    def resume_queue(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ids = [row[0] for row in db.execute("SELECT id FROM tasks WHERE status='paused' ORDER BY rowid")]
            db.execute("INSERT INTO settings VALUES ('queue_paused','false') ON CONFLICT(key) DO UPDATE SET value='false'")
            db.execute("UPDATE tasks SET status='queued',message='已继续，等待队列处理',updated_at=? WHERE status='paused'", (stamp(),))
            return ids

    def requeue_task(self, task_id, existing_task_id=None):
        if existing_task_id is not None and (not isinstance(existing_task_id, str) or not re.fullmatch(r'[0-9a-f]{16}', existing_task_id)):
            raise ValueError('接续任务编号无效')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not task or task['status'] not in {'failed', 'interrupted', 'cancelled'}:
                raise ValueError('只有失败、中断或取消的任务可重试')
            previous = re.match(r'^已由同链接任务 #([0-9a-f]{16}) 接续，已保存内容保留。原结果：', task['message'])
            if previous:
                if existing_task_id is not None and existing_task_id != previous[1]:
                    raise ValueError('该任务已由其他任务接续')
                linked = db.execute('SELECT * FROM tasks WHERE id=? AND link=? AND kind=? AND id!=?',
                                    (previous[1], task['link'], task['kind'], task_id)).fetchone()
                if not linked:
                    raise ValueError('接续任务已不存在，请重新提交链接')
                return self.task_dict(linked)
            if existing_task_id is not None:
                duplicate = db.execute("SELECT * FROM tasks WHERE id=? AND link=? AND kind=? AND id!=? AND status IN ('queued','parsing','downloading','paused','awaiting_confirmation','completed')", (existing_task_id, task['link'], task['kind'], task_id)).fetchone()
                if not duplicate:
                    raise ValueError('指定的接续任务不存在、不匹配或未完成')
            else:
                duplicate = db.execute("SELECT * FROM tasks WHERE link=? AND kind=? AND id!=? AND status IN ('queued','parsing','downloading','paused','awaiting_confirmation') ORDER BY created_at,rowid LIMIT 1", (task['link'], task['kind'], task_id)).fetchone()
            if duplicate:
                message = f"已由同链接任务 #{duplicate['id']} 接续，已保存内容保留。原结果：{task['message']}"
                db.execute("UPDATE tasks SET status='cancelled',message=?,updated_at=? WHERE id=?", (message, stamp(), task_id))
                return self.task_dict(duplicate)
            paused = db.execute("SELECT value FROM settings WHERE key='queue_paused'").fetchone()
            paused = bool(json.loads(paused[0])) if paused else False
            db.execute("UPDATE tasks SET status=?,message=?,completed=0,updated_at=? WHERE id=?",
                       ('paused' if paused else 'queued', '已暂停，已保存内容保留' if paused else '已加入重试队列', stamp(), task_id))
        return self.task(task_id)

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE tasks SET status='interrupted',message='上次运行中断，可点击重试',updated_at=? WHERE status IN ('parsing','downloading')", (stamp(),))

    def save_work(self, item, files):
        self.save_works([(item, files)])

    def save_works(self, records):
        with self.connect() as db:
            db.executemany("INSERT INTO works VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET files=excluded.files,metadata=excluded.metadata,description=excluded.description,author=excluded.author,author_id=excluded.author_id,type=excluded.type",
                       [(str(item["id"]), item.get("desc", ""), item.get("nickname", "未知作者"),
                        item.get("sec_uid") or item.get("uid") or "", item.get("type", ""), item.get("create_time") or stamp(),
                        json.dumps(files, ensure_ascii=False), json.dumps(item, ensure_ascii=False)) for item, files in records])

    def legacy_count(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM works WHERE id GLOB 'legacy-*'").fetchone()[0]

    def prune_legacy(self, scan_id):
        with self.connect() as db:
            db.execute("DELETE FROM works WHERE id GLOB 'legacy-*' AND json_extract(metadata,'$._legacy_scan') IS NOT ?", (scan_id,))

    @staticmethod
    def work_dict(row):
        if not row:
            return None
        result = dict(row)
        result["files"] = json.loads(result["files"])
        result.pop("metadata")
        result["source"] = "legacy" if result["id"].startswith("legacy-") else "download"
        result['favorite'] = bool(result.get('favorite',False))
        return result

    def work(self, work_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
            if row is None:
                row = db.execute("SELECT w.* FROM works w JOIN work_aliases a ON a.work_id=w.id WHERE a.id=?",(work_id,)).fetchone()
                if row and db.execute("SELECT 1 FROM work_aliases WHERE work_id=? AND id!=? LIMIT 1",(row['id'],work_id)).fetchone():
                    raise AmbiguousAliasError('此旧作品已关联多个作品编号，无法可靠区分，已停止，请核对后重试')
                if row and row['id'].startswith('legacy-'):
                    files = json.loads(row['files'])
                    if not _legacy_created(files[0] if files else None):
                        raise RuntimeError('此旧作品存在无法识别时间的文件，无法可靠核对，请核对后重试')
        return self.work_dict(row)

    def link_work(self, work_id, local_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM work_aliases WHERE work_id=? AND id!=? LIMIT 1",(local_id,work_id)).fetchone():
                raise AmbiguousAliasError('此旧作品已关联另一作品编号，无法可靠区分，已停止，请核对后重试')
            db.execute("INSERT INTO work_aliases VALUES (?,?) ON CONFLICT(id) DO UPDATE SET work_id=excluded.work_id",(work_id,local_id))

    def legacy_matches(self, uid, created, kind):
        with self.connect() as db:
            rows=db.execute("SELECT * FROM works WHERE author_id=? AND created_at=? AND type=? AND id GLOB 'legacy-*'",(uid,created,kind)).fetchall()
        matches = [self.work_dict(row) for row in rows]
        return [work for work in matches if _legacy_created(work['files'][0] if work['files'] else None)]

    def legacy_unidentified(self, uid):
        with self.connect() as db:
            rows = db.execute("SELECT json_extract(files,'$[0]') FROM works WHERE author_id=? AND id GLOB 'legacy-*'",(uid,))
            return any(_legacy_created(row[0]) is None for row in rows)

    def set_favorite(self, work_id, favorite):
        work=self.work(work_id)
        if not work:
            raise ValueError('作品不存在')
        with self.connect() as db:
            if favorite:
                db.execute('INSERT OR IGNORE INTO favorites VALUES (?,?)',(work['id'],stamp()))
            else:
                db.execute('DELETE FROM favorites WHERE work_id=?',(work['id'],))

    def comment_work_id(self, local_id):
        if re.fullmatch(r'[0-9]{19}', local_id or ''):
            return local_id
        with self.connect() as db:
            rows = db.execute('SELECT id FROM work_aliases WHERE work_id=?', (local_id,)).fetchall()
        return rows[0][0] if len(rows) == 1 and re.fullmatch(r'[0-9]{19}', rows[0][0]) else None

    @staticmethod
    def _comment_state(db, work_id):
        job = db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
        counts = db.execute("SELECT COUNT(*),COALESCE(SUM(root_id!=''),0) FROM comments WHERE work_id=?", (work_id,)).fetchone()
        paused = db.execute("SELECT value FROM settings WHERE key='comment_queue_paused'").fetchone()
        return {'operation': job['operation'] if job else 'unsaved', 'integrity': job['integrity'] if job else 'unsaved',
                'reason': job['reason'] if job else '', 'total': counts[0], 'replies': counts[1],
                'saved_at': job['saved_at'] if job else None, 'platform_total': job['platform_total'] if job else None,
                'queue_paused': bool(json.loads(paused[0])) if paused else False}

    def comment_state(self, work_id):
        with self.connect() as db:
            return self._comment_state(db, work_id)

    @staticmethod
    def _comment_items(db, work_id, root_id=None, offset=0, limit=None, internal=False):
        query = '''SELECT c.*, (SELECT COUNT(*) FROM comments r WHERE r.work_id=c.work_id AND r.root_id=c.id) AS reply_count,
                   (c.reply_to_id!='' AND NOT EXISTS(SELECT 1 FROM comments p WHERE p.work_id=c.work_id AND p.id=c.reply_to_id)) AS reply_target_missing
                   FROM comments c WHERE c.work_id=?'''
        args = [work_id]
        if root_id is not None:
            query += ' AND c.root_id=?'
            args.append(root_id)
        query += ' ORDER BY c.rowid'
        if limit is not None:
            query += ' LIMIT ? OFFSET ?'
            args.extend((limit, offset))
        items = []
        for row in db.execute(query, args).fetchall():
            value = dict(row)
            value.pop('platform_reply_count')
            value['reply_target_missing'] = bool(value['reply_target_missing'])
            fields = '*' if internal else 'id,kind,status,error'
            value['attachments'] = [dict(a) for a in db.execute(
                'SELECT ' + fields + ' FROM comment_assets WHERE work_id=? AND comment_id=? ORDER BY rowid', (work_id, row['id']))]
            items.append(value)
        return items

    def comment_page(self, work_id, root_id='', offset=0, limit=50):
        with self.connect() as db:
            items = self._comment_items(db, work_id, root_id, offset, limit + 1)
        return {'items': items[:limit], 'next_offset': offset + limit if len(items) > limit else None}

    def comment_snapshot(self, work_id):
        with self.connect() as db:
            db.execute('BEGIN')
            result = {'work_id': work_id, 'status': self._comment_state(db, work_id),
                      'items': self._comment_items(db, work_id, internal=True)}
        return result

    def comment_asset(self, asset_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM comment_assets WHERE id=?', (asset_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _comment_job(row):
        if not row:
            return None
        value = dict(row)
        value['checkpoint'] = json.loads(value['checkpoint'])
        return value

    def comment_job(self, work_id):
        with self.connect() as db:
            return self._comment_job(db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone())

    def comment_queue(self, work_id, action):
        if not re.fullmatch(r'[0-9]{19}', work_id or '') or action not in {'save', 'update', 'continue'}:
            raise ValueError('作品或评论操作无效')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT INTO settings VALUES ('comment_queue_paused','false') ON CONFLICT(key) DO UPDATE SET value='false'")
            row = db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
            if row and row['operation'] in ('queued', 'running'):
                return self._comment_job(row)
            if action == 'continue':
                db.execute("UPDATE comment_assets SET refresh_pending=1 WHERE work_id=? AND status='saved' AND error!='' AND source_url!=''", (work_id,))
            checkpoint = json.loads(row['checkpoint']) if row else {'cursor': 0, 'root_done': False, 'replies': []}
            if action in ('save', 'update') or (checkpoint['root_done'] and not checkpoint['replies']):
                checkpoint = {'cursor': 0, 'root_done': False, 'replies': []}
            token = secrets.token_hex(16)
            db.execute('''INSERT INTO comment_jobs(work_id,operation,integrity,reason,token,checkpoint,updated_at)
                          VALUES (?,'queued','unsaved','',?,?,?) ON CONFLICT(work_id) DO UPDATE SET
                          operation='queued',reason='',token=excluded.token,checkpoint=excluded.checkpoint,
                          requests=0,new_comments=0,active_seconds=0,asset_bytes=0,platform_total=NULL,updated_at=excluded.updated_at''',
                       (work_id, token, json.dumps(checkpoint), stamp()))
            return self._comment_job(db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone())

    def comment_cancel(self, work_id):
        with self.connect() as db:
            db.execute("UPDATE comment_jobs SET operation='stopped',reason='已停止，已保存内容保留',updated_at=? WHERE work_id=? AND operation IN ('queued','running')", (stamp(), work_id))

    def comment_recover(self):
        with self.connect() as db:
            db.execute("UPDATE comment_jobs SET operation='interrupted',reason='服务中断，已保存内容保留',updated_at=? WHERE operation='running'", (stamp(),))

    def comment_claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM comment_jobs WHERE operation IN ('queued','running') ORDER BY CASE operation WHEN 'running' THEN 0 ELSE 1 END,rowid LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE comment_jobs SET operation='running',updated_at=? WHERE work_id=? AND operation='queued'", (stamp(), row['work_id']))
                row = db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (row['work_id'],)).fetchone()
            return self._comment_job(row)

    def comment_media_busy(self):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','parsing','downloading') LIMIT 1").fetchone())

    def comment_request_begin(self, work_id, token, now, reserve=True):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT operation,token FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
            values = {r['key']: json.loads(r['value']) for r in db.execute("SELECT key,value FROM settings WHERE key IN ('queue_resume_at','comment_next_request_at','comment_queue_paused')")}
            if (not job or job['operation'] != 'running' or job['token'] != token or values.get('comment_queue_paused')
                    or now < values.get('queue_resume_at', 0) or (reserve and now < values.get('comment_next_request_at', 0))
                    or db.execute("SELECT 1 FROM tasks WHERE status IN ('queued','parsing','downloading') LIMIT 1").fetchone()):
                return False
            if reserve:
                db.execute("INSERT INTO settings VALUES ('comment_next_request_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(now + 3),))
            return True

    def comment_block(self, work_id, token):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = db.execute("""UPDATE comment_jobs SET operation=CASE WHEN operation='stopped' THEN 'stopped' ELSE 'failed' END,
                                integrity='partial',reason=CASE WHEN operation='stopped' THEN '已停止；评论访问受限，队列已暂停'
                                ELSE '评论访问需要登录、验证或暂时受限，请处理后重试' END,updated_at=?
                                WHERE work_id=? AND token=? AND operation IN ('running','stopped')""", (stamp(), work_id, token))
            if result.rowcount:
                db.execute("INSERT INTO settings VALUES ('comment_queue_paused','true') ON CONFLICT(key) DO UPDATE SET value='true'")

    def comment_commit_page(self, work_id, token, items, checkpoint, platform_total, elapsed):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
            if not job or job['token'] != token or job['operation'] not in ('running', 'stopped', 'queued'):
                return False
            new_count, now = 0, stamp()
            for item in items:
                values = (work_id, item['id'], item['root_id'], item['reply_to_id'], item['reply_to_user'], item['nickname'],
                          item['text'], item['created_at'], item['likes'], item['reply_count'], now, now)
                inserted = db.execute('INSERT OR IGNORE INTO comments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', values).rowcount
                new_count += inserted
                if not inserted:
                    db.execute('''UPDATE comments SET root_id=?,reply_to_id=?,reply_to_user=?,nickname=?,text=?,created_at=?,likes=?,
                                  platform_reply_count=?,last_saved_at=? WHERE work_id=? AND id=?''',
                               (*values[2:10], now, work_id, item['id']))
                for index, asset in enumerate(item['attachments']):
                    asset_id = hashlib.sha256(f"{work_id}\0{item['id']}\0{index}".encode()).hexdigest()[:32]
                    db.execute('''INSERT INTO comment_assets(id,work_id,comment_id,kind,source_url,status,error) VALUES (?,?,?,?,?,?,?)
                                  ON CONFLICT(id) DO UPDATE SET
                                  kind=CASE WHEN comment_assets.status='saved' THEN comment_assets.kind ELSE excluded.kind END,
                                  source_url=CASE WHEN comment_assets.status='saved' AND excluded.source_url='' THEN comment_assets.source_url ELSE excluded.source_url END,
                                  status=CASE WHEN comment_assets.status='saved' THEN 'saved'
                                              WHEN comment_assets.source_url!=excluded.source_url OR comment_assets.kind!=excluded.kind THEN excluded.status
                                              WHEN comment_assets.status='missing' AND excluded.status='pending' THEN 'pending' ELSE comment_assets.status END,
                                  refresh_pending=CASE WHEN comment_assets.status='saved' AND excluded.status!='pending' THEN 0
                                      WHEN comment_assets.status='saved' AND (comment_assets.source_url!=excluded.source_url OR comment_assets.kind!=excluded.kind OR comment_assets.error!='') THEN 1
                                      ELSE comment_assets.refresh_pending END,
                                  error=CASE WHEN comment_assets.status='saved' AND excluded.status!='pending' THEN '新图片暂不支持，已保留本地副本'
                                             WHEN comment_assets.status='saved' THEN comment_assets.error
                                             WHEN comment_assets.source_url!=excluded.source_url OR comment_assets.kind!=excluded.kind THEN excluded.error
                                             WHEN comment_assets.status='missing' AND excluded.status='pending' THEN '' ELSE comment_assets.error END''',
                               (asset_id, work_id, item['id'], asset['kind'], asset['source_url'], asset['status'], asset['error']))
            db.execute("UPDATE comment_jobs SET checkpoint=?,requests=requests+1,new_comments=new_comments+?,active_seconds=active_seconds+?,integrity='partial',saved_at=?,platform_total=COALESCE(?,platform_total),updated_at=? WHERE work_id=? AND token=?",
                       (json.dumps(checkpoint), new_count, elapsed, now, platform_total, now, work_id, token))
            return True

    def comment_next_asset(self, work_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM comment_assets WHERE work_id=? AND (status='pending' OR refresh_pending=1) ORDER BY rowid LIMIT 1", (work_id,)).fetchone()
        return dict(row) if row else None

    def comment_commit_asset(self, work_id, token, asset_id, result, elapsed, error=''):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT token,operation FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
            if not job or job['token'] != token or job['operation'] not in ('running', 'stopped'):
                return False
            db.execute('''UPDATE comment_assets SET
                       status=CASE WHEN ? AND status='saved' THEN status WHEN ? THEN 'missing' ELSE 'saved' END,
                       relative_path=CASE WHEN ? AND status='saved' THEN relative_path ELSE ? END,
                       sha256=CASE WHEN ? AND status='saved' THEN sha256 ELSE ? END,
                       bytes=CASE WHEN ? AND status='saved' THEN bytes ELSE ? END,
                       error=CASE WHEN ? AND status='saved' THEN '图片更新失败，已保留本地副本，可继续保存' ELSE ? END,
                       refresh_pending=0 WHERE id=? AND work_id=? AND (status='pending' OR refresh_pending=1)''',
                       (bool(error), bool(error), bool(error), result.get('relative_path', ''), bool(error), result.get('sha256', ''),
                        bool(error), result.get('bytes', 0), bool(error), error, asset_id, work_id))
            db.execute('UPDATE comment_jobs SET active_seconds=active_seconds+?,asset_bytes=asset_bytes+?,saved_at=?,updated_at=? WHERE work_id=? AND token=?',
                       (elapsed, result.get('bytes', 0), stamp(), stamp(), work_id, token))
            return True

    def comment_end(self, work_id, token, operation, reason, finished=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT token,operation FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
            if not job or job['token'] != token or job['operation'] != 'running':
                return
            missing = db.execute("SELECT 1 FROM comment_assets WHERE work_id=? AND (status!='saved' OR refresh_pending=1 OR error!='') LIMIT 1", (work_id,)).fetchone()
            integrity = 'finished-visible' if finished and not missing else 'partial'
            if finished and missing:
                old_image = db.execute("SELECT 1 FROM comment_assets WHERE work_id=? AND status='saved' AND error!='' LIMIT 1", (work_id,)).fetchone()
                reason = '部分图片更新失败，已保留本地副本，可继续保存' if old_image else '评论已保存，部分附件未保存或不受支持'
            db.execute('UPDATE comment_jobs SET operation=?,integrity=?,reason=?,updated_at=? WHERE work_id=? AND token=?',
                       (operation, integrity, reason, stamp(), work_id, token))

    def record_browse_history(self, items):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in items:
                existing = db.execute('SELECT work_id,url,viewed_at FROM browse_history WHERE event_id=?', (item['event_id'],)).fetchone()
                if existing and tuple(existing) != (item['work_id'], item['url'], item['viewed_at']):
                    raise ValueError('浏览记录标识冲突，请稍后重试')
                db.execute('''INSERT INTO browse_history(event_id,work_id,url,title,author_name,author_url,viewed_at,metadata_revision)
                              VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                              title=COALESCE(NULLIF(excluded.title,''),browse_history.title),
                              author_name=COALESCE(NULLIF(excluded.author_name,''),browse_history.author_name),
                              author_url=COALESCE(NULLIF(excluded.author_url,''),browse_history.author_url),
                              metadata_revision=excluded.metadata_revision
                              WHERE excluded.metadata_revision > browse_history.metadata_revision''',
                           (item['event_id'], item['work_id'], item['url'], item['title'], item['author_name'],
                            item['author_url'], item['viewed_at'], item['metadata_revision']))
        return len(items)

    def browse_history(self, limit, before=None):
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM browse_history').fetchone()[0]
            where = 'WHERE (viewed_at,event_id) < (?,?)' if before else ''
            args = (*before.split('|'), limit + 1) if before else (limit + 1,)
            rows = db.execute('SELECT * FROM browse_history ' + where +
                              ' ORDER BY viewed_at DESC,event_id DESC LIMIT ?', args).fetchall()
        items = [dict(row) for row in rows[:limit]]
        next_cursor = items[-1]['viewed_at'] + '|' + items[-1]['event_id'] if len(rows) > limit else None
        return {'items': items, 'total': total, 'next_cursor': next_cursor}

    def library(self, q="", author="", offset=0, limit=30, seed=None, ceiling=None, favorite=False):
        where, args = ["1=1"], []
        if favorite:
            where.append('id IN (SELECT work_id FROM favorites)')
        if q:
            where.append("(description LIKE ? OR author LIKE ? OR id=? OR id=(SELECT work_id FROM work_aliases WHERE id=?))")
            args.extend((f"%{q}%", f"%{q}%", q, q))
        if author:
            where.append("(author_id=? OR author=?)")
            args.extend((author, author))
        clause = " AND ".join(where)
        with self.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM works WHERE " + clause, args).fetchone()[0]
            select='SELECT *,EXISTS(SELECT 1 FROM favorites WHERE work_id=works.id) AS favorite FROM works'
            if seed is not None and not q and not author and not favorite:
                maximum=db.execute('SELECT COALESCE(MAX(rowid),0) FROM works').fetchone()[0]
                ceiling=min(ceiling or maximum,maximum)
                rows=[]; cursor=offset
                if ceiling:
                    # 遍历索引行号的置换，避免对几十万作品全表随机排序。
                    start=seed%ceiling;step=(seed*2+1)%ceiling or 1
                    while math.gcd(step,ceiling)!=1:step=(step+1)%ceiling or 1
                    while len(rows)<limit and cursor<ceiling:
                        ids=[1+(start+i*step)%ceiling for i in range(cursor,min(cursor+limit-len(rows),ceiling))]
                        cursor+=len(ids)
                        found={r['rid']:r for r in db.execute(select.replace('SELECT *,','SELECT rowid AS rid,*,')+' WHERE rowid IN ('+','.join('?' for _ in ids)+')',ids)}
                        rows.extend(found[i] for i in ids if i in found)
                result={'next_offset':cursor,'ceiling':ceiling,'has_more':cursor<ceiling}
            else:
                rows=db.execute(select+' WHERE '+clause+' ORDER BY rowid DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
                result={'next_offset':offset+len(rows),'ceiling':None,'has_more':offset+len(rows)<total}
        items=[self.work_dict(row) for row in rows]
        for item in items:item.pop('rid',None)
        return {"items":items,"total":total,**result}

    def author_history(self):
        authors={a['url']:{**a,'registered':True} for a in self.authors()}
        with self.connect() as db:
            tasks=db.execute("SELECT * FROM tasks WHERE kind='author' ORDER BY created_at DESC,rowid DESC").fetchall()
        for row in tasks:
            task=self.task_dict(row);url=task['link']
            if url not in authors:
                authors[url]={'id':hashlib.sha256(url.encode()).hexdigest()[:16],'url':url,'name':'博主信息待获取','enabled':False,'interval_minutes':1440,'last_checked':task['updated_at'],'next_check':0,'last_error':'','registered':False}
            entry=authors[url]
            if 'last_task' not in entry:
                entry['last_task']=task['status']
                if task['preview'] and not entry['registered']:entry['name']=task['preview'][0].get('nickname') or entry['name']
                if task['status']=='failed':entry['last_error']=task['message']
        return sorted(authors.values(),key=lambda a:not a['enabled'])

    def save_author(self, author_id, url, name="", enabled=False, interval_minutes=129600):
        with self.connect() as db:
            old = db.execute("SELECT id FROM authors WHERE url=?", (url,)).fetchone()
            if old:
                author_id = old[0]
            db.execute("INSERT INTO authors(id,name,url,enabled,interval_minutes) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=CASE WHEN excluded.name='待获取作者' THEN authors.name ELSE excluded.name END,enabled=excluded.enabled,interval_minutes=excluded.interval_minutes,next_check=0,schedule_until=0",
                       (author_id, name or "待获取作者", url, int(enabled), interval_minutes))
        return next(a for a in self.authors() if a["id"] == author_id)

    def authors(self):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM authors ORDER BY rowid DESC")]
        for row in rows:
            for name in ('enabled', 'starred', 'favorite'):
                row[name] = bool(row[name])
        return rows

    def author(self, author_id):
        return next((a for a in self.authors() if a["id"] == author_id), None)

    def patch_author(self, author_id, **values):
        if not values or not set(values) <= {"enabled", "interval_minutes", "name", "last_checked", "next_check", "last_error", "starred", "favorite", "schedule_until"}:
            raise ValueError("博主更新字段无效")
        with self.connect() as db:
            db.execute("UPDATE authors SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", (*values.values(), author_id))
        return self.author(author_id)

    def due_authors(self):
        return [a for a in self.authors() if a["enabled"] and a["next_check"] <= time.time()]

    def mark_author_scheduled(self, author_id, reset=False):
        author = self.author(author_id)
        if author:
            now = time.time()
            start = now + 60 if reset else max(now + 60, author['schedule_until'])
            end = period_end(start, author['interval_minutes'])
            self.patch_author(author_id, next_check=random_daytime(start, end), schedule_until=end)

    def migrate_subscription_schedule(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM settings WHERE key='quarterly_schedule_v1'").fetchone():
                return
            now = time.time() + 60
            end = period_end(now, 129600)
            for row in db.execute('SELECT id FROM authors').fetchall():
                db.execute('UPDATE authors SET interval_minutes=129600,next_check=?,schedule_until=? WHERE id=?',
                           (random_daytime(now, end), end, row['id']))
            db.execute("INSERT INTO settings VALUES ('quarterly_schedule_v1','true')")
            db.execute("INSERT INTO settings VALUES ('interval_minutes','129600') ON CONFLICT(key) DO UPDATE SET value='129600'")

    def stats(self):
        with self.connect() as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("works", "tasks", "authors")}
