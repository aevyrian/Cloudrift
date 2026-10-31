import asyncio
import re
import sys
from pathlib import Path
from http.cookies import SimpleCookie
from types import SimpleNamespace
from urllib.parse import urlsplit


CORE_COMMIT = "473c90ff70c663cfb69310fff2b8d5192f200661"
HOSTS = {"www.douyin.com", "douyin.com", "v.douyin.com", "www.iesdouyin.com", "iesdouyin.com"}


class CommentBlocked(RuntimeError):
    pass


class ParsedItems(list):
    """作品解析结果，skipped 记录因类型暂不支持而跳过的数量。"""

    def __init__(self, items=(), skipped=0):
        super().__init__(items)
        self.skipped = skipped


def _comment_id(value, empty=False):
    if empty and (value is None or value == '' or value == '0' or type(value) is int and value == 0):
        return ''
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,32}', value) or int(value) == 0:
        raise ValueError('评论编号无效')
    return value


def _comment_number(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError('评论数值无效')
    return value


def _comment_item(item, root_id):
    if not isinstance(item, dict):
        raise ValueError('评论结构无效')
    user = item.get('user')
    target = item.get('reply_to_user')
    user = {} if user is None else user
    target = {} if target is None else target
    if not isinstance(user, dict) or not isinstance(target, (dict, str)):
        raise ValueError('评论用户信息无效')
    nickname = user.get('nickname', '')
    reply_to_user = target if isinstance(target, str) else target.get('nickname', '')
    nickname = '' if nickname is None else nickname
    reply_to_user = '' if reply_to_user is None else reply_to_user
    text = item.get('text', '')
    if not all(isinstance(value, str) for value in (nickname, reply_to_user, text)):
        raise ValueError('评论文字无效')
    if isinstance(target, dict) and target.get('uid'):
        uid = _comment_id(target['uid'])
        reply_to_user = f'{reply_to_user}（{uid}）' if reply_to_user else uid
    attachments = []

    def add_asset(kind, data):
        urls = data.get('url_list') if isinstance(data, dict) else None
        url = next((value for value in urls if isinstance(value, str) and 0 < len(value) <= 8192
                    and value.startswith('https://')), '') if isinstance(urls, list) else ''
        attachments.append({'kind': kind if url else 'unsupported', 'source_url': url,
                            'status': 'pending' if url else 'unsupported', 'error': '' if url else '附件地址不可用'})

    images = item.get('image_list')
    if images is not None:
        if not isinstance(images, list):
            raise ValueError('评论图片结构无效')
        for image in images:
            add_asset('image', image.get('origin_url') if isinstance(image, dict) else None)
    sticker = item.get('sticker')
    if sticker is not None:
        add_asset('sticker', sticker.get('static_url') if isinstance(sticker, dict) else None)
    if item.get('voice') or item.get('audio_comment') or item.get('voice_url'):
        attachments.append({'kind': 'unsupported', 'source_url': '', 'status': 'unsupported', 'error': '音频评论暂不支持'})
    return {'id': _comment_id(item.get('cid')), 'root_id': root_id or '',
            'reply_to_id': _comment_id(item.get('reply_to_reply_id'), empty=True), 'reply_to_user': reply_to_user,
            'nickname': nickname, 'text': text, 'created_at': _comment_number(item.get('create_time')),
            'likes': _comment_number(item.get('digg_count')),
            'reply_count': _comment_number(item.get('reply_comment_total', 0 if root_id else None)),
            'attachments': attachments}


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

    async def cookie_account(self, cookie):
        try:
            if str(self.source) not in sys.path:
                sys.path.insert(0, str(self.source))
            from curl_cffi.requests import AsyncSession
            from src.custom import DATA_HEADERS, IMPERSONATE, USERAGENT
            from src.encrypt import DouYinParams
            from src.interface.template import API
            from src.tools import cookie_str_to_dict

            class Silent:
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None

            current = self.app.parameter if self.ready and self.app and self.app.parameter else None
            allowed = {'accept', 'accept-encoding', 'content-type', 'referer', 'user-agent', 'x-tt-argus'}
            headers = {key: value for key, value in (current.headers if current else DATA_HEADERS).items() if key.lower() in allowed}
            params = SimpleNamespace(headers=headers, logger=Silent(), console=Silent(),
                                     douyin_params=current.douyin_params if current else DouYinParams(),
                                     max_retry=0, timeout=5, client=None,
                                     impersonate=current.impersonate if current else IMPERSONATE,
                                     user_agent=current.user_agent if current else USERAGENT)
            query = API(params, cookie=cookie)
            query.api = 'https://www.douyin.com/aweme/v1/web/user/profile/self/'
            query.set_referer('https://www.douyin.com/user/self')
            credentials = {key.lower(): value for key, value in cookie_str_to_dict(cookie).items()}
            values = {key: value for key, value in query.generate_params().items() if key.lower() not in {'mstoken', 'uifid', 'webid'}}
            values['uifid'] = credentials.get('uifid', '')
            values['msToken'] = credentials.get('mstoken', '')
            signed = query.deal_url_params(query.api, values)
            async with asyncio.timeout(6):
                async with AsyncSession(impersonate=params.impersonate, timeout=5, verify=True, allow_redirects=False, trust_env=False) as client:
                    response = await client.get(f'{query.api}?{signed}', headers=query.headers, timeout=5, allow_redirects=False)
            if response.status_code != 200 or not response.content:
                return None
            data = response.json()
            if not isinstance(data, dict) or type(data.get('status_code')) is not int or data['status_code'] != 0:
                return None
            user = data.get('user')
            if not isinstance(user, dict):
                return None
            def clean(value):
                return ''.join(c for c in value if ord(c) >= 32 and ord(c) != 127).strip()[:100] if isinstance(value, str) else ''
            nickname, unique_id = clean(user.get('nickname')), clean(user.get('unique_id'))
            return {'nickname': nickname, 'unique_id': unique_id} if nickname or unique_id else None
        except Exception:
            return None

    async def author_count(self, sec_uid):
        try:
            if str(self.source) not in sys.path:
                sys.path.insert(0, str(self.source))
            from curl_cffi.requests import AsyncSession
            from src.custom import DATA_HEADERS, PARAMS_HEADERS, IMPERSONATE, USERAGENT
            from src.encrypt import DouYinParams, TtWid
            from src.interface.user import User

            class Silent:
                def __getattr__(self, name):
                    return lambda *args, **kwargs: None

            allowed = {'accept', 'accept-encoding', 'content-type', 'referer'}
            headers = {key: value for key, value in DATA_HEADERS.items() if key.lower() in allowed}
            registration_headers = {key: value for key, value in PARAMS_HEADERS.items() if key.lower() in allowed}
            async with AsyncSession(timeout=5, impersonate=IMPERSONATE, verify=True, allow_redirects=False) as client:
                registration = await client.post(TtWid.API, data=TtWid.DATA, headers=registration_headers)
                if registration.status_code != 200 or not registration.content or any(text in registration.text for text in ('请完成验证', '访问过于频繁')):
                    raise ValueError
                cookies = SimpleCookie()
                cookies.load(registration.headers.get('Set-Cookie', ''))
                visitor = cookies.get('ttwid')
                if not visitor or not visitor.value or len(visitor.value) > 2048:
                    raise ValueError
                client.cookies.set('ttwid', visitor.value, domain='.douyin.com')
                home = await client.get('https://www.douyin.com/', headers=headers)
                if home.status_code != 200 or not home.content or any(text in home.text for text in ('请完成验证', '访问过于频繁')):
                    raise ValueError
                params = SimpleNamespace(headers=headers, logger=Silent(), console=Silent(), douyin_params=DouYinParams(),
                                         max_retry=0, timeout=5, client=client, impersonate=IMPERSONATE, user_agent=USERAGENT)
                query = User(params, sec_user_id=sec_uid)
                query.params = {key: value for key, value in query.params.items() if key.lower() not in {'mstoken', 'uifid', 'webid'}} | {'msToken': '', 'uifid': ''}
                query.set_referer(None)
                values = query.generate_params()
                tokens = [cookie.value for cookie in client.cookies.jar if cookie.name == 'msToken']
                values['msToken'] = tokens[-1] if tokens else ''
                values['msToken'] = values.pop('msToken')
                # 数量已有缓存和并发限流，不走下载分页的随机等待。
                signed = query.deal_url_params(query.api, values)
                response = await client.get(f'{query.api}?{signed}', headers=query.headers)
                if response.status_code != 200 or not response.content or any(text in response.text for text in ('请完成验证', '访问过于频繁')):
                    raise ValueError
                payload = response.json()
                user = payload.get('user') if isinstance(payload, dict) else None
            count = user.get('aweme_count') if isinstance(user, dict) else None
            if not isinstance(user, dict) or user.get('sec_uid') != sec_uid or type(count) is not int or not 0 <= count <= 2**53 - 1:
                raise ValueError
            return count
        except Exception:
            raise RuntimeError('无法获取博主作品数量') from None

    async def comment_page(self, work_id, cursor=0, root_id=None):
        if not isinstance(work_id, str) or not re.fullmatch(r'\d{19}', work_id):
            raise ValueError('无法识别原作品编号')
        cursor = _comment_number(cursor)
        root_id = _comment_id(root_id, empty=True)
        if not self.ready or not self.app or not self.app.parameter:
            raise RuntimeError('下载核心尚未就绪')
        if not any(key.lower() == 'cookie' and value for key, value in self.app.parameter.headers.items()):
            raise CommentBlocked('请先配置抖音 Cookie，再保存评论')
        try:
            from src.interface.comment import Comment, Reply
            params = self.app.parameter
            if root_id:
                query = Reply(params, detail_id=work_id, comment_id=root_id, pages=1, cursor=cursor, count=20)
            else:
                query = Comment(params, detail_id=work_id, pages=1, cursor=cursor, count=20)
            query.set_referer(None)
            values = query.generate_params()
            if 'msToken' in values:
                values['msToken'] = values.pop('msToken')
            signed = query.deal_url_params(query.api, values)
            async with asyncio.timeout(12):
                response = await params.client.get(f'{query.api}?{signed}', headers=query.headers, timeout=12, allow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308, 401, 403, 429):
                raise CommentBlocked('评论访问需要登录、验证或暂时受限，请处理后重试')
            if response.status_code != 200 or not response.content:
                raise ValueError('评论接口响应无效')
            try:
                payload = response.json()
            except Exception:
                if any(word in response.text.lower() for word in ('验证码', '验证', '登录', '频繁', 'captcha', 'verify', 'login', 'rate limit')):
                    raise CommentBlocked('评论访问需要登录、验证或暂时受限，请处理后重试') from None
                raise ValueError('评论接口响应无效') from None
            if not isinstance(payload, dict):
                raise ValueError('评论结构无效')
            status = payload.get('status_code')
            if type(status) is not int or status != 0:
                message = payload.get('status_msg', '')
                if isinstance(message, str) and any(word in message.lower() for word in ('验证', '登录', '频繁', 'captcha', 'verify', 'login', 'rate limit')):
                    raise CommentBlocked('评论访问需要登录、验证或暂时受限，请处理后重试')
                raise ValueError('评论接口返回失败')
            comments, more = payload.get('comments'), payload.get('has_more')
            next_cursor = _comment_number(payload.get('cursor'))
            if not isinstance(comments, list) or type(more) not in (int, bool) or more not in (0, 1):
                raise ValueError('评论分页结构无效')
            if more and (not comments or next_cursor <= cursor):
                raise ValueError('评论分页未正常推进')
            total = payload.get('total')
            if total is not None:
                total = _comment_number(total)
            return {'items': [_comment_item(item, root_id) for item in comments],
                    'next_cursor': next_cursor, 'has_more': bool(more), 'total': total}
        except CommentBlocked:
            raise
        except Exception:
            raise RuntimeError('读取评论失败，请检查网络或稍后重试') from None

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
        skipped = 0
        for item in result:
            if item.get("type") not in {"视频", "图集", "实况"}:
                skipped += 1
                continue
            if not re.fullmatch(r"\d{19}", str(item.get("id", ""))) or not item.get("downloads"):
                raise RuntimeError("解析结果缺少作品标识或可下载地址")
            unique[str(item["id"])] = item
        return ParsedItems(unique.values(), skipped)

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
