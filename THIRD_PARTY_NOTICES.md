# 第三方来源与许可

Cloudrift 由 aevyrian 维护，应用代码依根目录 [LICENSE](LICENSE) 提供。项目继续保留下载核心及其他组件的原作者署名和许可证，不将上游代码声明为独立原创。

## TikTokDownloader / DouK-Downloader

- 原项目：[JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader)。
- 固定来源提交：[`473c90ff70c663cfb69310fff2b8d5192f200661`](https://github.com/JoeanAmier/TikTokDownloader/tree/473c90ff70c663cfb69310fff2b8d5192f200661)。
- 源码位置：`vendor/TikTokDownloader/`，保留原 README、作者元数据和 [GPL-3.0 许可](vendor/TikTokDownloader/license)。
- 该副本包含 Cloudrift 所需的接口适配和请求安全修正，并非未修改的上游快照；固定基线差异见 [补丁说明](vendor/TikTokDownloader/PATCHES.md)。Cloudrift 的网页、队列、订阅、媒体库及 Sender 在此基础上实现。
- 本地原版 README 的部分图片属于未打包的上游文档资源；可查看固定提交的 [中文原文](https://github.com/JoeanAmier/TikTokDownloader/blob/473c90ff70c663cfb69310fff2b8d5192f200661/README.md) 和 [英文原文](https://github.com/JoeanAmier/TikTokDownloader/blob/473c90ff70c663cfb69310fff2b8d5192f200661/README_EN.md)。

## FFmpeg

Docker 构建使用固定摘要校验的 [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) GPL 构建，具体版本、下载地址和 SHA256 见 `tools/prepare_ffmpeg.py`。下载包中的 `LICENSE.txt` 随运行时保留；FFmpeg 源码及许可说明见 [FFmpeg](https://ffmpeg.org/legal.html)。公开源码包只包含准备脚本，不包含 FFmpeg 二进制归档。

## Intel VAAPI

构建脚本采用 [intel/libva 2.22.0](https://github.com/intel/libva/tree/2.22.0)，并使用 Debian 的 Intel media driver 与运行时包。原组件各自的许可证继续适用；`build_vaapi.sh` 保留 libva 的 `COPYING` 和安装包中的版权资料到镜像的 `/usr/share/doc/`。具体构建过程见该脚本；[Intel media-driver](https://github.com/intel/media-driver) 与 [libva COPYING](https://github.com/intel/libva/blob/2.22.0/COPYING) 提供来源和许可原件。

## Python 依赖

直接与固定依赖版本见根目录 `requirements.txt`，下载核心的依赖元数据见 `vendor/TikTokDownloader/pyproject.toml`。这些依赖各自的许可和版权说明保持有效；公开源码包不包含安装后的 Python 环境或 wheel 缓存。
