import asyncio
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .core import Core, download_directory, normalize_input
from .store import Store, FREQUENCIES, BEIJING
from .legacy import LegacyIndex, recorded_download, recorded_downloads
from .playback import PlaybackCache
from .cookie_sync import CookieSync


VERSION = "1.3.6"
BASE = Path(__file__).resolve().parent


def safe_error(exc, store):
    message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else f"操作失败（{type(exc).__name__}），请检查 Cookie、网络和服务日志后重试"
    try:
        secrets_to_hide = (store.setting("cookie", ""), store.setting("access_token", ""))
    except (sqlite3.Error, OSError):
        return "数据暂时无法读写，请检查磁盘空间、目录权限和数据库占用"
    for secret in secrets_to_hide:
        if secret:
            message = message.replace(secret, "[已隐藏]")
    return message[:500]


class JobService:
    def __init__(self, store, core, media_root, legacy_root=None):
        self.store, self.core = store, core
        self.media_root = Path(media_root).resolve()
        self.legacy_root = Path(legacy_root).resolve() if legacy_root else None
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.running = False
        self.worker = None
        self.queue_error = ""
        self.schedule_error = ""
        self.retry_delay = 2
        self.active_task_id = None
        self.active_download = None

    async def initialize(self):
        try:
            await self.core.start(self.store.setting("cookie", ""), self.store.setting("download_dir", ""))
        except Exception as exc:
            self.core.ready = False
            self.core.error = safe_error(exc, self.store)

    def retry(self, task_id):
        task = self.store.task(task_id)
        if not task:
            raise ValueError("任务不存在")
        return self.store.requeue_task(task_id)

    async def cancel(self, task_id):
        task = self.store.task(task_id)
        if not task:
            raise ValueError('任务不存在')
        if task['status'] in {'completed', 'cancelled'}:
            return task
        self.store.update_task(task_id, status='cancelled', message='已取消，已保存作品和断点文件保留')
        if self.active_task_id == task_id and self.active_download:
            self.active_download.cancel()
            try:
                await self.active_download
            except asyncio.CancelledError:
                pass
        return self.store.task(task_id)

    def confirm(self, task_id):
        task = self.store.task(task_id)
        if not task or task["status"] != "awaiting_confirmation":
            raise ValueError("该任务没有等待画质确认")
        self.store.update_task(task_id, status="queued", accept_quality=True, message="已接受当前任务的原项目优选质量")
        return self.store.task(task_id)

    def complete(self, work):
        root=self.legacy_root if work and work['source']=='legacy' else self.media_root
        return bool(root and work and work["files"] and all(
            (root / file).resolve().is_relative_to(root)
            and (root / file).is_file() and (root / file).stat().st_size > 0
            for file in work["files"]))

    def saved(self, item, legacy_cache=None):
        work_id=str(item['id']); work=self.store.work(work_id)
        if self.complete(work):
            if item.get('type') in {'图集','实况'} and len(work['files'])!=len(item.get('downloads',[])):
                raise RuntimeError('已保存作品的文件数量与当前作品不一致，已停止以免重复下载')
            return True
        if not self.legacy_root:
            return False
        if not self.legacy_root.is_dir() or self.store.setting('legacy_scan',{}).get('status')!='completed':
            raise RuntimeError('旧库未就绪或扫描尚未完成，已停止以免重复下载，完成后可重试')
        uid=str(item.get('uid') or '')
        if not uid:
            raise RuntimeError('作品缺少博主编号，无法核对旧库，已停止以免重复下载')
        try:
            created=datetime.fromisoformat(str(item.get('create_time',''))).isoformat(timespec='seconds')
        except ValueError as exc:
            raise RuntimeError('作品发布时间无法识别，无法核对旧库，已停止以免重复下载') from exc
        matches=self.store.legacy_matches(uid,created,item.get('type',''))
        if len(matches)>1:
            raise RuntimeError('匹配到多个旧作品，无法可靠区分，已停止以免重复下载')
        if matches:
            old=matches[0]
            expected=len(item.get('downloads',[])) if item.get('type') in {'图集','实况'} else None
            if not self.complete(old) or (expected is not None and len(old['files'])!=expected):
                raise RuntimeError('旧作品文件缺失或数量不一致，已保留原文件并停止，请核对后重试')
            self.store.link_work(work_id,old['id'])
            return True
        if legacy_cache is None:
            recorded = recorded_download(self.legacy_root, work_id)
            unidentified = self.store.legacy_unidentified(uid) if not recorded else False
        else:
            if 'recorded' not in legacy_cache:
                legacy_cache['recorded'] = recorded_downloads(self.legacy_root, legacy_cache['ids'])
            recorded = work_id in legacy_cache['recorded']
            unknown = legacy_cache.setdefault('unidentified', {})
            if not recorded and uid not in unknown:
                unknown[uid] = self.store.legacy_unidentified(uid)
            unidentified = unknown.get(uid, False)
        if recorded:
            raise RuntimeError('旧下载记录已有此作品，但无法可靠定位文件，已停止以免重复下载')
        if unidentified:
            raise RuntimeError('此博主存在无法识别时间的旧文件，已停止以免重复下载，请先核对旧文件')
        return False

    def finish(self, task, is_update, **values):
        pause = (120 if is_update else 300) + secrets.randbelow(181) if task['kind'] == 'author' else 0
        self.store.update_task(task['id'], pause_seconds=pause, **values)

    async def process_one(self):
        task = self.store.claim_task()
        if not task:
            return False
        self.active_task_id = task['id']
        self.active_download = asyncio.create_task(self.process_task(task))
        try:
            return await self.active_download
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            return True
        finally:
            self.active_task_id = self.active_download = None

    async def process_task(self, task):
        async with self.lock:
            current = self.store.task(task['id'])
            if not current or current['status'] == 'cancelled':
                return True
            is_update = task['kind'] == 'author' and self.store.author_downloaded(task['link'])
            try:
                if not self.core.ready:
                    await self.initialize()
                if not self.core.ready:
                    raise RuntimeError(self.core.error or "下载核心未就绪，请检查设置")
                # 每次重试重新解析，避免使用已过期的媒体地址。
                items = await self.core.parse(task["link"], task["kind"])
                self.store.update_task(task["id"], preview=items)
                pending, failures = [], []
                legacy_cache = {'ids': [str(item['id']) for item in items]}
                for item in items:
                    try:
                        if not self.saved(item, legacy_cache):
                            pending.append(item)
                        else:
                            is_update = True
                    except RuntimeError as exc:
                        failures.append(f"{item['id']}：{safe_error(exc, self.store)}")
                self.store.update_task(task["id"], total=len(pending) + len(failures))
                if pending and not task["accept_quality"] and self.store.setting("quality_policy", "upstream") == "confirm":
                    self.store.update_task(task["id"], status="awaiting_confirmation", message="已解析。原项目已优选画质，但未自动判定水印；请确认采用当前版本")
                    return True
                completed = 0
                for item in pending:
                    self.store.update_task(task["id"], status="downloading", completed=completed,
                                           message=f"正在下载第 {completed + 1}/{len(pending)} 个作品：{item.get('desc') or item['id']}"[:160])
                    try:
                        files = await self.core.download(item)
                        if not files or any(not (self.media_root / file).resolve().is_relative_to(self.media_root)
                                            or not (self.media_root / file).is_file()
                                            or (self.media_root / file).stat().st_size <= 0 for file in files):
                            raise RuntimeError("文件未完整保存，任务不会标记完成，可重试")
                    except RuntimeError as exc:
                        failures.append(f"{item['id']}：{safe_error(exc, self.store)}")
                        continue
                    self.store.save_work(item, files)
                    completed += 1
                    self.store.update_task(task["id"], completed=completed)
                summary = f"已完整保存 {completed} 个作品，跳过本库已有 {len(items) - completed - len(failures)} 个"
                if failures:
                    summary += f"；{len(failures)} 个未完成，可重试：" + '；'.join(failures[:3])
                self.finish(task, is_update, status="failed" if failures else "completed", completed=completed, message=summary[:1500])
                if task["author_id"] and self.store.author(task["author_id"]):
                    name = items[0].get("nickname", "") if items else ""
                    values = {"last_checked": time.time(), "last_error": summary[:500] if failures else ""}
                    if name:
                        values["name"] = name
                    self.store.patch_author(task["author_id"], **values)
            except asyncio.CancelledError:
                current = self.store.task(task['id'])
                if current:
                    self.finish(task, is_update, status='cancelled' if current['status'] == 'cancelled' else 'interrupted',
                                message=current['message'] if current['status'] == 'cancelled' else '服务停止，任务已保留，可重试')
                raise
            except Exception as exc:
                message = safe_error(exc, self.store)
                self.finish(task, is_update, status="failed", message=message)
                if task["author_id"] and self.store.author(task["author_id"]):
                    self.store.patch_author(task["author_id"], last_checked=time.time(), last_error=message)
        return True

    def schedule(self):
        now = time.time()
        if (not 8 <= datetime.fromtimestamp(now, BEIJING).hour < 18
                or now < self.store.setting('queue_resume_at', 0) or self.store.author_queue_busy()):
            return
        for author in sorted(self.store.due_authors(), key=lambda a: a['next_check'])[:1]:
            self.store.create_task(author["url"], "author", author_id=author["id"])
            self.store.mark_author_scheduled(author["id"])

    async def run(self):
        self.running = True
        try:
            async with self.lock:
                await self.initialize()
            async with asyncio.TaskGroup() as group:
                group.create_task(self.run_queue())
                group.create_task(self.run_schedule())
        finally:
            self.running = False

    async def run_queue(self):
        needs_recovery = True
        while self.running:
            try:
                if needs_recovery:
                    self.store.recover()
                    needs_recovery = False
                processed = await self.process_one()
                self.queue_error = ""
                remaining = self.store.setting('queue_resume_at', 0) - time.time()
                await asyncio.sleep(min(30, max(0.5, remaining)) if not processed else 0)
            except (sqlite3.Error, OSError) as exc:
                self.queue_error = f"数据读写暂时失败（{type(exc).__name__}），请检查磁盘空间和数据库占用；队列将重试"
                needs_recovery = True
                await asyncio.sleep(self.retry_delay)

    async def run_schedule(self):
        while self.running:
            try:
                self.schedule()
                self.schedule_error = ""
            except (sqlite3.Error, OSError) as exc:
                self.schedule_error = f"追更登记暂时失败（{type(exc).__name__}），稍后自动重试"
            await asyncio.sleep(self.retry_delay if self.schedule_error else 0.5)


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link: str = Field(min_length=1, max_length=5000)
    kind: str = "auto"
    accept_quality: bool = False


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookie: str | None = Field(default=None, max_length=30000)
    quality_policy: str | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=525600)
    download_dir: str | None = Field(default=None, max_length=1000)


class CookieSyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookie: str | None = Field(default=None, min_length=1, max_length=30000)
    error: Literal["permission_denied", "not_logged_in", "read_failed"] | None = None


class AdminKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=8, max_length=128)


class AuthorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2000)
    name: str = Field(default="", max_length=100)
    enabled: bool = False
    favorite: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=525600)
    frequency: Literal["once", "daily", "three_days", "weekly", "monthly", "quarterly", "yearly"] | None = None


class AuthorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=525600)
    frequency: Literal["once", "daily", "three_days", "weekly", "monthly", "quarterly", "yearly"] | None = None
    starred: bool | None = None
    favorite: bool | None = None


class TaskBatchInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['retry', 'cancel', 'delete']
    ids: list[str] = Field(min_length=1, max_length=1000)


class FavoriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    favorite: bool


def make_app(data_dir=None, media_dir=None, token=None, core=None, legacy_dir=None):
    os.umask(0o077)
    data_dir = Path(data_dir or os.environ.get("APP_DATA", "data"))
    media_dir = Path(media_dir or os.environ.get("APP_MEDIA", "media")).resolve()
    store = Store(data_dir)
    # APP_TOKEN seeds a fresh install only; a later change must survive restart.
    seed = token or os.environ.get("APP_TOKEN")
    if seed and not store.setting("access_token"):
        store.set_admin_key(seed, "")
    core = core or Core(os.environ.get("APP_CORE", str(BASE.parent / "vendor" / "TikTokDownloader")), data_dir, media_dir)
    legacy = LegacyIndex(store, legacy_dir or os.environ.get("APP_LEGACY"))
    service = JobService(store, core, media_dir, legacy.root)
    playback = PlaybackCache(data_dir / 'playback-cache')
    cookie_sync = CookieSync(store, service)
    count_cache, count_requests = {}, {}
    count_limit = asyncio.Semaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        store.migrate_subscription_schedule()
        worker = asyncio.create_task(service.run())
        service.worker = worker
        if legacy.root and (not store.legacy_count() or legacy.state.get('status') in {'scanning', 'interrupted'}):
            legacy.start()
        try:
            yield
        finally:
            pending_counts = list(count_requests.values())
            for task in pending_counts:
                task.cancel()
            await asyncio.gather(*pending_counts, return_exceptions=True)
            await cookie_sync.close()
            await playback.close()
            await legacy.close()
            service.running = False
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            await core.close()

    app = FastAPI(title="Cloudrift", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service, app.state.store, app.state.legacy = service, store, legacy
    app.state.playback = playback
    app.state.cookie_sync = cookie_sync

    def authorize(request: Request):
        supplied = request.headers.get("Authorization", "")
        supplied = supplied[7:] if supplied.startswith("Bearer ") else ""
        if request.url.path.startswith("/api/media/"):
            supplied = supplied or request.query_params.get("token", "")
        current = store.setting("access_token", "")
        if not supplied or not current or not hmac.compare_digest(supplied.encode(), current.encode()):
            raise HTTPException(401, "管理员密钥不正确或已更改，请重新登录")
        return supplied

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": safe_error(exc, store)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "输入格式或长度不正确，请检查表单中的链接、间隔和设置"}, status_code=422)

    @app.get("/health")
    async def health():
        alive = bool(service.running and service.worker and not service.worker.done())
        healthy = alive and not service.queue_error and not service.schedule_error
        return JSONResponse({"status": "ok" if healthy else "degraded", "version": VERSION,
                             "core_ready": core.ready, "worker_alive": alive, "queue_error": service.queue_error or service.schedule_error},
                            status_code=200 if healthy else 503)

    @app.get("/api/status", dependencies=[Depends(authorize)])
    def status():
        return {"version": VERSION, "core_ready": core.ready, "core_error": core.error,
                "worker_alive": bool(service.running and service.worker and not service.worker.done()),
                "queue_error": service.queue_error or service.schedule_error,
                "cookie_configured": bool(store.setting("cookie", "")), "stats": store.stats(), "media_root": str(media_dir)}

    @app.get("/api/auth")
    async def auth_status():
        return JSONResponse({"setup_required": not store.setting("admin_key_set", False),
                             "requires_current_key": bool(store.setting("access_token"))},
                            headers={"Cache-Control": "no-store"})

    def save_admin_key(body, expected, first=False):
        if not body.key.isascii() or any(char.isspace() or not char.isprintable() for char in body.key):
            raise ValueError("密钥请用 8–128 位英文字母、数字或符号，不含空格")
        if not store.set_admin_key(body.key, expected, first):
            raise HTTPException(409 if first else 401, "密钥状态已改变，请刷新后重新登录")
        # The old generated-key export is obsolete once the owner sets a key.
        try:
            (data_dir / "access.key").unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": True}

    @app.post("/api/auth/setup")
    async def setup_admin(body: AdminKeyInput, request: Request):
        if store.setting("admin_key_set", False):
            raise HTTPException(409, "管理员密钥已设置，请登录后到设置页修改")
        expected = authorize(request) if store.setting("access_token") else ""
        return save_admin_key(body, expected, first=True)

    @app.put("/api/auth/key")
    async def change_admin(body: AdminKeyInput, current=Depends(authorize)):
        return save_admin_key(body, current)

    def settings_response():
        return {"cookie_configured": bool(store.setting("cookie", "")),
                "quality_policy": store.setting("quality_policy", "upstream"),
                "interval_minutes": store.setting("interval_minutes", 60), "script_token": store.setting("access_token", ""),
                "download_dir": str(download_directory(store.setting("download_dir", ""), media_dir)),
                "media_root": str(media_dir)}

    @app.get("/api/settings", dependencies=[Depends(authorize)])
    async def settings():
        return settings_response()

    @app.post("/api/cookie-sync")
    async def create_cookie_sync(owner=Depends(authorize)):
        try:
            result = await cookie_sync.create(owner)
        except ValueError:
            raise HTTPException(409, "已有同步请求正在处理，请稍后重试") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/api/cookie-sync/{request_id}")
    async def get_cookie_sync(request_id: str, owner=Depends(authorize)):
        try:
            result = cookie_sync.view(request_id, owner)
        except ValueError:
            raise HTTPException(404, "Cookie 同步请求不存在或已过期，请重新操作") from None
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post("/api/cookie-sync/{request_id}")
    async def submit_cookie_sync(request_id: str, body: CookieSyncInput, owner=Depends(authorize)):
        if (body.cookie is None) == (body.error is None):
            raise HTTPException(422, "请提交读取结果或固定错误状态")
        try:
            current = cookie_sync.view(request_id, owner)
        except ValueError:
            raise HTTPException(404, "Cookie 同步请求不存在或已过期，请重新操作") from None
        if current["status"] != "waiting":
            raise HTTPException(409, "Cookie 同步请求已处理，请重新操作")
        try:
            result = cookie_sync.submit(request_id, owner, cookie=body.cookie, error=body.error)
        except ValueError:
            raise HTTPException(409, "Cookie 同步请求已处理，请重新操作") from None
        return JSONResponse(result, status_code=202, headers={"Cache-Control": "no-store"})

    @app.put("/api/settings", dependencies=[Depends(authorize)])
    async def save_settings(body: SettingsInput):
        if body.quality_policy is not None and body.quality_policy not in {"confirm", "upstream"}:
            raise ValueError("请选择每次确认或明确接受原项目优选质量")
        async with service.lock:
            if body.download_dir is not None:
                path = download_directory(body.download_dir, media_dir)
                probe = path / (".write-check-" + secrets.token_hex(8))
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    probe.write_bytes(b"")
                    probe.unlink()
                except OSError:
                    raise ValueError("下载目录不可写，请检查 NAS 挂载和目录权限") from None
                body.download_dir = path.relative_to(media_dir).as_posix()
            for key, value in body.model_dump(exclude_none=True).items():
                store.set_setting(key, value)
            if body.cookie is not None or body.download_dir is not None:
                await service.initialize()
        return settings_response()

    def public_task(task):
        if not task:
            raise HTTPException(404, "任务不存在")
        task = dict(task)
        task["preview"] = [{key: item.get(key) for key in ("id", "desc", "nickname", "type", "width", "height")} for item in task["preview"]]
        return task

    @app.get("/api/tasks", dependencies=[Depends(authorize)])
    async def tasks():
        result = [public_task(task) for task in store.tasks()]
        resume_at = store.setting('queue_resume_at', 0)
        if resume_at > time.time():
            for task in result:
                if task['status'] == 'queued':
                    task.update(queue_resume_at=resume_at, message=f"队列休息中，预计 {datetime.fromtimestamp(resume_at, BEIJING):%H:%M:%S} 按顺序继续")
        return result

    @app.post("/api/tasks", dependencies=[Depends(authorize)])
    async def add_task(body: TaskInput):
        kind, link = normalize_input(body.link, body.kind)
        return public_task(store.create_task(link, kind, body.accept_quality))

    @app.post("/api/tasks/{task_id}/retry", dependencies=[Depends(authorize)])
    async def retry(task_id: str):
        return public_task(service.retry(task_id))

    @app.post("/api/tasks/{task_id}/confirm", dependencies=[Depends(authorize)])
    async def confirm(task_id: str):
        return public_task(service.confirm(task_id))

    @app.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel(task_id: str):
        return public_task(await service.cancel(task_id))

    @app.delete('/api/tasks/{task_id}', dependencies=[Depends(authorize)])
    async def delete_task(task_id: str):
        await service.cancel(task_id)
        store.delete_task(task_id)
        return {'deleted': task_id}

    @app.post('/api/tasks/batch', dependencies=[Depends(authorize)])
    async def task_batch(body: TaskBatchInput):
        changed, skipped = [], []
        for task_id in dict.fromkeys(body.ids):
            try:
                if body.action == 'retry':
                    service.retry(task_id)
                elif body.action == 'cancel':
                    task = store.task(task_id)
                    if not task or task['status'] in {'completed', 'cancelled'}:
                        raise ValueError('任务已结束或不存在')
                    await service.cancel(task_id)
                else:
                    await service.cancel(task_id)
                    store.delete_task(task_id)
                changed.append(task_id)
            except ValueError as exc:
                skipped.append({'id': task_id, 'reason': str(exc)})
        return {'changed': changed, 'skipped': skipped}

    @app.get("/api/library", dependencies=[Depends(authorize)])
    def library(q: str = "", author: str = "", offset: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100), seed: int | None = Query(default=None,ge=0,le=4294967295), ceiling: int | None = Query(default=None,ge=0), favorite: bool = False):
        result = store.library(q[:200], author[:200], offset, limit, seed, ceiling, favorite)
        for item in result["items"]:
            item["files"] = [{"name": Path(file).name, "url": f"/api/media/{item['id']}/{i}"} for i, file in enumerate(item["files"])]
        return result

    @app.put("/api/favorites/{work_id}", dependencies=[Depends(authorize)])
    async def favorite_work(work_id: str, body: FavoriteInput):
        if not store.work(work_id):
            raise HTTPException(404,"作品不存在")
        store.set_favorite(work_id,body.favorite)
        return {"favorite":body.favorite}

    @app.get("/api/authors/history", dependencies=[Depends(authorize)])
    async def author_history():
        return store.author_history()

    @app.get("/api/legacy", dependencies=[Depends(authorize)])
    async def legacy_status():
        return legacy.status()

    @app.post("/api/legacy/scan", dependencies=[Depends(authorize)])
    async def scan_legacy():
        return legacy.start()

    @app.get("/api/authors", dependencies=[Depends(authorize)])
    async def authors():
        return store.authors()

    @app.get('/api/author-count', dependencies=[Depends(authorize)])
    async def author_count(sec_uid: str = Query(pattern=r'^[A-Za-z0-9_-]{1,128}$')):
        cookie = store.setting('cookie', '') or ''
        headers = {'Cache-Control': 'no-store'}
        if not cookie:
            return JSONResponse({'detail': '无法获取博主作品数量'}, status_code=503, headers=headers)
        key = (hashlib.sha256(cookie.encode()).hexdigest(), sec_uid)
        cached = count_cache.get(key)
        if cached and cached[0] > time.monotonic():
            count = cached[1]
        else:
            async def fetch_count():
                try:
                    try:
                        async with asyncio.timeout(45):
                            async with count_limit:
                                value = await core.author_count(sec_uid, cookie)
                    except Exception:
                        value = None
                    count_cache[key] = (time.monotonic() + (30 if value is None else 300), value)
                    while len(count_cache) > 128:
                        count_cache.pop(next(iter(count_cache)))
                    return value
                finally:
                    count_requests.pop(key, None)

            if key not in count_requests:
                count_requests[key] = asyncio.create_task(fetch_count())
            count = await asyncio.shield(count_requests[key])
        if count is None or store.setting('cookie', '') != cookie:
            return JSONResponse({'detail': '无法获取博主作品数量'}, status_code=503, headers=headers)
        return JSONResponse({'sec_uid': sec_uid, 'count': count}, headers=headers)

    @app.post("/api/authors", dependencies=[Depends(authorize)])
    async def add_author(body: AuthorInput):
        kind, url = normalize_input(body.url, "author")
        if "\n" in url:
            raise ValueError("每次添加一个博主")
        author_id = hashlib.sha256(url.encode()).hexdigest()[:16]
        if body.favorite is not None:
            existing = next((a for a in store.authors() if a['url'] == url), None)
            if existing:
                return store.patch_author(existing['id'], favorite=body.favorite)
        minutes = FREQUENCIES
        author = store.save_author(author_id, url, body.name,
                                   body.frequency != "once" if body.frequency else body.enabled,
                                   minutes[body.frequency] if body.frequency else body.interval_minutes or store.setting("interval_minutes", 129600))
        if body.frequency and author["enabled"]:
            store.mark_author_scheduled(author["id"], reset=True)
        if body.favorite is not None:
            store.patch_author(author['id'], favorite=body.favorite)
        return store.author(author["id"])

    @app.patch("/api/authors/{author_id}", dependencies=[Depends(authorize)])
    async def patch_author(author_id: str, body: AuthorPatch):
        if not store.author(author_id):
            raise HTTPException(404, "博主不存在")
        values = body.model_dump(exclude_none=True)
        frequency = values.pop("frequency", None)
        if frequency:
            values.update(enabled=frequency != "once", interval_minutes=FREQUENCIES[frequency])
        if not values:
            raise ValueError("没有需要修改的设置")
        author = store.patch_author(author_id, **values)
        if (frequency or body.enabled is True or body.interval_minutes) and author["enabled"]:
            store.mark_author_scheduled(author_id, reset=True)
        return store.author(author_id)

    @app.post("/api/authors/{author_id}/check", dependencies=[Depends(authorize)])
    async def check_author(author_id: str):
        author = store.author(author_id)
        if not author:
            raise HTTPException(404, "博主不存在")
        store.mark_author_scheduled(author_id, reset=True)
        return public_task(store.create_task(author["url"], "author", author_id=author_id))

    def media_path(work_id, index):
        item = store.work(work_id)
        if not item or index < 0 or index >= len(item["files"]):
            raise HTTPException(404, "媒体不存在")
        root = legacy.root if item['source'] == 'legacy' else media_dir
        if not root:
            raise HTTPException(404, "旧库目录未挂载")
        path = (root / item["files"][index]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "媒体文件不存在或目录不可用")
        return path

    @app.get('/api/media/{work_id}/{index}/playback', dependencies=[Depends(authorize)])
    async def prepare_playback(work_id: str, index: int, force: bool = False, retry: bool = False, warm: bool = False):
        try:
            result = await playback.prepare(media_path(work_id,index),force=force,retry=retry,warm=warm)
        except RuntimeError as exc:
            result = {'status':'failed','message':str(exc)}
        if result['status']=='ready':
            result['url']=f'/api/media/{work_id}/{index}'+('?compatible=true' if result['mode']=='compatible' else '')
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @app.get("/api/media/{work_id}/{index}", dependencies=[Depends(authorize)])
    async def media(work_id: str, index: int, compatible: bool = False):
        path=media_path(work_id,index)
        if compatible:
            ready=playback.ready(path)
            if ready is None and playback.streaming(path):
                return StreamingResponse(playback.stream(path),media_type='video/mp4',headers={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer'})
            path=ready
            if path is None:raise HTTPException(409,'播放副本尚未准备好，请重新打开作品')
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600", "Referrer-Policy": "no-referrer"})

    @app.get("/douyin-send.user.js")
    async def userscript():
        return FileResponse(BASE.parent / "userscripts" / "douyin-send.user.js", media_type="application/javascript",headers={"Cache-Control":"no-cache"})

    static = BASE / "static"
    static.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/style.css")
    async def stylesheet():
        return FileResponse(static / "style.css",headers={"Cache-Control":"no-cache"})

    @app.get("/app.js")
    async def javascript():
        return FileResponse(static / "app.js",headers={"Cache-Control":"no-cache"})

    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(static / "favicon.svg", media_type="image/svg+xml")

    @app.get("/")
    async def home():
        return FileResponse(static / "index.html", headers={"Referrer-Policy": "no-referrer","Cache-Control":"no-cache"})

    return app
