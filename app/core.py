import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


CORE_COMMIT = "473c90ff70c663cfb69310fff2b8d5192f200661"
HOSTS = {"www.douyin.com", "douyin.com", "v.douyin.com", "www.iesdouyin.com", "iesdouyin.com"}


def download_directory(value, media_root):
    root = Path(media_root).resolve()
    path = Path(value.strip()) if value and value.strip() else root
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("下载目录必须位于媒体挂载目录内；更换 NAS 磁盘请修改 Compose 的媒体挂载")
    return path


def normalize_input(value, kind="auto"):
    value = value.strip()
    if kind not in {"auto", "detail", "author"}:
        raise ValueError("请选择作品或博主")
    if re.fullmatch(r"\d{19}", value) and kind != "author":
        return "detail", value
    urls = re.findall(r"https?://[^\s<>\"，。；]+", value)
    if not urls:
        raise ValueError("请粘贴抖音作品链接、博主主页或完整分享文案")
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("只接受抖音 HTTPS 链接，不能使用其他网站或内网地址")
    detected = "author" if any("/user/" in urlsplit(url).path for url in urls) else "detail"
    kind = detected if kind == "auto" else kind
    if kind == "author" and len(urls) != 1:
        raise ValueError("每次提交一个博主主页，避免误下载多个博主")
    return kind, "\n".join(dict.fromkeys(urls))


def verified_files(media_root, item):
    root = Path(media_root).resolve()
    work_id = str(item.get("id", ""))
    if not re.fullmatch(r"\d{19}", work_id):
        raise RuntimeError("下载核心返回了无效的作品标识")
    directory = root / "作品" / work_id
    expected = len(item.get("downloads", [])) if item.get("type") in {"图集", "实况"} else 1
    images = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic"}
    videos = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
    allowed = images | videos if item.get("type") == "实况" else images if item.get("type") == "图集" else videos
    files = sorted((p for p in directory.glob("*") if p.is_file() and p.suffix.lower() in allowed), key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p.name)])
    if expected < 1 or len(files) != expected:
        raise RuntimeError(f"作品文件尚未完整保存：预期 {expected} 个，实际 {len(files)} 个，可重试")
    for path in files:
        if not path.resolve().is_relative_to(root) or path.stat().st_size <= 0:
            raise RuntimeError("媒体文件不完整或路径越出新媒体目录")
    return [p.relative_to(root).as_posix() for p in files]


class Core:
    def __init__(self, source, data_dir, media_dir):
        self.source = Path(source).resolve()
        self.data_dir = Path(data_dir).resolve() / "core"
        self.media_root = Path(media_dir).resolve()
        self.media_dir = self.media_root
        self.app = None
        self.parser = None
        self.ready = False
        self.error = ""

    async def start(self, cookie="", download_dir=""):
        await self.close()
        self.media_dir = download_directory(download_dir, self.media_root)
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(self.source)) if str(self.source) not in sys.path else None
        import src.custom as custom
        import src.custom.internal as internal
        custom.VOLUME = internal.VOLUME = self.data_dir
        from src.application import TikTokDownloader
        from src.application.main_server import APIServer
        self.app = await TikTokDownloader().__aenter__()
        self.app.check_config()
        settings = self.app.settings.read()
        settings.update(cookie=cookie, root=str(self.media_dir), folder_name="作品", name_format="id",
                        folder_mode=True, download=True, music=False, dynamic_cover=False, static_cover=False,
                        storage_format="", original_quality=True, tiktok_platform=False)
        self.app.settings.update(settings)
        await self.app.check_settings(False)
        self.parser = APIServer(self.app.parameter, self.app.database, server_mode=True)
        self.ready = True
        self.error = ""

    async def close(self):
        self.ready = False
        if self.app:
            app, self.app = self.app, None
            app.event_cookie.set()
            await app.__aexit__(None, None, None)
            if app.params_task:
                await asyncio.to_thread(app.params_task.join, 5)
        self.parser = None

    async def author_count(self, sec_uid, cookie):
        try:
            if str(self.source) not in sys.path:
                sys.path.insert(0, str(self.source))
            from curl_cffi.requests import AsyncSession
            from src.custom import DATA_HEADERS, IMPERSONATE, USERAGENT
            from src.encrypt import DouYinParams
            from src.interface.user import User

            class Silent:
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None

            async with AsyncSession(timeout=8, impersonate=IMPERSONATE, verify=True, allow_redirects=False) as client:
                params = SimpleNamespace(headers=DATA_HEADERS, logger=Silent(), console=Silent(), douyin_params=DouYinParams(),
                                         max_retry=0, timeout=8, client=client, impersonate=IMPERSONATE, user_agent=USERAGENT)
                user = await User(params, cookie=cookie, sec_user_id=sec_uid).run()
            count = user.get('aweme_count') if isinstance(user, dict) else None
            if not isinstance(user, dict) or user.get('sec_uid') != sec_uid or type(count) is not int or not 0 <= count <= 2**53 - 1:
                raise ValueError
            return count
        except Exception:
            raise RuntimeError('无法获取博主作品数量') from None

    async def parse(self, link, kind, earliest=None):
        if not self.ready:
            raise RuntimeError("下载核心尚未就绪，请检查设置和服务日志")
        from src.models import Account, Detail
        result = []
        if kind == "author":
            identifiers = await self.parser.links.run(link, "user")
            if not identifiers:
                raise ValueError("未识别博主主页，请选择发送博主或粘贴完整主页链接")
            for identifier in identifiers:
                response = await self.parser.handle_account(Account(sec_user_id=identifier, tab="post", earliest=earliest, source=False))
                if response.data is None:
                    raise RuntimeError("未取得博主作品，请检查 Cookie、主页权限和网络；不会把失败当作没有更新")
                result.extend(response.data if isinstance(response.data, list) else [response.data])
        else:
            identifiers = [link] if re.fullmatch(r"\d{19}", link) else await self.parser.links.run(link, "detail")
            if not identifiers:
                raise ValueError("未识别作品，请粘贴单个作品链接，博主主页请选择发送博主")
            for identifier in identifiers:
                response = await self.parser.handle_detail(Detail(detail_id=identifier, source=False))
                if response.data is None:
                    raise RuntimeError("未取得作品数据，请检查 Cookie、作品是否可见和网络")
                result.append(response.data)
        unique = {}
        for item in result:
            if item.get("type") not in {"视频", "图集", "实况"}:
                raise RuntimeError("该作品类型暂不支持，任务已保留，可重试")
            if not re.fullmatch(r"\d{19}", str(item.get("id", ""))) or not item.get("downloads"):
                raise RuntimeError("解析结果缺少作品标识或可下载地址")
            unique[str(item["id"])] = item
        return list(unique.values())

    async def download(self, item):
        if not self.ready:
            raise RuntimeError("下载核心尚未就绪")
        try:
            files = verified_files(self.media_dir, item)
        except RuntimeError:
            # 图集中断后作品级记录可能存在，清掉它才能让原下载器补齐缺失图片。
            await self.parser.downloader.recorder.delete_id(str(item["id"]))
            self.parser.downloader.last_error = ''
            success = await self.parser.downloader.run([item.copy()], type_="detail", tiktok=False)
            if success is not True:
                reason = self.parser.downloader.last_error or '下载器未提供具体原因'
                raise RuntimeError(f"下载未完成：{reason}。已保存作品保留，重试会重新解析地址并跳过已有作品")
            files = verified_files(self.media_dir, item)
        return [(self.media_dir / file).relative_to(self.media_root).as_posix() for file in files]
