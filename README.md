# 抖音收藏库

抖音收藏库（Cloudrift）是由 aevyrian 维护的独立项目，仓库为 [aevyrian/Cloudrift](https://github.com/aevyrian/Cloudrift)。它基于 [JoeanAmier/TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) 的下载核心，提供中文网页、下载队列、按需订阅和抖音收藏库助手，用于下载喜欢的抖音作品并在自己的媒体库浏览视频、图集和实况。

下载核心随项目保存在 `vendor/TikTokDownloader/`；上游署名与 GPL-3.0 许可保留，见 [第三方说明](THIRD_PARTY_NOTICES.md) 和 [LICENSE](LICENSE)。项目结构及现有部署迁移分别见 [架构说明](docs/architecture.md) 和 [迁移说明](docs/migration.md)。

## 能做什么

- 保存单个作品或博主现有作品，显示真实任务状态、进度、失败原因、重试与画质确认。
- 下载页固定使用 **A · 沉浸列表**，保留搜索、筛选、详情和原有操作，不再提供布局选择。
- 作者首次下载结束后随机休息5～8分钟，已有作者更新结束后随机休息2～5分钟；按顺序继续，重启保留等待时间。失败或取消也会等待，已有完整作品核对后跳过。
- 普通下载仅执行一次。主动追更可选3天、7天、月、季度、年；北京时间08–18点错峰，历史博主不会自动成为订阅。1.3.0升级时已有博主的周期统一为季度，保留原启停状态，之后可单独修改。
- 在作品库随机浏览、搜索、收藏、刷视频和翻图集。普通视频进入时自动播放，播完接着播放下一条；每次打开网站默认开启声音，可手动关闭。浏览器限制有声自动播放时需点击画面播放。收藏支持作品和博主，订阅可星标置前。
- 任务支持单个及当前筛选结果的批量重试、取消、删除；无需二次确认，删除仅移除记录，已下载媒体和断点文件保留。
- 通过脚本猫等油猴管理器安装抖音收藏库助手，从抖音网页发送当前作品或博主；下载按钮仅下载一次，爱心用于订阅。同次打开面板时切换作者复用订阅查询，重新打开会刷新其他设备的改动。
- 点击“添加下载”下的“一键同步抖音 Cookie”，由已安装的助手读取当前浏览器登录状态；服务端验证成功后保存，正在下载时等待任务结束再应用。首次需允许助手读取抖音 Cookie，失败不会覆盖原配置。
- 旧媒体目录可只读接入。不搬迁、改名或重下旧文件；发现匹配歧义、索引未完成或文件不完整时停止任务并提示处理。

## 安装

当前 Docker 运行时面向 **Linux amd64**，可部署在支持 Docker Compose 的 NAS 或 Linux 主机。首次无缓存构建需要联网下载 Python 基础镜像、依赖、FFmpeg 和驱动；公开源码包不包含离线缓存，也不等同于已构建的镜像。

```bash
git clone https://github.com/aevyrian/Cloudrift.git Cloudrift
cd Cloudrift
cp .env.example .env
docker compose up -d --build
```

打开 `http://你的主机地址:8090`：

1. 首次进入时自行设置管理员密钥；以后用它登录，可在设置中修改。
2. 在设置中填写自己的抖音 Cookie，或安装助手后点击“一键同步抖音 Cookie”；按需要选择自动下载或逐任务确认画质。
3. 粘贴作品分享内容或博主页链接；到“下载”查看任务，保存后到“作品库”观看。
4. 如需网页发送，在网站设置页安装 抖音收藏库助手，填入自己的网站地址和管理员密钥。

密钥和 Cookie 仅在自己的部署中配置，不要写入 Git、脚本源码或公开问题反馈。网站用于可信局域网；不要把带有个人媒体与登录信息的实例直接暴露到公网。

### 使用发行镜像

公开镜像位于 [Docker Hub](https://hub.docker.com/r/aevyrian/cloudrift)，支持 Linux amd64，提供 `1.3.6` 和 `latest` 标签，可免登录拉取。新安装先按上文下载项目并准备 `.env`，再在项目目录运行：

```bash
docker pull aevyrian/cloudrift:1.3.6
docker tag aevyrian/cloudrift:1.3.6 cloudrift:1.3.6
docker compose up -d --no-build
```

第二步将仓库镜像对应到现有 Compose 使用的本地标签。镜像仓库中的 1.3.6 与 v1.3.6 Release 的已验证镜像相同；现有部署先按 [迁移说明](docs/migration.md) 核对挂载。

也提供相同内容的 [GHCR 镜像](https://github.com/users/aevyrian/packages/container/package/cloudrift)：拉取 `ghcr.io/aevyrian/cloudrift:1.3.6` 后，同样将其标记为 `cloudrift:1.3.6` 即可沿用 Compose。

也可从 [Releases](https://github.com/aevyrian/Cloudrift/releases) 下载同一版本的源码 ZIP、Linux amd64 镜像归档及 `SHA256SUMS.txt`。在 Linux 主机校验已下载文件后解压源码，将镜像归档导入 Docker；镜像标签与包内 Compose 保持一致：

```bash
sha256sum --ignore-missing -c SHA256SUMS.txt
docker load -i cloudrift-v1.3.6-linux-amd64.tar.gz
# 在解压后的源码目录中：
cp .env.example .env
docker compose up -d --no-build
```

镜像附带运行依赖；使用该路径不需要现场构建。下载作品仍需网络及有效 Cookie。已有部署先阅读 [迁移说明](docs/migration.md)，不要覆盖原 `.env` 或改变挂载。

## 数据和升级

| 主机目录 | 容器位置 | 内容 |
| --- | --- | --- |
| `APP_DATA_DIR`，默认 `./data` | `/data` | 配置、SQLite、任务记录、索引和播放缓存 |
| `APP_MEDIA_DIR`，默认 `./media` | `/media` | 新下载的媒体 |
| 可选 `APP_LEGACY_DIR` | `/legacy`，只读 | 需要接入的旧媒体目录 |

`.env.example` 提供通用路径。`APP_TOKEN` 留空即可首次在网页自设；只可用于全新数据目录的初始预设，网站修改后以保存的新密钥为准。不要把不同部署指向同一份 SQLite 数据目录。

升级前备份自己的 `.env`、Compose 与 `data/`，媒体另按原有备份方式保存。保留数据目录与媒体路径，再运行：

```bash
git pull --ff-only
docker compose up -d --build
```

停止使用 `docker compose down`，不要加 `-v`，不要删除或清理数据目录。更换磁盘只修改挂载路径不会迁移文件；原文件需由你按已有备份和迁移方案处理。旧部署更换管理员密钥后，也要更新抖音收藏库助手的连接密钥。

## 接入旧媒体

NAS/Linux 的 `.env` 中填写自己的旧目录，并启用覆盖配置：

```dotenv
APP_LEGACY_DIR=/你的旧媒体目录
COMPOSE_FILE=docker-compose.yml:docker-compose.legacy.yml
```

重建后，旧目录通过 `/legacy:ro` 读取，索引保存到 `data/`。若希望新下载仍写到同一媒体根，将 `APP_MEDIA_DIR` 设置为该目录，网站下载目录保留 `/media`。它不会增加额外 Cloudrift 目录，也不会自动搬动原文件。

飞牛等只接受单个 Compose 文件的界面，需要自行合并所用覆盖配置到主文件，保留 `/data`、可写 `/media` 和只读 `/legacy` 的对应关系。更新仅替换本项目容器，不使用会清除项目数据的“清理”。

## 播放与可选核显

浏览器不兼容的 HEVC 等视频会在观看时生成 H.264/AAC 副本，最长边为 1920；原文件和原分辨率保留。缓存位于 `data/playback-cache/`，默认上限 20 GB；首次准备仍需等待，不保证所有编码都能直接播放。

Intel 核显可选用 `docker-compose.vaapi.yml`，接入自己的 `/dev/dri/renderD128`；与旧库配置一起使用时合并相应覆盖文件。无需开启高权限。硬件转换不可用时会回退软件转换；已开始输出的流失败后可能需要点“重试播放”。

## 开发与源码包

开发使用 Python 3.12 和 Node.js 22 或更新版本。先创建虚拟环境，再安装包括测试客户端的开发依赖：

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node tests/test_userscript.mjs
node tests/test_task_view.mjs
python tools/package_image.py --source-only
```

测试使用临时目录与合成数据。任务页已做桌面、手机、筛选、详情及不写下载队列的检查；这些结果不代表真实新下载、所有旧媒体编码或所有 NAS 硬件均已验证。下载能力仍受 Cookie、作品权限和平台接口变化影响，不保证最高画质或无水印。

源码打包与镜像工具见 [tools/README.md](tools/README.md)。公开源码包按清单选择代码、合成测试、部署文件和第三方许可，排除运行数据、凭据、工作记录、备份与离线缓存。当前版本记录见 [CHANGELOG.md](CHANGELOG.md)。
