import asyncio
import copy
import time

from .core import CommentBlocked


class CommentService:
    def __init__(self, store, core):
        self.store = store
        self.core = core

    def queue(self, work_id, action='save'):
        self.store.comment_queue(work_id, action)
        return self.store.comment_state(work_id)

    def cancel(self, work_id):
        self.store.comment_cancel(work_id)
        return self.store.comment_state(work_id)

    def recover(self):
        self.store.comment_recover()

    async def step(self):
        now = time.time()
        if (self.store.setting('comment_queue_paused', False)
                or now < self.store.setting('queue_resume_at', 0) or self.store.comment_media_busy()):
            return False
        job = self.store.comment_claim()
        if not job:
            return False
        work_id, token = job['work_id'], job['token']
        if job['requests'] >= 200 or job['new_comments'] >= 2000 or job['active_seconds'] >= 1200:
            self.store.comment_end(work_id, token, 'stopped', '已达到本次保存上限，可继续保存')
            return True
        asset = self.store.comment_next_asset(work_id)
        if asset:
            if not self.store.comment_request_begin(work_id, token, now, reserve=False):
                return False
            remaining = 200 * 1024 * 1024 - job['asset_bytes']
            if remaining <= 0:
                self.store.comment_end(work_id, token, 'stopped', '本次图片额度已用完，可继续保存')
                return True
            from .comment_assets import AssetLimit, AssetStorageError, download_asset
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(download_asset(asset['source_url'], self.store.root, remaining), timeout=15)
                self.store.comment_commit_asset(work_id, token, asset['id'], result, time.monotonic() - started)
            except AssetLimit:
                self.store.comment_end(work_id, token, 'stopped', '本次图片额度已用完，可继续保存')
            except AssetStorageError:
                self.store.comment_end(work_id, token, 'failed', '本地存储不可用，请检查空间后重试')
            except Exception:
                self.store.comment_commit_asset(work_id, token, asset['id'], {}, time.monotonic() - started, '图片保存失败，可稍后重试')
            return True
        checkpoint = copy.deepcopy(job['checkpoint'])
        replies = checkpoint['replies']
        if checkpoint['root_done'] and not replies:
            self.store.comment_end(work_id, token, 'finished', '', finished=True)
            return True
        if not self.store.comment_request_begin(work_id, token, now):
            return False
        root_id = replies[0]['id'] if replies else None
        cursor = replies[0]['cursor'] if replies else checkpoint['cursor']
        started = time.monotonic()
        try:
            page = await asyncio.wait_for(self.core.comment_page(work_id, cursor=cursor, root_id=root_id), timeout=15)
            items, next_cursor, more = page['items'], page['next_cursor'], page['has_more']
            if (not isinstance(items, list) or type(next_cursor) is not int or next_cursor < 0
                    or type(more) is not bool or (more and (not items or next_cursor <= cursor))
                    or any(item['root_id'] != (root_id or '') for item in items)):
                raise ValueError('分页无效')
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
            total = page.get('total') if not root_id else None
            self.store.comment_commit_page(work_id, token, items, checkpoint, total, time.monotonic() - started)
        except CommentBlocked:
            self.store.comment_block(work_id, token)
        except Exception:
            self.store.comment_end(work_id, token, 'failed', '读取评论失败，已保存内容保留，可稍后重试')
        return True
