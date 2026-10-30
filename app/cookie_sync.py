import asyncio
import hashlib
import hmac
import secrets
import time

from curl_cffi.requests import AsyncSession


CHECK_URL = 'https://www.douyin.com/aweme/v1/web/notice/count/?aid=6383&device_platform=webapp'
FAILURES = {
    'permission_denied': '未取得浏览器授权，原配置已保留。',
    'not_logged_in': '请先在当前浏览器登录抖音。',
    'read_failed': '浏览器未能读取 Cookie，原配置已保留。',
    'invalid_cookie': '读取的 Cookie 不完整，原配置已保留。',
    'verify_failed': '无法确认抖音登录状态，原配置已保留。',
    'expired': '请求已过期，请重新操作。',
    'changed': '配置已变化，请重新操作。',
    'apply_failed': '更新未完成，原配置已保留。',
    'rollback_failed': '更新未完成，原设置或下载核心需要检查。',
}


async def verify_cookie(cookie):
    try:
        async with AsyncSession(impersonate='chrome', timeout=15) as client:
            response = await client.get(CHECK_URL, headers={
                'Cookie': cookie, 'Accept': 'application/json', 'Referer': 'https://www.douyin.com/',
            }, allow_redirects=False)
        if response.status_code != 200:
            return False
        data = response.json()
        return isinstance(data, dict) and type(data.get('status_code')) is int and data['status_code'] == 0 and isinstance(data.get('notice_count'), list)
    except Exception:
        return False


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).digest()


class CookieAccount:
    def __init__(self, store, service):
        self.store = store
        self.service = service
        self.tasks = {}
        self.memory = None

    def invalidate(self):
        self.memory = None
        for task in self.tasks.values():
            task.cancel()

    def current(self, schedule=True):
        cookie = self.store.setting('cookie', '') or ''
        if not cookie:
            return {'cookie_account': None, 'cookie_account_status': 'unconfigured', 'cookie_account_loading': False}
        fingerprint = _digest(cookie).hex()
        cache = self.memory if self.memory and self.memory.get('digest') == fingerprint else self.store.setting('cookie_account_cache', None)
        if isinstance(cache, dict) and cache.get('digest') == fingerprint:
            account = cache.get('account')
            if isinstance(account, dict):
                account = {key: account.get(key, '')[:100] if isinstance(account.get(key, ''), str) else '' for key in ('nickname', 'unique_id')}
                if not any(account.values()):
                    account = None
            else:
                account = None
            return {'cookie_account': account, 'cookie_account_status': 'ready' if account else 'unavailable', 'cookie_account_loading': False}
        if schedule and fingerprint not in self.tasks:
            task = asyncio.create_task(self._probe(fingerprint))
            self.tasks[fingerprint] = task
            task.add_done_callback(lambda done: self.tasks.pop(fingerprint, None))
        return {'cookie_account': None, 'cookie_account_status': 'unavailable', 'cookie_account_loading': fingerprint in self.tasks}

    async def _probe(self, fingerprint):
        try:
            cookie = self.store.setting('cookie', '') or ''
            if _digest(cookie).hex() != fingerprint:
                return
            try:
                account = await self.service.core.cookie_account(cookie)
            except Exception:
                account = None
            if isinstance(account, dict):
                account = {key: account.get(key, '')[:100] if isinstance(account.get(key, ''), str) else '' for key in ('nickname', 'unique_id')}
                if not any(account.values()):
                    account = None
            else:
                account = None
            if _digest(self.store.setting('cookie', '') or '').hex() != fingerprint:
                return
            self.memory = {'digest': fingerprint, 'account': account}
            self.store.set_setting('cookie_account_cache', self.memory)
        except Exception:
            return

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class _ConfigurationChanged(Exception):
    pass


class CookieSync:
    WAIT_SECONDS = 600
    APPLY_SECONDS = 1800
    ACTIVE = {'waiting', 'validating', 'waiting_apply', 'applying'}

    def __init__(self, store, service, account=None):
        self.store = store
        self.service = service
        self.account = account
        self.item = None
        self.task = None
        self.create_lock = asyncio.Lock()
        self.recovering = False

    def _owner_ok(self, owner_hash):
        current = self.store.setting('access_token', '')
        return isinstance(current, str) and bool(current) and hmac.compare_digest(owner_hash, _digest(current))

    def _owner(self, owner):
        if not isinstance(owner, str) or not owner or not self._owner_ok(_digest(owner)):
            raise ValueError('请求不存在或已过期，请重新操作。')
        return _digest(owner)

    def _expire(self):
        if self.item and self.item['status'] == 'waiting' and time.monotonic() >= self.item['deadline']:
            self._fail(self.item, 'expired')

    @staticmethod
    def _fail(item, reason):
        item.update(status='failed', reason=reason, message=FAILURES[reason])

    @staticmethod
    def _public(item):
        result = {key: item[key] for key in ('id', 'status', 'message')}
        if item.get('reason'):
            result['reason'] = item['reason']
        result['expires_in'] = max(0, int(item['deadline'] - time.monotonic())) if item['status'] in CookieSync.ACTIVE else 0
        return result

    async def create(self, owner):
        async with self.create_lock:
            owner_hash = self._owner(owner)
            self._expire()
            if self.item and self.item['status'] in self.ACTIVE:
                if hmac.compare_digest(self.item['owner'], owner_hash):
                    return self._public(self.item)
                old_item = self.item
                await self.close()
                if old_item.get('reason') == 'rollback_failed':
                    raise ValueError(FAILURES['rollback_failed'])
                self._fail(old_item, 'changed')
                owner_hash = self._owner(owner)
            cookie = self.store.setting('cookie', '') or ''
            self.item = {'id': secrets.token_urlsafe(32), 'owner': owner_hash, 'original': _digest(cookie),
                         'status': 'waiting', 'message': '等待浏览器读取 Cookie。',
                         'deadline': time.monotonic() + self.WAIT_SECONDS}
            return self._public(self.item)

    def _get(self, request_id, owner):
        owner_hash = self._owner(owner)
        self._expire()
        if (not self.item or not isinstance(request_id, str)
                or not hmac.compare_digest(request_id.encode('utf-8'), self.item['id'].encode('ascii'))
                or not hmac.compare_digest(owner_hash, self.item['owner'])):
            raise ValueError('请求不存在或已过期，请重新操作。')
        return self.item

    def view(self, request_id, owner):
        return self._public(self._get(request_id, owner))

    def submit(self, request_id, owner, cookie=None, error=None):
        item = self._get(request_id, owner)
        if item['status'] != 'waiting':
            raise ValueError('请求已使用或过期，请重新操作。')
        if error is not None:
            reason = error if isinstance(error, str) and error in {'permission_denied', 'not_logged_in', 'read_failed'} else 'read_failed'
            self._fail(item, reason)
            return self._public(item)
        if not isinstance(cookie, str) or not cookie.strip() or len(cookie) > 30000 or '=' not in cookie or any(ord(c) < 32 or ord(c) == 127 for c in cookie):
            self._fail(item, 'invalid_cookie')
            return self._public(item)
        item.update(status='validating', message='正在验证抖音登录状态。', deadline=time.monotonic() + self.APPLY_SECONDS)
        self.task = asyncio.create_task(self._run(item, cookie.strip()))
        return self._public(item)

    async def _run(self, item, cookie):
        try:
            async with asyncio.timeout(self.APPLY_SECONDS):
                if not self._owner_ok(item['owner']):
                    self._fail(item, 'changed')
                    return
                item.update(status='waiting_apply', message='正在优先更新登录信息。')
                context = self.service.configuration() if hasattr(self.service, 'configuration') else self.service.lock
                async with context:
                    if await verify_cookie(cookie) is not True:
                        self._fail(item, 'verify_failed')
                        return
                    old = self.store.setting('cookie', '') or ''
                    old_account = self.store.setting('cookie_account_cache', None)
                    if not self._owner_ok(item['owner']) or not hmac.compare_digest(_digest(old), item['original']):
                        self._fail(item, 'changed')
                        return
                    directory = self.store.setting('download_dir', '') or ''
                    item.update(status='applying', message='正在更新下载设置。')
                    write_attempted = False
                    try:
                        await self.service.core.start(cookie, directory)
                        if not self.service.core.ready:
                            raise RuntimeError('core not ready')
                        if not self._owner_ok(item['owner']) or not hmac.compare_digest(_digest(self.store.setting('cookie', '') or ''), item['original']):
                            raise _ConfigurationChanged
                        write_attempted = True
                        self.store.set_setting('cookie', cookie)
                        self.store.set_setting('cookie_account_cache', None)
                        if self.account:
                            self.account.invalidate()
                    except BaseException as error:
                        self.recovering = True
                        try:
                            current = self.store.setting('cookie', '') or ''
                            if write_attempted and current == cookie and current != old:
                                self.store.set_setting('cookie', old)
                                self.store.set_setting('cookie_account_cache', old_account)
                                if self.account:
                                    self.account.invalidate()
                                current = old
                            await self.service.core.start(current, self.store.setting('download_dir', '') or '')
                            if not self.service.core.ready:
                                raise RuntimeError('core not ready')
                            self._fail(item, 'changed' if isinstance(error, _ConfigurationChanged) else 'apply_failed')
                        except BaseException:
                            self._fail(item, 'rollback_failed')
                        finally:
                            self.recovering = False
                        raise
                    item.update(status='success', message='Cookie 已验证并更新。')
        except asyncio.CancelledError:
            if item['status'] != 'failed':
                self._fail(item, 'apply_failed')
            raise
        except TimeoutError:
            if item['status'] != 'failed':
                self._fail(item, 'expired')
        except Exception:
            if item['status'] != 'failed':
                self._fail(item, 'verify_failed' if item['status'] == 'validating' else 'apply_failed')

    async def close(self):
        task, item = self.task, self.item
        if task and not task.done():
            if not self.recovering and not task.cancelling():
                task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    raise
        if item and item['status'] in self.ACTIVE:
            self._fail(item, 'apply_failed')
