# Cloudrift 源码与镜像打包

常规安装优先按根目录 [README](../README.md) 使用 Docker Compose。Dockerfile 当前构建 Linux amd64；没有本地缓存时需要联网获取基础镜像、Python 依赖、FFmpeg 与 Intel 驱动。这里的脚本不会代替部署、健康检查或真实下载验收。

## 生成公开源码包

在项目目录使用已安装的 Python 运行：

```bash
python tools/package_image.py --source-only
```

命令无需联网、crane 或离线缓存，在 `dist/` 生成按网站版本命名的源码 ZIP、独立 Sender、ZIP 内逐文件 SHA256 清单，以及交付产物的 `SHA256SUMS.txt`。文件清单可通过 `sha256sum -c` 在解压后的源码目录中校验；产物清单在 `dist/` 中校验。重复运行会覆盖这四个同名产物，不删除其他文件。

`source_files(root)` 是 Git 发布快照与 ZIP 共用的文件清单，定义于 `tools/package_image.py`：

- 包含应用、Sender、合成测试、部署文件、CI 配置、架构与迁移说明、必要构建脚本、第三方核心源码、修改说明和许可证。
- 保留 `tools/ffmpeg/`、`tools/vaapi/`、`tools/wheels/` 的 `.gitkeep`，以满足 Docker 构建目录要求。
- 排除 `.comet`、本地 Git 历史、AGENTS、本机工作记录、设计稿、备份、运行配置、数据、媒体及离线二进制缓存。
- `.env.example` 是唯一允许进入包的环境配置示例，所有凭据默认留空。

ZIP 可通过 `python -m unittest discover -s tests -p test_package.py -v` 检查打包选择及边界；该命令不会下载媒体或构建镜像。

## 高级镜像归档工具

`package_image.py` 可使用官方 [google/go-containerregistry crane](https://github.com/google/go-containerregistry) 拼装标准 docker-save 归档；现有流程针对 crane v0.22.1、Linux CPython 3.12 amd64。使用自己的官方工具路径，不把工具或凭据提交到仓库：

```bash
python tools/package_image.py --prepare-only
python tools/package_image.py --crane /自己的路径/crane
```

第二步还需要事先准备并校验 `tools/vaapi/runtime.tar` 与 `SHA256SUMS`。`build_vaapi.sh` 仅在一次性 Debian 构建容器中运行，不能在 Windows 或 NAS 宿主直接执行。普通用户无需使用这条高级路径；Dockerfile 会在隔离构建阶段自行准备所需内容。

Python 依赖及中间层保存在 `.comet/package/`，交付产物位于 `dist/`。镜像归档工具校验 Linux 原生库与运行时摘要，生成的 `build-info.json` 明确记录 `container_run_verified: false`。归档可读、摘要匹配均不表示它已在 NAS 中运行。

FFmpeg 与驱动的来源和许可见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。公开源码包不包含这些缓存二进制，不声明可以离线构建。打包工具不会自动上传镜像、发布 Release 或修改 NAS。
