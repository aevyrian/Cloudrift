import hashlib
import hmac
import json
import re
import secrets
import threading
import time


class AccountError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


def cookie_fingerprint(cookie, salt):
    values = {}
    for part in cookie.split(';'):
        key, sep, value = part.strip().partition('=')
        if sep and key in ('uid_tt', 'uid_tt_ss', 'sessionid', 'sessionid_ss', 'sid_tt'):
            values.setdefault(key, set()).add(value.strip())
    uids = set().union(*(values.get(k, set()) for k in ('uid_tt', 'uid_tt_ss')))
    if len(uids) != 1 or not next(iter(uids), '') or not any(any(v for v in values.get(k, set())) for k in ('sessionid', 'sessionid_ss', 'sid_tt')):
        return None
    try:
        encoded = next(iter(uids)).encode('utf-8')
    except UnicodeError:
        return None
    return hmac.new(bytes.fromhex(salt), encoded, hashlib.sha256).hexdigest()


class CommentAccounts:
    def __init__(self, store):
        self.store = store
        self.bindings = {}
        self.lock = threading.RLock()
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS comment_account_requests (fingerprint TEXT NOT NULL, requested_at REAL NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS comment_account_request_time ON comment_account_requests(fingerprint, requested_at)')
            db.execute('CREATE TABLE IF NOT EXISTS comment_account_rate (fingerprint TEXT PRIMARY KEY, next_at REAL NOT NULL, count INTEGER NOT NULL)')
            for key, value in (('comment_identity_salt', secrets.token_hex(32)), ('comment_accounts_paused', True),
                               ('comment_accounts_reason', '请先绑定账号并手动解除评论暂停')):
                db.execute('INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)', (key, json.dumps(value)))
            db.execute("UPDATE comment_jobs SET operation='interrupted',reason='旧评论任务已停止，请重新选择账号',updated_at=? WHERE operation IN ('queued','running','browser') AND account_fingerprint=''", (self._stamp(),))
            self.reconcile_primary(db, self._get(db, 'cookie', '') or '')

    @staticmethod
    def _stamp():
        from .store import stamp
        return stamp()

    @staticmethod
    def _get(db, key, default=None):
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _set(db, key, value):
        db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))

    def reconcile_primary(self, db, cookie):
        salt = self._get(db, 'comment_identity_salt')
        current = cookie_fingerprint(cookie, salt) if isinstance(cookie, str) else None
        current = current or ''
        previous = self._get(db, 'comment_account_primary', '')
        secondary = self._get(db, 'comment_account_secondary', '')
        conflict = bool(current and secondary and hmac.compare_digest(current, secondary))
        if current != previous or conflict or (not current and not self._get(db, 'comment_accounts_paused', True)):
            self._set(db, 'comment_account_primary', current)
            if conflict:
                self._set(db, 'comment_account_secondary', '')
            reason = ('下载账号身份不可用，请更新下载 Cookie' if not current else
                      '下载账号与小号相同，请重新绑定不同的小号' if conflict else
                      '下载账号已变更，请检查后手动解除评论暂停')
            self._set(db, 'comment_accounts_paused', True)
            self._set(db, 'comment_accounts_reason', reason)
            db.execute("UPDATE comment_jobs SET operation='interrupted',reason='下载账号已变更或不可用，已保存内容保留',updated_at=? WHERE operation IN ('queued','running','browser')", (self._stamp(),))

    def status(self):
        with self.store.connect() as db:
            primary = self._get(db, 'comment_account_primary', '')
            secondary = self._get(db, 'comment_account_secondary', '')
            return {'default_role': 'secondary', 'identity_salt': self._get(db, 'comment_identity_salt'),
                    'paused': self._get(db, 'comment_accounts_paused', True), 'reason': self._get(db, 'comment_accounts_reason', ''),
                    'primary': {'bound': bool(primary), 'source': 'download', 'reason': '' if primary else '下载账号身份不可用，请更新下载 Cookie'},
                    'secondary': {'bound': bool(secondary)}, 'can_bind_secondary': bool(primary)}

    def bind_start(self, role):
        if role not in ('primary', 'secondary'):
            raise AccountError(422, '账号角色无效')
        if role == 'primary':
            raise AccountError(409, '大号使用下载账号，请更新下载 Cookie')
        if role == 'secondary' and not self.status()['primary']['bound']:
            raise AccountError(409, '请先绑定大号')
        nonce = secrets.token_urlsafe(32)
        self.bindings[nonce] = {'role': role, 'expires_at': time.time() + 600, 'status': 'waiting'}
        return {'nonce': nonce, 'role': role, 'expires_at': self.bindings[nonce]['expires_at']}

    def bind_view(self, nonce):
        item = self.bindings.get(nonce)
        if not item or time.time() >= item['expires_at']:
            raise AccountError(404, '绑定请求已过期')
        return {'status': item['status'], 'role': item['role'], 'identity_salt': self.status()['identity_salt'], 'expires_at': item['expires_at']}

    @staticmethod
    def valid_fingerprint(value):
        return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None

    def bind(self, nonce, fingerprint):
        with self.lock:
            return self._bind(nonce, fingerprint)

    def _bind(self, nonce, fingerprint):
        if not self.valid_fingerprint(fingerprint):
            raise AccountError(422, '账号身份无效')
        self.bind_view(nonce)
        item = self.bindings[nonce]
        if item['role'] == 'primary':
            raise AccountError(409, '大号使用下载账号，请更新下载 Cookie')
        if item['status'] != 'waiting':
            raise AccountError(409, '绑定请求已处理')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._bind_secondary(db, fingerprint)
        item['status'] = 'bound'
        return {'status': 'bound', 'role': item['role']}

    def _bind_secondary(self, db, fingerprint):
        if db.execute("SELECT 1 FROM comment_jobs WHERE operation IN ('browser','queued','running') LIMIT 1").fetchone():
            raise AccountError(409, '评论任务进行中，请先停止')
        primary = self._get(db, 'comment_account_primary', '')
        if not primary:
            raise AccountError(409, '请先更新下载 Cookie 以识别大号')
        if hmac.compare_digest(primary, fingerprint):
            raise AccountError(409, '大号和小号必须是不同账号')
        self._set(db, 'comment_account_secondary', fingerprint)
        self._set(db, 'comment_accounts_paused', True)
        self._set(db, 'comment_accounts_reason', '账号绑定已变更，请手动解除评论暂停')

    def bind_cookie(self, cookie):
        if not isinstance(cookie, str) or not cookie.strip() or len(cookie) > 30000 or any(ord(c) < 32 or ord(c) == 127 for c in cookie):
            raise AccountError(422, '小号 Cookie 无效，请检查后重试')
        with self.lock, self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            fingerprint = cookie_fingerprint(cookie, self._get(db, 'comment_identity_salt'))
            if not fingerprint:
                raise AccountError(422, '小号 Cookie 缺少有效账号身份，请重新获取')
            self._bind_secondary(db, fingerprint)
        return self.status()

    def pause(self, reason='评论已暂停'):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._set(db, 'comment_accounts_paused', True)
            self._set(db, 'comment_accounts_reason', reason)
            db.execute("UPDATE comment_jobs SET operation='interrupted',reason='评论已暂停，已保存内容保留',updated_at=? WHERE operation IN ('queued','running','browser')", (self._stamp(),))
        return self.status()

    def blocked(self, fingerprint):
        if not self.valid_fingerprint(fingerprint):
            raise AccountError(422, '账号身份无效')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._set(db, 'comment_accounts_paused', True)
            self._set(db, 'comment_accounts_reason', '评论访问受限或状态不明，请处理后手动解除暂停')
            db.execute("UPDATE comment_jobs SET operation='interrupted',reason='评论访问受限，已保存内容保留',updated_at=? WHERE operation IN ('queued','running','browser')", (self._stamp(),))
        return self.status()

    def resume(self, acknowledge):
        if acknowledge is not True:
            raise AccountError(422, '请确认已处理验证')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not self._get(db, 'comment_account_primary', '') or not self._get(db, 'comment_account_secondary', ''):
                raise AccountError(409, '请先绑定大号和小号')
            self._set(db, 'comment_accounts_paused', False)
            self._set(db, 'comment_accounts_reason', '')
            self._set(db, 'comment_queue_paused', False)
        return self.status()

    def check(self, db, role, fingerprint=None, allow_primary=False):
        if self._get(db, 'comment_accounts_paused', True):
            raise AccountError(409, '评论已暂停，请先处理并手动解除暂停')
        if role not in ('primary', 'secondary') or (role == 'primary' and allow_primary is not True):
            raise AccountError(409, '大号评论需要本次明确授权')
        bound = self._get(db, 'comment_account_' + role, '')
        if not bound or not self._get(db, 'comment_account_primary', '') or not self._get(db, 'comment_account_secondary', ''):
            raise AccountError(409, '请先绑定大号和小号')
        if fingerprint is not None and (not self.valid_fingerprint(fingerprint) or not hmac.compare_digest(bound, fingerprint)):
            raise AccountError(409, '当前账号与绑定身份不一致')
        return bound

    def reserve(self, db, fingerprint):
        now = time.time()
        db.execute('DELETE FROM comment_account_requests WHERE fingerprint=? AND requested_at<=?', (fingerprint, now - 86400))
        count, first = db.execute('SELECT COUNT(*),MIN(requested_at) FROM comment_account_requests WHERE fingerprint=?', (fingerprint,)).fetchone()
        rate = db.execute('SELECT next_at,count FROM comment_account_rate WHERE fingerprint=?', (fingerprint,)).fetchone()
        ready_at = max(rate['next_at'] if rate else 0, (first + 86400) if count >= 200 else 0)
        if now < ready_at:
            return {'ready': False, 'retry_after': max(1, int(ready_at - now + 0.999))}
        sequence = (rate['count'] if rate else 0) + 1
        delay = 3 + (8 + secrets.randbelow(8) if sequence % 15 == 0 else 0)
        db.execute('INSERT INTO comment_account_requests VALUES (?,?)', (fingerprint, now))
        db.execute('INSERT INTO comment_account_rate VALUES (?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET next_at=excluded.next_at,count=excluded.count', (fingerprint, now + delay, sequence))
        return {'ready': True}
