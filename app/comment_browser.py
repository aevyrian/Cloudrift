import secrets
import re
import threading
import time

from .comments import advance_checkpoint
from .core import CommentBlocked, parse_comment_page


class BrowserError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class BrowserCommentService:
    def __init__(self, store):
        self.store = store
        self.sessions = {}
        self.lock = threading.RLock()

    def _expire(self):
        now = time.time()
        for token, session in list(self.sessions.items()):
            if now >= session['expires_at']:
                self.store.comment_end(session['work_id'], token, 'interrupted', '浏览器会话已过期，已保存内容保留')
                del self.sessions[token]

    def expire(self):
        with self.lock:
            self._expire()

    @staticmethod
    def _next(job):
        checkpoint = job['checkpoint']
        if checkpoint['replies']:
            return {'root_id': checkpoint['replies'][0]['id'], 'cursor': checkpoint['replies'][0]['cursor']}
        if not checkpoint['root_done']:
            return {'root_id': '', 'cursor': checkpoint['cursor']}
        return None

    def _session(self, work_id, token, client=None, fingerprint=None):
        self._expire()
        session = self.sessions.get(token)
        job = self.store.comment_job(work_id)
        if not session or session['work_id'] != work_id or not job or not secrets.compare_digest(job['token'], token) or job['operation'] != 'browser' or job['channel'] != 'browser':
            raise BrowserError(409, '浏览器会话已失效，请重新开始')
        if not client or not fingerprint or job['client_id'] != client or job['account_fingerprint'] != fingerprint:
            raise BrowserError(409, '浏览器身份未领取或已变更')
        with self.store.connect() as db:
            self.store.comment_accounts.check(db, job['account_role'], fingerprint, allow_primary=True)
        return session, job

    def _result(self, session, job):
        return {'work_id': session['work_id'], 'token': job['token'], 'expires_at': session['expires_at'],
                'next': self._next(job), 'status': self.store.comment_state(job['work_id']), 'account_role': job['account_role']}

    def start(self, work_id, action, account_role, allow_primary=False):
        with self.lock:
            self._expire()
            try:
                job = self.store.comment_queue(work_id, action, browser=True, account_role=account_role, allow_primary=allow_primary)
            except RuntimeError as exc:
                raise BrowserError(409, str(exc)) from None
            session = {'work_id': work_id, 'expires_at': time.time() + 1200, 'permit': None}
            self.sessions[job['token']] = session
            return self._result(session, job)

    def claim(self, work_id, token, fingerprint, client):
        with self.lock:
            self._expire()
            session = self.sessions.get(token)
            if not session or session['work_id'] != work_id or not re.fullmatch(r'[a-f0-9]{32}', client or ''):
                raise BrowserError(409, '浏览器会话或身份无效')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
                if not row or row['token'] != token or row['operation'] != 'browser':
                    raise BrowserError(409, '浏览器会话已失效')
                self.store.comment_accounts.check(db, row['account_role'], fingerprint, allow_primary=True)
                if row['client_id'] and (row['client_id'] != client or row['account_fingerprint'] != fingerprint):
                    raise BrowserError(409, '此评论任务已由其他标签领取')
                if db.execute("SELECT 1 FROM comment_jobs WHERE account_fingerprint=? AND work_id!=? AND operation IN ('browser','queued','running') LIMIT 1", (fingerprint, work_id)).fetchone():
                    raise BrowserError(409, '该账号已有评论任务，请先停止')
                db.execute('UPDATE comment_jobs SET client_id=?,account_fingerprint=? WHERE work_id=? AND token=?', (client, fingerprint, work_id, token))
            return self._result(session, self.store.comment_job(work_id))

    def get(self, work_id, token, client, fingerprint):
        with self.lock:
            session, job = self._session(work_id, token, client, fingerprint)
            return self._result(session, job)

    def permit(self, work_id, token, client, fingerprint, root_id, cursor):
        with self.lock:
            session, job = self._session(work_id, token, client, fingerprint)
            if type(cursor) is not int or not isinstance(root_id, str) or self._next(job) != {'root_id': root_id, 'cursor': cursor}:
                raise BrowserError(409, '评论分页已变化，请重新获取断点')
            if job['requests'] >= 200 or job['new_comments'] >= 2000:
                raise BrowserError(409, '已达到本次保存上限，请停止后继续保存')
            if session['permit']:
                return {'ready': True, 'permit': session['permit']}
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT * FROM comment_jobs WHERE work_id=?', (work_id,)).fetchone()
                if not row or row['operation'] != 'browser' or row['token'] != token or row['client_id'] != client or row['account_fingerprint'] != fingerprint:
                    raise BrowserError(409, '浏览器会话已失效')
                self.store.comment_accounts.check(db, row['account_role'], fingerprint, allow_primary=True)
                result = self.store.comment_accounts.reserve(db, fingerprint)
            if result['ready']:
                session['permit'] = secrets.token_hex(16)
                result['permit'] = session['permit']
            return result

    def cancel(self, work_id):
        with self.lock:
            self.store.comment_cancel(work_id)
            for token, session in list(self.sessions.items()):
                if session['work_id'] == work_id:
                    del self.sessions[token]
            return self.store.comment_state(work_id)

    def page(self, work_id, token, client, fingerprint, root_id, cursor, payload, permit):
        with self.lock:
            session, job = self._session(work_id, token, client, fingerprint)
            expected = self._next(job)
            if type(cursor) is not int or not isinstance(root_id, str) or expected != {'root_id': root_id, 'cursor': cursor}:
                raise BrowserError(409, '评论分页已变化，请重新获取断点')
            if job['requests'] >= 200 or job['new_comments'] >= 2000:
                raise BrowserError(409, '已达到本次保存上限，请停止后继续保存')
            if not isinstance(permit, str) or not session['permit'] or not secrets.compare_digest(session['permit'], permit):
                raise BrowserError(409, '评论请求许可无效')
            if not isinstance(payload, dict) or not isinstance(payload.get('comments'), list) or len(payload['comments']) > 100:
                raise BrowserError(422, '评论页面格式或数量无效')
            for item in payload['comments']:
                if isinstance(item, dict) and 'aweme_id' in item:
                    value = item['aweme_id']
                    if type(value) not in (str, int) or str(value) != work_id:
                        raise BrowserError(422, '评论所属作品不匹配')
            try:
                page = parse_comment_page(payload, cursor, root_id)
                checkpoint = advance_checkpoint(job['checkpoint'], page, root_id)
                committed = self.store.comment_commit_page(work_id, token, page['items'], checkpoint,
                                                           page['total'] if not root_id else None, 0)
            except CommentBlocked:
                self.store.comment_accounts.blocked(fingerprint)
                self.sessions.pop(token, None)
                raise BrowserError(409, '评论访问需要登录、验证或暂时受限，请处理后重试') from None
            except ValueError as exc:
                raise BrowserError(422, str(exc)) from None
            if not committed:
                raise BrowserError(409, '浏览器会话已失效，请重新开始')
            session['permit'] = None
            return self._result(session, self.store.comment_job(work_id))

    def finish(self, work_id, token, client, fingerprint, status):
        with self.lock:
            _, job = self._session(work_id, token, client, fingerprint)
            if status == 'complete':
                if self._next(job) is not None:
                    raise BrowserError(409, '评论尚未遍历完成')
                if not self.store.comment_browser_finish(work_id, token):
                    raise BrowserError(409, '浏览器会话已失效，请重新开始')
            elif status in ('stopped', 'failed', 'blocked'):
                if status == 'blocked':
                    self.store.comment_accounts.blocked(fingerprint)
                    self.sessions.pop(token, None)
                    return self.store.comment_state(work_id)
                reason = {'stopped': '已停止，已保存内容保留', 'failed': '评论读取失败，已保存内容保留',
                          'blocked': '评论访问需要登录、验证或暂时受限，请处理后重试'}[status]
                self.store.comment_end(work_id, token, 'stopped' if status == 'stopped' else 'failed', reason)
            else:
                raise BrowserError(422, '结束状态无效')
            self.sessions.pop(token, None)
            return self.store.comment_state(work_id)
