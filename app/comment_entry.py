import json
import re
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class CommentChannel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel: Literal['browser', 'core']


class BatchStart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    channel: Literal['browser', 'core']
    allow_primary: StrictBool = False
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class BatchResume(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allow_primary: StrictBool = False


def comment_targets(store, author_id):
    matches = [author for author in store.author_history() if author['id'] == author_id]
    if len(matches) != 1:
        raise HTTPException(404, '博主不存在或身份不唯一')
    author = matches[0]
    try:
        parsed = urlsplit(author['url'])
        path = re.fullmatch(r'/user/([A-Za-z0-9_-]{6,128})/?', parsed.path)
        valid = parsed.scheme == 'https' and parsed.hostname in ('www.douyin.com', 'douyin.com') and not parsed.username and not parsed.password and parsed.port in (None, 443) and path
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(409, '此博主主页无法确认真实身份，无法批量补全评论')
    sec_uid = path[1]
    uids = set()
    with store.connect() as db:
        for row in db.execute("SELECT preview FROM tasks WHERE kind='author' AND (link=? OR author_id=?)", (author['url'], author_id)):
            try:
                items = json.loads(row['preview'])
            except (ValueError, TypeError):
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get('sec_uid') == sec_uid and re.fullmatch(r'[0-9]{1,32}', str(item.get('uid') or '')):
                    uids.add(str(item['uid']))
        identity_limited = len(uids) != 1
        identities = [sec_uid] + (list(uids) if not identity_limited else [])
        rows = db.execute('''SELECT w.id, COUNT(a.id) AS alias_count, MIN(a.id) AS alias
            FROM works w LEFT JOIN work_aliases a ON a.work_id=w.id
            WHERE w.author_id IN (''' + ','.join('?' for _ in identities) + ''')
            GROUP BY w.id ORDER BY w.rowid DESC''', identities).fetchall()
    targets, seen, skipped, duplicates = [], set(), 0, 0
    for row in rows:
        local_id = row['id']
        real_id = local_id if re.fullmatch(r'[0-9]{19}', local_id or '') else row['alias'] if row['alias_count'] == 1 else None
        if not real_id or not re.fullmatch(r'[0-9]{19}', real_id):
            skipped += 1
        elif real_id in seen:
            duplicates += 1
        else:
            seen.add(real_id)
            targets.append({'work_id': local_id, 'real_id': real_id})
    return {'targets': targets, 'matched': len(targets), 'skipped': skipped,
            'duplicates': duplicates, 'identity_limited': identity_limited}


def register_comment_entry(app, store, authorize, batch_service=None):
    @app.get('/api/comment-channel', dependencies=[Depends(authorize)])
    def get_channel():
        channel = store.setting('comment_channel', 'browser')
        return JSONResponse({'channel': channel if channel in ('browser', 'core') else 'browser'}, headers={'Cache-Control': 'no-store'})

    @app.put('/api/comment-channel', dependencies=[Depends(authorize)])
    def put_channel(body: CommentChannel):
        store.set_setting('comment_channel', body.channel)
        return JSONResponse({'channel': body.channel}, headers={'Cache-Control': 'no-store'})

    @app.get('/api/authors/{author_id}/comment-targets', dependencies=[Depends(authorize)])
    def get_comment_targets(author_id: str):
        return JSONResponse(comment_targets(store, author_id), headers={'Cache-Control': 'no-store'})

    if batch_service:
        @app.post('/api/authors/{author_id}/comment-batches', dependencies=[Depends(authorize)])
        def start_comment_batch(author_id: str, body: BatchStart):
            return JSONResponse(batch_service.start(author_id, body.model_dump()), headers={'Cache-Control': 'no-store'})

        @app.get('/api/comment-batches/current', dependencies=[Depends(authorize)])
        def current_comment_batch():
            return JSONResponse(batch_service.status(), headers={'Cache-Control': 'no-store'})

        @app.post('/api/comment-batches/{batch_id}/stop', dependencies=[Depends(authorize)])
        def stop_comment_batch(batch_id: str):
            return JSONResponse(batch_service.stop(batch_id), headers={'Cache-Control': 'no-store'})

        @app.post('/api/comment-batches/{batch_id}/pause', dependencies=[Depends(authorize)])
        def pause_comment_batch(batch_id: str):
            return JSONResponse(batch_service.pause(batch_id), headers={'Cache-Control': 'no-store'})

        @app.post('/api/comment-batches/{batch_id}/resume', dependencies=[Depends(authorize)])
        def resume_comment_batch(batch_id: str, body: BatchResume):
            return JSONResponse(batch_service.resume(batch_id, body.allow_primary), headers={'Cache-Control': 'no-store'})

        @app.get('/api/comment-batches/{batch_id}/browser', dependencies=[Depends(authorize)])
        def browser_comment_batch(batch_id: str):
            return JSONResponse(batch_service.browser_current(batch_id), headers={'Cache-Control': 'no-store'})
