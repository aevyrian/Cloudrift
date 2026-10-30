# 下载核心来源与修改

下载核心源自 [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) 固定提交 [`473c90ff70c663cfb69310fff2b8d5192f200661`](https://github.com/JoeanAmier/TikTokDownloader/tree/473c90ff70c663cfb69310fff2b8d5192f200661)。该副本随 Cloudrift 分发，并非未经修改的上游快照；原作者署名与 [GPL-3.0 许可](license) 保留。再分发时请同时保留适用的许可、来源和修改说明；其他依赖见根目录 [第三方说明](../../THIRD_PARTY_NOTICES.md)。

## 相对固定来源的源码变更

以下比较覆盖 `src/` 文件，并忽略 CRLF 与 LF 的差异：两边各有 124 个文件，无新增或删除；以下 11 个文件有修改：

- `src/application/main_server.py`
- `src/application/main_terminal.py`
- `src/config/parameter.py`
- `src/config/settings.py`
- `src/downloader/download.py`
- `src/extract/extractor.py`
- `src/interface/account.py`
- `src/interface/template.py`
- `src/link/requester.py`
- `src/module/tiktok_unofficial.py`
- `src/tools/session.py`

修改涉及空结果与分页错误处理、重复分页防护、下载续传和长度校验、已下载文件判断、过期媒体地址、Cookie 配置权限及 TLS 校验等；精确差异以当前文件与上述固定提交的比较为准。后续更改以项目版本记录为准。

本地保留的上游 README 原文引用了未包含在本源码包中的文档图片。可在上游固定提交阅读 [中文 README](https://github.com/JoeanAmier/TikTokDownloader/blob/473c90ff70c663cfb69310fff2b8d5192f200661/README.md) 或 [英文 README](https://github.com/JoeanAmier/TikTokDownloader/blob/473c90ff70c663cfb69310fff2b8d5192f200661/README_EN.md)。
