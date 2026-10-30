# 历史记录与离线评论整合实施计划

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement and review these tasks.

**Goal:** 将匿名数量查询、后台浏览历史和用户主动选择的离线评论合入 Cloudrift，基本检查通过先推送可试用版，再审计、修复和再次推送。
**Architecture:** 保留现有 FastAPI、SQLite、原生网页及内置下载核心。评论与媒体共用现有调度循环和核心锁，评论数据独立存储；网站读本地快照，明确点击才采集。
**Tech Stack:** Python、FastAPI、SQLite、JavaScript、Docker；不新增服务或依赖。
**Spec:** [评论离线收藏与导出设计](../specs/2026-10-30-offline-comments-design.md)。用户在设计后明确选择本轮实现，与历史功能一起推送；后续澄清先推送供试用，再全项目审计、修复和再次推送。

## 全局约束

- 保留数据、媒体、配置和可信旧库映射；未知真实作品编号禁止采集，不扫描或自动补采旧库。
- 打开面板仅查询本地。采集主评论、回复、图片/图片表情；分别记录操作状态和完整性。
- 媒体任务优先，队列冷却同时限制评论及其附件。单页/单附件完成后交还调度，使用同一核心锁。
- 每次手动操作最多 200 个评论请求、2000 条新增评论（整页边界）、20 分钟实际工作时间、200 MiB 附件；每附件最多 20 MiB，评论请求间隔至少 3 秒。
- 异常与空数据不混淆。验证/限流暂停评论队列，由用户处理后手动继续；不清除登录配置。
- 身份编号全程字符串；来源地址和凭据不出现在正常 API、导出及日志中。附件只经受控公共 HTTPS 来源、逐跳验证和本地鉴权访问。
- 网站版本 1.4.0，助手 1.11.0。源码推送与 NAS、公开镜像发布状态分别记录。

## 审查重点

| 情况 | 必须验证的结果 |
| --- | --- |
| 保存中取消、重启或同一作品再次提交 | 已保存数据保留，旧响应不能覆盖新操作状态 |
| 回复分页异常、平台空响应、附件无法保存 | 显示部分/失败，不宣称零评论或完整 |
| 旧作品有多个映射、索引移除 | 不猜编号，已有评论不随媒体索引删除 |
| 播放中打开面板、快速切换作品 | 不改变播放选择，不自动跳走，不串评论 |
| 恶意图片地址、路径、评论文本、导出表格公式 | 拒绝内网/越界与执行内容，凭据不外泄 |

## 任务与接口

### 1. 整合已有历史与匿名读取
- [x] 比较原工作区修改前基线与主仓库，备份并仅合并相关代码/测试，保留 Cloudrift 链接及发行文件。
- [x] 回归后台记录、幂等补充资料、数量匿名会话及时间线分页。

### 2. 评论单页核心适配
文件：`app/core.py`、`tests/test_core_comments.py`。
- [x] 先测试单页请求、主/回复关系、真实空结束、异常响应及无额外重试，再实现 `Core.comment_page(work_id, cursor=0, root_id=None)`。
- [x] 返回规范化 `items/next_cursor/has_more/total`；登录验证或限流抛 `CommentBlocked`，其他失败返回安全错误。
- [x] 每次仅一个请求，保留当前手动采集配置，禁止跳转与吞错；匿名作品数量仍完全隔离。

### 3. 存储与分段调度
文件：`app/store.py`、`app/comments.py`、`tests/test_comments_store.py`、`tests/test_comments_service.py`。
- [x] 先覆盖恢复、取消竞态、旧编号、分页事务、限额、冷却和媒体优先，再实现三类独立表和 `CommentService`。
- [x] `Store.comment_work_id/comment_state/comment_page/comment_snapshot/comment_asset` 提供本地查询与导出快照。
- [x] `CommentService.queue/cancel/recover/step` 只由现有 `JobService.run_queue` 调用；每片段至多一页或一附件。
- [x] 更新合并不删未再次见到的历史内容，人工继续保留断点并重置本轮额度。

### 4. 附件与导出
文件：`app/comment_assets.py`、`tests/test_comment_assets.py`。
- [x] 先覆盖 SSRF、DNS 重绑定、重定向、文件上限、类型伪装、路径越界与失败清理，再实现 `download_asset(source_url, data_root, remaining_bytes)` 和 `asset_path(data_root, asset)`。
- [x] `export_snapshot(snapshot, data_root, format)` 生成 JSON、UTF-8 BOM CSV、ZIP，按字段白名单保留关系与本地附件，不再次访问平台。
- [x] 图片边下载边写入临时文件，验证后原子替换；ZIP 逐文件写入，导出结束清理临时文件。

### 5. API 与播放器
文件：`app/main.py`、`app/static/`、`tests/test_comments_api.py`、`tests/test_comments_ui.mjs`。
- [x] 鉴权 GET `/api/works/{work_id}/comments` 只读；POST 同路径手动保存/更新/继续，POST `/cancel` 停止。
- [x] 鉴权 `/api/comment-assets/{asset_id}` 与作品 `/comments/export` 使用本地记录，错误不泄漏内部路径和来源。
- [x] 面板分页阅读主评论和回复，安全文本、鉴权附件、缺图提示，桌面侧栏/小屏底栏；切换作品关闭并丢弃旧结果。
- [x] 验证面板内滚动、Esc/焦点返回和自动下一条暂停，不更改用户播放/暂停选择。

### 6. 首版推送，再全项目审计与修复
文件：版本、README、CHANGELOG、架构/源码清单、Docker/CI 及必要修复。
- [x] 全部 Python 与 Node 检查、语法、源码打包及敏感内容检查；局限与真实平台证据分开记录。
- [x] 核对远端变化，正常提交推送可试用版，立即告知用户：`c2ded40`，网站 1.4.0／助手 1.11.0；NAS 和公开镜像尚未切换此版本。
- [ ] 继续独立审计既有后端、前端、Sender、部署与新功能，记录具体复现和最小修复；修复后复审并再次推送。
- [ ] 回写原任务和项目日记，准确区分首版与审计修复版。

## 分工冲突检查

核心只改 core；存储/调度只改 store/comments；附件只改 comment_assets；前端只改 static；根代理接 main/API；发行代理接文档和构建。共享返回结构由上述接口固定，各组件先单测再集成。已有历史代码先合并后分工，避免覆盖。公开设计保留原设计阶段记录，实施状态另行更新，不把设计评审当成平台实测。
