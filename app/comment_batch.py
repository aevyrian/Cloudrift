import hmac
import json
import re
import secrets
import time

from fastapi import HTTPException

from .comment_accounts import AccountError, cookie_fingerprint
from .comment_entry import comment_targets
from .store import stamp


class CommentBatchService:
    def __init__(self, store, comments, browser_comments):
        self.store, self.comments, self.browser = store, comments, browser_comments
        self.ready = False
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS comment_batches (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, author_id TEXT NOT NULL,
                    author_name TEXT NOT NULL, channel TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL, total INTEGER NOT NULL, added INTEGER NOT NULL DEFAULT 0,
                    skipped INTEGER NOT NULL, duplicates INTEGER NOT NULL, identity_limited INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', current_work_id TEXT NOT NULL DEFAULT '',
                    current_token TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS comment_batch_one_active ON comment_batches ((1))
                    WHERE status IN ('running','waiting_browser','paused');
                CREATE TABLE IF NOT EXISTS comment_batch_items (
                    batch_id TEXT NOT NULL, position INTEGER NOT NULL, work_id TEXT NOT NULL,
                    real_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    PRIMARY KEY(batch_id,position), UNIQUE(batch_id,real_id)
                );
            ''')

    @staticmethod
    def _public(db, row):
        if not row:
            return None
        completed = db.execute("SELECT COUNT(*) FROM comment_batch_items WHERE batch_id=? AND status='finished'", (row['id'],)).fetchone()[0]
        added = row['added']
        if row['status'] in ('running', 'waiting_browser', 'paused') and row['current_token']:
            job = db.execute('SELECT new_comments FROM comment_jobs WHERE work_id=? AND token=?', (row['current_work_id'], row['current_token'])).fetchone()
            added += job['new_comments'] if job else 0
        return {key: row[key] for key in ('id', 'author_id', 'author_name', 'channel', 'status', 'total', 'skipped', 'duplicates', 'reason', 'current_work_id', 'created_at', 'updated_at')} | {'completed': completed, 'added': added, 'identity_limited': bool(row['identity_limited'])}

    def status(self):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM comment_batches ORDER BY CASE WHEN status IN ('running','waiting_browser','paused') THEN 0 ELSE 1 END, rowid DESC LIMIT 1").fetchone()
            return {'batch': self._public(db, row)}

    def _check(self, db, channel, allow_primary, expected=None):
        role = 'primary' if channel == 'core' else 'secondary'
        actual = self.store.comment_accounts.check(db, role, expected, allow_primary=allow_primary if role == 'primary' else False)
        if channel == 'core':
            accounts = self.store.comment_accounts
            try:
                fingerprint = cookie_fingerprint(accounts._get(db, 'cookie', '') or '', accounts._get(db, 'comment_identity_salt'))
            except (TypeError, ValueError):
                fingerprint = None
            if not fingerprint or not hmac.compare_digest(fingerprint, actual):
                raise AccountError(409, '后台账号身份无法确认或与绑定大号不一致')
        return actual

    def start(self, author_id, body):
        channel = body['channel']
        if channel not in ('browser', 'core') or type(body['allow_primary']) is not bool or not re.fullmatch(r'[a-f0-9]{32}', body['request_id']):
            raise HTTPException(422, '批量请求无效')
        with self.browser.lock:
            if not self.ready:
                raise HTTPException(503, '评论任务正在恢复，请稍后重试')
            with self.store.connect() as db:
                existing = db.execute('SELECT * FROM comment_batches WHERE request_id=?', (body['request_id'],)).fetchone()
                if existing:
                    if existing['author_id'] != author_id or existing['channel'] != channel:
                        raise HTTPException(409, '请求标识已用于其他批次')
                    return {'batch': self._public(db, existing)}
            result = comment_targets(self.store, author_id)
            authors = [a for a in self.store.author_history() if a['id'] == author_id]
            if len(authors) != 1:
                raise HTTPException(404, '博主不存在或身份不唯一')
            if not result['targets']:
                raise HTTPException(409, '没有可识别原作品编号的已收录作品')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT * FROM comment_batches WHERE request_id=?', (body['request_id'],)).fetchone()
                if existing:
                    if existing['author_id'] != author_id or existing['channel'] != channel:
                        raise HTTPException(409, '请求标识已用于其他批次')
                    return {'batch': self._public(db, existing)}
                if db.execute("SELECT 1 FROM comment_batches WHERE status IN ('running','waiting_browser','paused')").fetchone():
                    raise HTTPException(409, '已有评论补全任务，请先完成或停止')
                if self.store.comment_accounts._get(db, 'comment_channel', 'browser') != channel:
                    raise HTTPException(409, '评论通道已改变，请刷新后重试')
                fingerprint = self._check(db, channel, body['allow_primary'])
                now, batch_id = stamp(), secrets.token_hex(16)
                db.execute('''INSERT INTO comment_batches(id,request_id,author_id,author_name,channel,fingerprint,status,total,skipped,duplicates,identity_limited,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,'running',?,?,?,?,?,?)''',
                    (batch_id, body['request_id'], author_id, authors[0]['name'], channel, fingerprint, len(result['targets']), result['skipped'], result['duplicates'], int(result['identity_limited']), now, now))
                db.executemany('INSERT INTO comment_batch_items(batch_id,position,work_id,real_id) VALUES (?,?,?,?)',
                               [(batch_id, i, item['work_id'], item['real_id']) for i, item in enumerate(result['targets'])])
            self.step()
            return self.status()

    def stop(self, batch_id):
        with self.browser.lock:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM comment_batches WHERE id=?', (batch_id,)).fetchone()
                if not row:
                    raise HTTPException(404, '评论补全任务不存在')
                if row['status'] in ('running', 'waiting_browser', 'paused'):
                    job = db.execute('SELECT token,new_comments FROM comment_jobs WHERE work_id=?', (row['current_work_id'],)).fetchone()
                    if job and job['token'] == row['current_token']:
                        db.execute('UPDATE comment_batches SET added=added+? WHERE id=?', (job['new_comments'], batch_id))
                    db.execute("UPDATE comment_batches SET status='stopped',reason='已停止，已保存评论保留',updated_at=? WHERE id=?", (stamp(), batch_id))
                    db.execute("UPDATE comment_jobs SET operation='stopped',reason='已停止，已保存内容保留',updated_at=? WHERE work_id=? AND token=? AND operation IN ('queued','running','browser')", (stamp(), row['current_work_id'], row['current_token']))
                result = {'batch': self._public(db, db.execute('SELECT * FROM comment_batches WHERE id=?', (batch_id,)).fetchone())}
            self.browser.sessions.pop(row['current_token'], None)
            return result

    def pause(self, batch_id):
        with self.browser.lock:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM comment_batches WHERE id=?', (batch_id,)).fetchone()
                if not row:
                    raise HTTPException(404, '评论补全任务不存在')
                if row['status'] not in ('running', 'waiting_browser', 'paused'):
                    raise HTTPException(409, '此评论补全任务不能暂停')
                db.execute("UPDATE comment_jobs SET operation='interrupted',reason='浏览器助手中断，请检查后继续',updated_at=? WHERE work_id=? AND token=? AND operation IN ('queued','running','browser')", (stamp(), row['current_work_id'], row['current_token']))
                db.execute("UPDATE comment_batches SET status='paused',reason='浏览器助手中断，请检查后手动继续',updated_at=? WHERE id=?", (stamp(), batch_id))
                result = {'batch': self._public(db, db.execute('SELECT * FROM comment_batches WHERE id=?', (batch_id,)).fetchone())}
            self.browser.sessions.pop(row['current_token'], None)
            return result

    def resume(self, batch_id, allow_primary=False):
        with self.browser.lock:
            if not self.ready:
                raise HTTPException(503, '评论任务正在恢复，请稍后重试')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM comment_batches WHERE id=?', (batch_id,)).fetchone()
                if not row:
                    raise HTTPException(404, '评论补全任务不存在')
                if row['status'] != 'paused':
                    raise HTTPException(409, '只有已暂停的任务可以继续')
                self._check(db, row['channel'], allow_primary, row['fingerprint'])
                if row['current_work_id']:
                    job = db.execute('SELECT token,operation,integrity,new_comments FROM comment_jobs WHERE work_id=?', (row['current_work_id'],)).fetchone()
                    if not job or job['token'] != row['current_token']:
                        raise HTTPException(409, '当前作品的评论任务已变化，请停止本批任务后重新开始')
                    if job and job['operation'] in ('queued','running','browser'):
                        raise HTTPException(409, '当前作品已有评论任务，请先处理')
                    db.execute('UPDATE comment_batches SET added=added+? WHERE id=?', (job['new_comments'], batch_id))
                    if job['operation'] == 'finished' and job['integrity'] == 'finished-visible':
                        db.execute("UPDATE comment_batch_items SET status='finished' WHERE batch_id=? AND real_id=? AND status='active'", (batch_id, row['current_work_id']))
                    else:
                        db.execute("UPDATE comment_batch_items SET status='pending' WHERE batch_id=? AND real_id=? AND status='active'", (batch_id, row['current_work_id']))
                db.execute("UPDATE comment_batches SET status='running',reason='',current_work_id='',current_token='',updated_at=? WHERE id=?", (stamp(), batch_id))
            self.step()
            with self.store.connect() as db:
                return {'batch': self._public(db, db.execute('SELECT * FROM comment_batches WHERE id=?', (batch_id,)).fetchone())}

    def recover(self):
        with self.browser.lock:
            self.ready = False
            if self.comments is not None:
                self.comments.recover()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                rows = db.execute("SELECT * FROM comment_batches WHERE status IN ('running','waiting_browser')").fetchall()
                for row in rows:
                    db.execute("UPDATE comment_jobs SET operation='interrupted',reason='服务中断，已保存内容保留',updated_at=? WHERE work_id=? AND token=? AND operation IN ('queued','running','browser')", (stamp(), row['current_work_id'], row['current_token']))
                    db.execute("UPDATE comment_batches SET status='paused',reason='服务已重启，请检查后手动继续',updated_at=? WHERE id=?", (stamp(), row['id']))
            for row in rows:
                self.browser.sessions.pop(row['current_token'], None)
            self.ready = True

    def step(self):
        with self.browser.lock:
            if not self.ready:
                return
            self.browser._expire()
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM comment_batches WHERE status IN ('running','waiting_browser') LIMIT 1").fetchone()
                if not row:
                    return
                try:
                    self._check(db, row['channel'], True, row['fingerprint'])
                    if self.store.comment_accounts._get(db, 'comment_queue_paused', False):
                        raise AccountError(409, '评论队列已暂停')
                except AccountError as exc:
                    db.execute("UPDATE comment_jobs SET operation='interrupted',reason='账号已变更或评论已暂停',updated_at=? WHERE work_id=? AND token=? AND operation IN ('queued','running','browser')", (stamp(), row['current_work_id'], row['current_token']))
                    db.execute("UPDATE comment_batches SET status='paused',reason=?,updated_at=? WHERE id=?", (str(exc), stamp(), row['id']))
                    return
                if row['current_work_id']:
                    job = db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (row['current_work_id'],)).fetchone()
                    if not job or job['token'] != row['current_token']:
                        db.execute("UPDATE comment_batches SET status='paused',reason='当前作品的评论任务已变化，请检查后继续',updated_at=? WHERE id=?", (stamp(), row['id']))
                        return
                    if job['operation'] in ('queued','running','browser'):
                        return
                    if job['operation'] != 'finished' or job['integrity'] != 'finished-visible':
                        db.execute("UPDATE comment_batches SET status='paused',reason=?,updated_at=? WHERE id=?", (job['reason'] or '当前作品未完成，请检查后继续', stamp(), row['id']))
                        return
                    db.execute("UPDATE comment_batch_items SET status='finished' WHERE batch_id=? AND real_id=? AND status='active'", (row['id'], row['current_work_id']))
                    db.execute("UPDATE comment_batches SET added=added+?,current_work_id='',current_token='',status='running',updated_at=? WHERE id=?", (job['new_comments'], stamp(), row['id']))
                item = db.execute("SELECT * FROM comment_batch_items WHERE batch_id=? AND status='pending' ORDER BY position LIMIT 1", (row['id'],)).fetchone()
                if not item:
                    db.execute("UPDATE comment_batches SET status='completed',reason='本次任务已完成',updated_at=? WHERE id=?", (stamp(), row['id']))
                    return
                work = db.execute('SELECT id FROM works WHERE id=?', (item['work_id'],)).fetchone()
                aliases = db.execute('SELECT id FROM work_aliases WHERE work_id=?', (item['work_id'],)).fetchall() if item['work_id'] != item['real_id'] else []
                if not work or (item['work_id'] != item['real_id'] and (len(aliases) != 1 or aliases[0]['id'] != item['real_id'])):
                    db.execute("UPDATE comment_batches SET status='paused',reason='作品关联已变化，请检查后停止并重新开始',updated_at=? WHERE id=?", (stamp(), row['id']))
                    return
                if db.execute("SELECT 1 FROM comment_jobs WHERE work_id=? AND operation IN ('queued','running','browser')", (item['real_id'],)).fetchone():
                    db.execute("UPDATE comment_batches SET status='paused',reason='当前作品已有其他评论任务，请先处理',updated_at=? WHERE id=?", (stamp(), row['id']))
                    return
                existing = db.execute('SELECT integrity,operation FROM comment_jobs WHERE work_id=?', (item['real_id'],)).fetchone()
                action = 'continue' if existing and (existing['integrity'] == 'partial' or existing['operation'] in ('stopped','interrupted','failed')) else 'update'
                try:
                    job = self.store.comment_queue(item['real_id'], action, browser=row['channel']=='browser', account_role='secondary' if row['channel']=='browser' else 'primary', allow_primary=row['channel']=='core', db=db)
                except (AccountError, RuntimeError, ValueError) as exc:
                    db.execute("UPDATE comment_batches SET status='paused',reason=?,updated_at=? WHERE id=?", (str(exc), stamp(), row['id']))
                    return
                db.execute("UPDATE comment_batch_items SET status='active' WHERE batch_id=? AND position=?", (row['id'], item['position']))
                db.execute("UPDATE comment_batches SET status=?,current_work_id=?,current_token=?,updated_at=? WHERE id=?", ('waiting_browser' if row['channel']=='browser' else 'running', item['real_id'], job['token'], stamp(), row['id']))
            if row['channel'] == 'browser':
                self.browser.sessions[job['token']] = {'work_id': item['real_id'], 'expires_at': time.time()+1200, 'permit': None}

    def browser_current(self, batch_id):
        with self.browser.lock:
            self.browser._expire()
            with self.store.connect() as db:
                row = db.execute('SELECT * FROM comment_batches WHERE id=?', (batch_id,)).fetchone()
                if not row:
                    raise HTTPException(404, '评论补全任务不存在')
                job = db.execute('SELECT operation,token,client_id FROM comment_jobs WHERE work_id=?', (row['current_work_id'],)).fetchone()
                session = self.browser.sessions.get(row['current_token'])
                valid = row['channel']=='browser' and row['status']=='waiting_browser' and job and job['operation']=='browser' and job['token']==row['current_token'] and session and session['work_id']==row['current_work_id']
                return {'batch': self._public(db, row), 'work': {'work_id': row['current_work_id'], 'token': row['current_token'], 'claimed': bool(job['client_id'])} if valid else None}
