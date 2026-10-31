"""评论采集通道抽象。

服务端通道沿用 ``Core.comment_page``；助手通道由油猴页面采集。两个通道返回
相同的分页结构，共用 ``Core.comment_item`` 字段映射和评论表按评论 ID 去重。
"""

from .core import comment_item

# 新保存/更新优先尝试助手通道；旧 checkpoint 没有 channel 时仍按 server 续传。
DEFAULT_CHANNEL = 'assistant'
LEGACY_CHANNEL = 'server'
COMMENT_CHANNELS = ('server', 'assistant')


class CommentChannelUnavailable(RuntimeError):
    """当前通道暂时不可用（例如助手尚未连接），调用方可回退到其他通道。"""


class CommentChannel:
    name = ''

    async def page(self, work_id, cursor=0, root_id=None):
        raise NotImplementedError


class ServerCommentChannel(CommentChannel):
    name = 'server'

    def __init__(self, core):
        self.core = core

    async def page(self, work_id, cursor=0, root_id=None):
        return await self.core.comment_page(work_id, cursor=cursor, root_id=root_id)


class AssistantCommentChannel(CommentChannel):
    """助手页面采集通道。

    ``fetch`` 由后续助手采集接口提供，返回平台原始评论页::

        {'comments': [...], 'cursor': int, 'has_more': 0|1, 'total': int|None}

    页面统一经 ``comment_item`` 映射，保证与服务端通道字段一致。
    """

    name = 'assistant'

    def __init__(self, fetch=None):
        self.fetch = fetch

    async def page(self, work_id, cursor=0, root_id=None):
        if self.fetch is None:
            raise CommentChannelUnavailable('助手评论通道尚未连接')
        payload = await self.fetch(work_id, cursor=cursor, root_id=root_id)
        return assistant_page(payload, cursor, root_id)


def assistant_page(payload, cursor, root_id):
    if not isinstance(payload, dict):
        raise ValueError('助手评论页结构无效')
    comments = payload.get('comments')
    more = payload.get('has_more')
    next_cursor = payload.get('cursor')
    if (not isinstance(comments, list) or type(more) not in (int, bool) or more not in (0, 1)
            or type(next_cursor) is not int or next_cursor < 0
            or (more and (not comments or next_cursor <= cursor))):
        raise ValueError('助手评论分页结构无效')
    total = payload.get('total')
    if total is not None and (type(total) is not int or total < 0):
        raise ValueError('助手评论分页结构无效')
    return {'items': [comment_item(item, root_id) for item in comments],
            'next_cursor': next_cursor, 'has_more': bool(more), 'total': total}


def channel_checkpoint(value=None, channel=None):
    """补齐 checkpoint 的通道字段；旧数据没有 channel 时按 server 处理。"""
    checkpoint = dict(value) if isinstance(value, dict) else {}
    checkpoint.setdefault('channel', LEGACY_CHANNEL)
    checkpoint.setdefault('cursor', 0)
    checkpoint.setdefault('root_done', False)
    checkpoint.setdefault('replies', [])
    if channel and channel != checkpoint['channel']:
        return {'channel': channel, 'cursor': 0, 'root_done': False, 'replies': []}
    return checkpoint


def default_channels(core):
    return {ServerCommentChannel.name: ServerCommentChannel(core),
            AssistantCommentChannel.name: AssistantCommentChannel()}
