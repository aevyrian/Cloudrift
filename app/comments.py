import asyncio
import copy
import time

from .comment_channels import (CommentChannelUnavailable, DEFAULT_CHANNEL,
                               assistant_page as map_assistant_page,
                               channel_checkpoint, default_channels)
from .core import CommentBlocked


class CommentService:
    def __init__(self, store, core, channels=None):
        self.store = store
        self.core = core
        self.channels = default_channels(core) if channels is None else channels

    def queue(self, work_id, action='save', channel=DEFAULT_CHANNEL):
        self.store.comment_queue(work_id, action, channel)
        return self.store.comment_state(work_id)

    def cancel(self, work_id):
        self.store.comment_cancel(work_id)
        return self.store.comment_state(work_id)

    def recover(self):
        self.store.comment_recover()

    def assistant_page(self, work_id, page, root_id='', cursor=None, token='', action=None):
        """接收助手采集的一页原始评论数据，映射后入库并保存分页进度。

        一级评论和楼中楼共用 ``assistant_page``（内部调用 ``comment_item``），
        评论编号去重交给 ``comment_commit_page``；重复上报只做幂等写入，不回退
        checkpoint。返回新的会话 token 供助手后续页携带。
        """
        root_id = root_id or ''
        job = self.store.comment_assistant_start(work_id, action, token)
        checkpoint = channel_checkpoint(copy.deepcopy(job['checkpoint']))
        replies = checkpoint['replies']
        if root_id:
            if not replies or replies[0]['id'] != root_id:
                raise ValueError('回复分页与当前进度不一致')
            expected = replies[0]['cursor']
        else:
            expected = checkpoint['cursor']
        if cursor is None:
            cursor = expected
        elif type(cursor) is not int or not 0 <= cursor <= 2**63 - 1:
            raise ValueError('分页游标无效')
        mapped = map_assistant_page(page, cursor, root_id or None)
        items, next_cursor, more = mapped['items'], mapped['next_cursor'], mapped['has_more']
        replay = cursor < expected
        if replay:
            # 网络重试或重复上报：评论表按编号去重，进度保持在更靠后的位置。
            if not items:
                raise ValueError('评论分页为空')
        else:
            if cursor != expected:
                raise ValueError('分页游标与当前进度不一致')
            if more and (not items or next_cursor <= cursor):
                raise ValueError('评论分页未正常推进')
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
        before = self.store.comment_state(work_id)['total']
        total = mapped.get('total') if not root_id else None
        started = time.monotonic()
        if not self.store.comment_commit_page(work_id, job['token'], items, checkpoint, total,
                                              time.monotonic() - started):
            raise ValueError('评论采集会话已失效，请重新开始')
        after = self.store.comment_state(work_id)['total']
        if not replay and checkpoint['root_done'] and not replies:
            self.store.comment_end(work_id, job['token'], 'finished', '', finished=True)
        return {'accepted': True, 'token': job['token'], 'new_comments': max(0, after - before),
                'checkpoint': checkpoint, 'status': self.store.comment_state(work_id)}

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
        checkpoint = channel_checkpoint(copy.deepcopy(job['checkpoint']))
        replies = checkpoint['replies']
        if checkpoint['root_done'] and not replies:
            self.store.comment_end(work_id, token, 'finished', '', finished=True)
            return True
        channel = self.channels.get(checkpoint['channel'])
        if channel is None:
            self.store.comment_end(work_id, token, 'failed', '评论通道不可用，已保存内容保留，可稍后重试')
            return True
        if not self.store.comment_request_begin(work_id, token, now):
            return False
        root_id = replies[0]['id'] if replies else None
        cursor = replies[0]['cursor'] if replies else checkpoint['cursor']
        started = time.monotonic()
        try:
            page = await asyncio.wait_for(channel.page(work_id, cursor=cursor, root_id=root_id), timeout=15)
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
        except CommentChannelUnavailable:
            self.store.comment_end(work_id, token, 'failed', '评论通道暂不可用，已保存内容保留，可稍后重试')
        except Exception:
            self.store.comment_end(work_id, token, 'failed', '读取评论失败，已保存内容保留，可稍后重试')
        return True
