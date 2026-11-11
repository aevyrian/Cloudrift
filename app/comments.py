import asyncio
import copy
import hmac
import time

from .blocking import run_blocking
from .core import CommentBlocked
from .comment_accounts import AccountError, cookie_fingerprint


def advance_checkpoint(checkpoint, page, root_id):
    checkpoint = copy.deepcopy(checkpoint)
    replies = checkpoint['replies']
    items, next_cursor, more = page['items'], page['next_cursor'], page['has_more']
    if root_id:
        if more:
            replies[0]['cursor'] = next_cursor
        else:
            replies.pop(0)
    else:
        checkpoint['cursor'] = next_cursor
        checkpoint['root_done'] = not more
        known = {pending['id'] for pending in replies}
        for item in items:
            if item['reply_count'] > 0 and item['id'] not in known:
                replies.append({'id': item['id'], 'cursor': 0})
                known.add(item['id'])
    return checkpoint


class CommentService:
    def __init__(self, store, core):
        self.store = store
        self.core = core

    def queue(self, work_id, action='save', account_role=None, allow_primary=False):
        self.store.comment_queue(work_id, action, account_role=account_role, allow_primary=allow_primary)
        return self.store.comment_state(work_id)

    def cancel(self, work_id):
        self.store.comment_cancel(work_id)
        return self.store.comment_state(work_id)

    def recover(self):
        self.store.comment_recover()

    def block_account(self, fingerprint):
        try:
            self.store.comment_accounts.blocked(fingerprint)
        except AccountError:
            self.store.comment_accounts.pause('后台评论访问受限或状态不明，请处理后手动解除暂停')

    def core_identity_matches(self, expected):
        try:
            headers = self.core.app.parameter.headers
            cookies = [value for key, value in headers.items() if isinstance(key, str) and key.lower() == 'cookie']
            if len(cookies) != 1 or not isinstance(cookies[0], str):
                return False
            actual = cookie_fingerprint(cookies[0], self.store.setting('comment_identity_salt'))
            return bool(actual and expected and hmac.compare_digest(actual, expected))
        except (AttributeError, TypeError, ValueError):
            return False

    def _prepare_step(self):
        now = time.time()
        if (self.store.setting('comment_queue_paused', False)
                or now < self.store.setting('queue_resume_at', 0) or self.store.comment_media_busy()):
            return False, None
        job = self.store.comment_claim()
        if not job:
            return False, None
        work_id, token = job['work_id'], job['token']
        browser_done = job['channel'] == 'browser' and job['checkpoint']['root_done'] and not job['checkpoint']['replies']
        if not browser_done and (job['requests'] >= 200 or job['new_comments'] >= 2000 or job['active_seconds'] >= 1200):
            self.store.comment_end(work_id, token, 'stopped', '已达到本次保存上限，可继续保存')
            return True, None
        if job['channel'] == 'browser' and not browser_done:
            self.store.comment_end(work_id, token, 'interrupted', '浏览器采集未完成，已保存内容保留')
            return True, None
        asset = self.store.comment_next_asset(work_id)
        if asset:
            if not self.store.comment_request_begin(work_id, token, now, reserve=False):
                return False, None
            remaining = 200 * 1024 * 1024 - job['asset_bytes']
            if remaining <= 0:
                self.store.comment_end(work_id, token, 'stopped', '本次图片额度已用完，可继续保存')
                return True, None
            return None, ('asset', job, asset, remaining)
        checkpoint = copy.deepcopy(job['checkpoint'])
        replies = checkpoint['replies']
        if checkpoint['root_done'] and not replies:
            self.store.comment_end(work_id, token, 'finished', '', finished=True)
            return True, None
        if not self.core_identity_matches(job['account_fingerprint']):
            self.store.comment_accounts.pause('后台评论账号身份已变更，请检查后手动解除暂停')
            return True, None
        if not self.store.comment_request_begin(work_id, token, now):
            return False, None
        root_id = replies[0]['id'] if replies else None
        cursor = replies[0]['cursor'] if replies else checkpoint['cursor']
        return None, ('page', job, checkpoint, root_id, cursor)

    def _still_eligible(self, job):
        if not self.store.comment_request_begin(job['work_id'], job['token'], time.time(), reserve=False):
            return False
        if job['channel'] == 'core' and not self.core_identity_matches(job['account_fingerprint']):
            self.store.comment_accounts.pause('后台评论账号身份已变更，请检查后手动解除暂停')
            return False
        return True

    def _commit_page(self, job, checkpoint, root_id, cursor, page, started):
        if not self.core_identity_matches(job['account_fingerprint']):
            self.store.comment_accounts.pause('后台评论账号身份已变更，请检查后手动解除暂停')
            return
        items, next_cursor, more = page['items'], page['next_cursor'], page['has_more']
        if (not isinstance(items, list) or type(next_cursor) is not int or next_cursor < 0
                or type(more) is not bool or (more and (not items or next_cursor <= cursor))
                or any(item['root_id'] != (root_id or '') for item in items)):
            raise ValueError('分页无效')
        checkpoint = advance_checkpoint(checkpoint, page, root_id)
        total = page.get('total') if not root_id else None
        self.store.comment_commit_page(job['work_id'], job['token'], items, checkpoint, total, time.monotonic() - started)

    def _failed_page(self, job, blocked):
        self.block_account(job['account_fingerprint'])
        if blocked:
            self.store.comment_block(job['work_id'], job['token'])
        else:
            self.store.comment_end(job['work_id'], job['token'], 'failed', '读取评论失败，已保存内容保留，可稍后重试')

    async def step(self):
        completed, work = await run_blocking(self._prepare_step)
        if work is None:
            return completed
        kind, job, *args = work
        work_id, token = job['work_id'], job['token']
        if not await run_blocking(self._still_eligible, job):
            return False
        if kind == 'asset':
            asset, remaining = args
            from .comment_assets import AssetLimit, AssetStorageError, download_asset
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(download_asset(asset['source_url'], self.store.root, remaining), timeout=15)
                await run_blocking(self.store.comment_commit_asset, work_id, token, asset['id'], result, time.monotonic() - started)
            except AssetLimit:
                await run_blocking(self.store.comment_end, work_id, token, 'stopped', '本次图片额度已用完，可继续保存')
            except AssetStorageError:
                await run_blocking(self.store.comment_end, work_id, token, 'failed', '本地存储不可用，请检查空间后重试')
            except Exception:
                await run_blocking(self.store.comment_commit_asset, work_id, token, asset['id'], {}, time.monotonic() - started, '图片保存失败，可稍后重试')
            return True
        checkpoint, root_id, cursor = args
        started = time.monotonic()
        try:
            page = await asyncio.wait_for(self.core.comment_page(work_id, cursor=cursor, root_id=root_id), timeout=15)
            await run_blocking(self._commit_page, job, checkpoint, root_id, cursor, page, started)
        except CommentBlocked:
            await run_blocking(self._failed_page, job, True)
        except Exception:
            await run_blocking(self._failed_page, job, False)
        return True
