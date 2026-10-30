# 项目结构

抖音收藏库（Cloudrift）提供下载任务、网页媒体库和浏览器助手。当前源码版本网站 1.4.0、助手 1.11.0。下载核心已随本项目源码自包含在 `vendor/TikTokDownloader/`，运行时不需要旧项目目录或另行克隆核心；首次构建仍需联网获取基础镜像、依赖、FFmpeg 和驱动。

- `app/__main__.py`、`app/main.py`：服务入口、API 和任务调度。
- `app/core.py`：调用内置下载核心，规范输入、下载目录和结果校验。
- `app/store.py`、`app/legacy.py`：配置、SQLite、浏览历史、离线评论与历史媒体的只读索引。
- `app/comments.py`、`app/comment_assets.py`：用户主动选择的评论采集、附件与导出；评论图片保存在数据目录。
- `app/playback.py`、`app/static/`：播放兼容缓存和网站界面。
- `app/cookie_sync.py`、`userscripts/`：浏览器助手及 Cookie 同步。
- `vendor/TikTokDownloader/`：固定来源的上游下载核心及本项目适配；来源与修改见 [补丁说明](../vendor/TikTokDownloader/PATCHES.md)。
- `Dockerfile`、`docker-compose*.yml`、`tools/`：构建、部署、源码打包及 FFmpeg／可选核显运行时准备。
- `tests/`：使用合成数据的自动检查。

浏览历史只记录助手启用后识别到的浏览事件，数量补充查询使用独立未登录会话。评论只有主动保存才访问抖音，已保存数据可在局域网离线阅读；真实平台接口与长期运行仍需实测。

旧个人下载管理器仅供历史参考，没有混入本项目。原核心的说明文件和许可证随源码保留，运行数据、媒体、凭据及离线缓存不属于公开源码包。
