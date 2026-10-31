# Cloudrift 1.4.3 P1 计划（历史采集规则 + 第二评论通道）

> 目标：仅规划，不改代码。以下把**已验证事实**与**推测/待确认**分开写。
> 证据基于本次阅读：`AGENTS.md`、`README.md`、`CHANGELOG.md`、`docs/superpowers/plans/2026-10-30-history-comments-integration.md`、`docs/superpowers/specs/2026-10-30-offline-comments-design.md`、`app/main.py`、`app/store.py`、`app/core.py`、`app/comments.py`、`app/comment_assets.py`、`userscripts/douyin-send.user.js`、指定测试文件、`.ref-douyin-comments/`参考实现。

---

## P1-A：首次历史采集数量规则

### A.1 现状核查

| 目标规则 | 现状 | 证据 | 说明 |
|---|---|---|---|
| 首次采集：视频+图片+发言总数 ≤ 300 全量 | **未实现** | `app/main.py:247-279` | 现逻辑对作者任务直接下载 `parse` 返回的全部 `items`，无 300 分层。 |
| >300 先只抓视频和图片 | **未实现** | `app/main.py:251-268` | 无类型分层批次逻辑。 |
| >1000（视频+图片）进入等待用户确认 | **部分实现（仅机制存在）** | `app/main.py:260-262`, `app/main.py:696-699` | 已有 `awaiting_confirmation`/`confirm` 流程，但目前仅用于画质策略 `quality_policy=confirm`。 |
| 单账号本次 >600 且触发限流则暂停该账号并保存进度 | **未实现** | `app/main.py:295-299`, `app/store.py:703-720` | 现仅失败/中断，不会按“>600+限流”定向暂停作者并记录“本次进度点”。 |
| 历史作者排队不并发 | **已实现** | `app/store.py:224-226`, `app/main.py:302-310`, `app/main.py:323-337` | `author_queue_busy()` + 单 worker 处理，作者追更不会并发登记。 |
| 增量更新优先 | **部分实现** | `app/main.py:240`, `app/core.py:331-341` | 有“是否更新任务”判定 `is_update`，但 `parse(..., earliest=...)` 参数未被利用，尚无真正增量优先抓取。 |
| 保留去重、重试、恢复 | **已实现（需保持）** | 去重/跳过：`app/main.py:160-213`; 重试：`app/store.py:282-314`; 中断恢复：`app/store.py:315-318`; 测试：`tests/test_queue_pacing.py:102-121` | 现机制稳定，P1 需复用不回归。 |
| 保留首次5-8分钟/更新2-5分钟间隔 | **已实现（需保持）** | `app/main.py:214-216`; 测试 `tests/test_queue_pacing.py:25-55` | `300+rand(0..180)` 和 `120+rand(0..180)` 已覆盖边界。 |

#### “发言”类型核查

- **已验证事实**：代码中可下载作品类型仅校验 `视频/图集/实况`，没有独立“发言”类型分支（`app/core.py:356-357`）；README 也只提这三类（`README.md:3`）。
- **推测/待确认**：你说的“发言”可能是抖音图文/文字动态在当前内核中的映射（可能落在`图集`或被过滤）。需你确认产品口径，否则阈值分母无法准确落地。

### A.2 设计（方案）

#### 已验证事实（可直接据此设计）

1. 现有任务状态机支持 `awaiting_confirmation`，前后端已有确认入口（`app/main.py:260-262`, `app/main.py:696-699`）。
2. `Store.update_task(..., preview=...)` 已可持久化任务预览，可承载“已解析但待确认”的展示信息（`app/store.py:206-218`）。
3. 作者排队与冷却时间已稳定，修改点应集中在 `process_task` 分流，而非改调度主干（`app/main.py:235-300`）。

#### 推测/待确认（落地前需拍板）

1. “发言”是否参与 300 阈值；若参与，其类型映射规则是什么。  
2. “>1000 等待确认”是否仅首次历史下载触发，还是对每次作者任务都触发。  
3. “单账号暂停”是 `author.enabled=false`（硬暂停）还是只暂停当前任务并写 `last_error`（软暂停）。

#### 建议实现路径

1. **首次/增量判定明确化**  
   - 在 `process_task` 里把“首次历史采集”与“增量更新”显式拆分：首次按阈值策略，增量优先走 `earliest`。  
   - 将 `Core.parse(... earliest=...)` 真正接入（`app/core.py:331-341`），优先减少增量任务扫描面。

2. **数量阈值分流（300/600/1000）**  
   - 解析后先做类型分桶：`视频/图集/实况`，并根据“发言定义”决定计数集合。  
   - `<=300`：按全量规则继续。  
   - `>300`：先执行“视频+图片”子集（具体映射由你确认）。  
   - `>1000`：置 `awaiting_confirmation`，复用现有确认 API；确认后继续同一任务。

3. **>600 + 限流的作者级暂停**  
   - 识别限流/验证类失败（建议在 `core` 层增加可区分异常类型，避免靠字符串）。  
   - 当“本次处理量>600 且命中限流”时：暂停该作者后续调度并保留 checkpoint（任务层+作者层都写明原因）。

4. **保持现有能力不回归**  
   - 不改 `finish` 冷却公式（`app/main.py:214-216`）。  
   - 不动去重、重试、恢复主路径（`app/store.py:282-318`）。

### A.3 计划改动文件与函数

1. `app/main.py`
   - `JobService.process_task`（`app/main.py:235`）：新增首次/增量分流、300/600/1000 规则、限流分支处理。  
   - `JobService.confirm`（`app/main.py:145`）：允许复用到“数量确认”场景（避免只绑定画质文案）。
2. `app/store.py`
   - 任务字段扩展（必要时）：为“等待确认原因/阶段”或“历史采集 checkpoint”预留。  
   - 作者状态更新函数：支持“被限流暂停”元数据（可在 `patch_author`/新增 helper）。
3. `app/core.py`
   - `Core.parse` 调用链（`app/core.py:331`）：接入 `earliest` 实际参数。  
   - 新增可区分的限流异常（供 `process_task` 精确处理）。

### A.4 新测试文件与用例（建议）

1. `tests/test_history_scope_rules.py`（新）
   - `<=300` 全量。
   - `>300` 仅视频+图片首轮。
   - `>1000` 进入 `awaiting_confirmation`，确认后继续。
   - “发言”映射分支（按最终口径断言）。
2. `tests/test_queue_pacing.py`（补充）
   - 新规则下仍保持 5-8 / 2-5 分钟冷却。
3. `tests/test_priority_pause.py`（补充）
   - `>600 + 限流` 触发作者暂停且保留进度。
4. `tests/test_download_flow.py`（补充）
   - 新规则不影响默认单作品下载路径。

### A.5 验收标准

1. 首次历史采集满足 300/1000 规则，且 UI/API 可见明确状态。  
2. `>600 + 限流` 不会继续消耗该作者请求，能恢复后续继续。  
3. 增量任务明显优先（可通过 `earliest` 参数调用与用例证明）。  
4. 现有去重、重试、恢复、间隔机制全部通过原有回归用例。  
5. 不改变首页布局与入口位置（遵守 `AGENTS.md` 约束）。

### A.6 建议小提交（编号）

1. **A1**：抽出首次/增量判定与类型计数函数（仅重构+测试桩）。  
2. **A2**：落 300/1000 规则并复用 `awaiting_confirmation`。  
3. **A3**：落 `earliest` 增量优先链路。  
4. **A4**：落 `>600 + 限流` 作者暂停与恢复。  
5. **A5**：补齐回归测试与文案。

### A.7 需要你决定的问题

1. “发言”在本项目中精确定义是什么（是否等于实况/图文/其他）？  
2. `>1000` 确认后是“继续当前任务”还是“拆成批次任务”更符合你的操作习惯？  
3. 限流暂停是自动恢复（下一调度周期）还是必须手动恢复？

---

## P1-B：第二评论通道（油猴已登录页面采集）

### B.1 现状核查

| 目标要求 | 现状 | 证据 | 说明 |
|---|---|---|---|
| 保留现有服务端评论通道（`core.comment_page`） | **已实现** | `app/core.py:274-329`, `app/comments.py:67` | 当前评论采集主通道。 |
| 新增“油猴助手已登录页面采集”通道 | **未实现** | `userscripts/douyin-send.user.js` 全文未见评论采集上报 API | 现助手仅下载/订阅/Cookie同步/浏览历史。 |
| 一级+楼中楼统一映射到 `comments` 字段（复用 `_comment_item` 规则） | **部分实现** | `_comment_item` 已存在 `app/core.py:34-78`; 入库 `app/store.py:529-564` | 仅服务端通道在用；第二通道尚未接入。 |
| 评论ID去重 | **已实现（共享存储）** | `comments` 主键 `(work_id,id)`：`app/store.py:106-112`; `INSERT OR IGNORE`：`app/store.py:539-545` | 两通道接入后可天然复用。 |
| 分页checkpoint保存、失败重试 | **已实现（现通道）** | `comment_jobs.checkpoint`：`app/store.py:101-103`; `comment_commit_page`：`app/store.py:529`; `continue`：`app/store.py:463-483` | 第二通道需共享同结构或扩展结构。 |
| 登录过期/验证码时暂停并提示 | **已实现（现通道）** | `CommentBlocked`：`app/core.py:14`, `app/core.py:297-305`; `comment_block` 暂停：`app/store.py:519-528`; 服务层处理：`app/comments.py:88-90` | 第二通道要复用同暂停语义。 |
| 助手与 Docker 后台通过现有带鉴权 HTTP API 交互，不另起 `127.0.0.1` 接收器 | **未实现（新需求）** | 现参考实现确实用本地接收器：`.ref-douyin-comments/scripts/recv.py:1-50` | 本项目应避免新增本机无鉴权端口。 |
| 两通道共享存储、通道选择/自动回退 | **部分实现** | 共享存储已具备；通道选择/回退无实现 | 需新增 channel 策略层。 |
| 评论任务与下载任务分离（说明 `comment_media_busy`） | **已实现（但耦合有意）** | 分离：`comment_jobs` 与 `tasks` 两表；耦合：`comment_media_busy`/`comment_request_begin` 会让评论让路下载 `app/store.py:502-517`, `app/comments.py:26-28` | 当前是“分离建模 + 媒体优先调度”。需在方案里保留。 |

### B.2 设计（方案）

#### 已验证事实（可直接据此设计）

1. 评论存储与状态机已完整，可承接第二通道（`app/store.py:100-119`, `app/store.py:463-603`）。
2. 助手已有带 Bearer 鉴权的 API 通信框架（`userscripts/douyin-send.user.js:197-202`）。
3. 当前系统已对评论队列做“媒体优先”隔离（`app/comments.py:26-28` + `app/store.py:502-517`）。

#### 推测/待确认（落地前需拍板）

1. 第二通道是“默认优先”还是“失败回退”到服务端通道。  
2. 回退触发条件：模板缺失、验证码、接口空页、超时分别怎么判。  
3. 助手端是否允许“后台持续采集”还是只在面板开启时执行。

#### 为什么不能走 `127.0.0.1` 接收器（本项目约束）

1. 参考实现 `recv.py` 是浏览器所在机器本地端口，无鉴权（`.ref-douyin-comments/scripts/recv.py:21-43`）。  
2. Cloudrift 现有安全模型是统一管理员鉴权 API（`app/main.py:496-504`），新通道应复用，避免旁路。  
3. 你的部署是 Docker/NAS 场景，浏览器与容器常不在同机；`127.0.0.1` 方案运维复杂且不可审计。

#### 建议实现路径

1. **通道抽象层**  
   - 在评论服务层新增 `channel` 概念：`server`（现有）/`assistant`（新）。  
   - `comment_jobs.checkpoint` 扩展为带 `channel` 和各自分页状态。

2. **助手采集 API（鉴权）**  
   - 新增受鉴权接口：创建采集会话、上报页面模板/分页响应、心跳/完成。  
   - 后端只接收必要字段，统一走 `_comment_item` 映射规则入库（建议抽出到共享模块，避免 core 私有函数耦合）。

3. **自动回退策略**  
   - 优先通道失败时按可判定错误自动回退另一通道，并写入 `comment_jobs.reason`。  
   - 验证码/登录过期统一走 `comment_block` 语义，暂停评论队列，等待人工处理。

4. **与下载任务分离（保持现状）**  
   - 继续使用 `comment_media_busy`，确保下载任务优先。  
   - 评论通道切换仅影响评论 worker，不影响下载 `tasks` 表状态机。

5. **先单作品小规模验证，再批量**  
   - Phase 1：单作品（少量评论）验证模板抓取、分页、去重、回退。  
   - Phase 2：中量作品验证中断恢复与重试。  
   - Phase 3：批量启用。

### B.3 计划改动文件与函数

1. `app/comments.py`
   - `CommentService.step`（`app/comments.py:24`）：引入通道策略、回退、统一暂停语义。
2. `app/store.py`
   - `comment_jobs` checkpoint 结构扩展（`app/store.py:101-103`, `app/store.py:529-564`）。
   - 新增/扩展 comment_* 方法以支持通道状态持久化与回退标记。
3. `app/main.py`
   - 新增助手评论采集 API（复用 `authorize`，`app/main.py:496-504`）。
   - 现有 `/api/works/{work_id}/comments` 保持兼容，允许传通道选择参数（默认策略待定）。
4. `app/core.py`
   - 将 `_comment_item` 迁移或暴露到可复用位置（当前在 `core` 内，`app/core.py:34-78`）。
5. `userscripts/douyin-send.user.js`
   - 增加评论采集协作（模板捕获、分页上报、验证码提示、恢复后继续）。

### B.4 新测试文件与用例（建议）

1. `tests/test_comments_channel_assistant.py`（新）
   - 助手分页上报→入库→去重。
   - 助手通道分页 checkpoint 恢复。
   - 助手失败自动回退 server 通道。
2. `tests/test_comments_service.py`（补充）
   - 双通道切换不破坏 `comment_queue_paused` 语义。
   - 验证码/登录过期在双通道下均触发暂停。
3. `tests/test_comments_api.py`（补充）
   - 助手采集 API 全部鉴权、参数白名单、无 token 401。
4. `tests/test_core_comments.py`（补充）
   - `_comment_item` 抽出后映射一致性回归（server/assistant 共用）。

### B.5 验收标准

1. 两通道写入同一 `comments/comment_assets/comment_jobs`，ID 去重一致。  
2. 单作品验证中，助手通道可完整抓一级+楼中楼并保存 checkpoint。  
3. 通道故障可自动回退；登录/验证码统一暂停并可人工恢复。  
4. 评论与下载任务继续分离：评论不改下载状态机，下载优先策略不变。  
5. 全过程不新增 `127.0.0.1` 接收器依赖，仅用现有鉴权 HTTP API。

### B.6 建议小提交（编号）

1. **B1**：抽象 comment channel 接口 + checkpoint 扩展（不改用户入口）。  
2. **B2**：后端新增助手采集 API（鉴权、上报、入库）。  
3. **B3**：userscript 接入助手采集（单作品最小可用）。  
4. **B4**：双通道选择与自动回退策略。  
5. **B5**：完善测试与文案，开启批量前开关控制。

### B.7 需要你决定的问题

1. 默认通道策略：`assistant 优先` 还是 `server 优先`？  
2. 自动回退是否允许跨会话持续（例如助手离线后直接走 server）？  
3. 助手端出现验证码时，UI 提示文案与恢复动作（自动重试/手动点继续）你偏好哪种？

---

## 统一风险与约束（两项 P1 共用）

### 已验证事实

1. 首页布局与入口位置有明确约束，不能改动（`AGENTS.md:3-5`）。  
2. 评论现有实现强调“手动触发、本地快照、媒体优先、可恢复”，这些是既有产品基线（`docs/superpowers/specs/2026-10-30-offline-comments-design.md:31-40`, `:76-89`）。

### 推测/待确认

1. 若同时落 A+B，建议先 A 后 B：A 影响下载主路径更广，B 先做单作品验证再放量更稳。  
2. 若你要更快试用，可先交“B 的单作品助手通道 MVP + A 的阈值规则最小版（不含>600作者暂停）”。

---

## 人工复核补充（对照 app/main.py:235-300、app/core.py:331-362 实际代码）

1. **“发言”目前会让整个作者任务失败**：`Core.parse` 遇到 `视频/图集/实况` 以外的类型直接 `raise RuntimeError("该作品类型暂不支持…")`（core.py:355-357），整个作者任务失败而不是跳过。A 规则落地前必须先把它改为“计数并跳过/单独记录”，否则 >300 时“只抓视频图片”无从谈起。这一步应作为 **A0** 提交放在 A1 之前。
2. **断点恢复的真实机制**：作者任务没有逐作品游标；每次（重）试都重新 `parse` 整个作品列表，再靠 `saved()` 跳过已完整保存的作品（main.py:246-257）。所以“暂停并保存进度”= 已存作品不重下 + 任务保持 paused/可续。不需要新建游标表，但 >600 统计“本次”数量要按本次 `completed` 计，而不是作品总数。
3. **计数时机**：`parse` 已拉完全部列表后才知道类型数量，此时请求已发生。若希望 >1000 在大量翻页前就停，可先用已有的 `Core.author_count`（core.py:222，未登录会话读主页作品数）预判，parse 后再用实际数复核。需在 A2 中二选一或两者都做。
4. **限流识别**：下载路径目前没有可区分的限流异常，只有通用 RuntimeError 文案；A4 需在 `core.download`/`parse` 上增加 `RateLimited` 类异常（可参照评论通道 `CommentBlocked` 的关键词判断，core.py:297-313）。
5. **“增量优先”**：调度上作者任务与单作品任务共用一条 FIFO 队列（`claim_task`），没有“更新任务优先”的排序；`earliest` 参数存在但调用处从未传入（main.py:247）。A3 应同时处理排序与 `earliest`。
6. **首次/更新判定**：`is_update` 来自 `author_downloaded` 或本次发现已有作品（main.py:240、256），A 规则只应在 `is_update=False` 时生效。

### 修订后的 A 提交顺序
A0 parse 不支持类型改为计数跳过 → A1 计数/首次判定函数 → A2 300/1000 规则 + 数量确认（复用 awaiting_confirmation，文案与画质确认区分）→ A3 更新优先排序 + earliest → A4 RateLimited 异常与 >600 作者暂停 → A5 回归测试与文案。共 6 步。

### 第二评论通道补充
- 参考项目实际做法（已读 README/scripts）：`page_hook.js` 拦截页面第一次真实评论请求作模板，`page_fetch.js` 只替换 `aweme_id/cursor/comment_id` 用页面内 XHR 发出（SDK 自动签名），`page_ui_expand.js` 点“展开回复”兜底，`recv.py` 在 127.0.0.1 落盘；节奏 2-4 秒/次、每 15 次停 8-15 秒；验证码时停下由人处理；>500 条一级评论后接口开始返回重复。需要 Claude in Chrome 扩展驱动，不能无人值守。
- 在 Cloudrift 中落地形态：油猴脚本运行在用户已登录的抖音页面，即可完成 hook+页面内请求，再用现有 Bearer 鉴权 API 把原始页回传 NAS 后台，后台用同一 `_comment_item` 映射入库。因此该通道**只在用户开着对应作品页时工作**，不能做服务器端批量。

---

## 致云已拍板的决定（2026-10-31）

1. **“发言”不管、不采集**：300/1000 门槛只按 视频+图集(含实况) 计数。A0 中非视频/图集/实况类型只计数跳过，不让任务失败。
   - ≤300：全量下载；300–1000：照常下载视频和图集（等同全量，因为本来就不采发言）；>1000：任务进入 `awaiting_confirmation`，确认原因为“数量确认”，文案与画质确认分开。
   - 仅对首次历史采集（`is_update=False`）生效。
2. **限流暂停**：单作者本次已完成 >600 且触发限流时，暂停该作者任务，**1 小时后自动恢复**继续；进度靠现有 `saved()` 去重/重拉列表跳过已存作品，不新建游标表。
3. **评论默认通道**：油猴助手新通道优先，失败自动回退服务器通道（B 部分本次不做）。

## 进度记录
- A0 f40e07e：不支持类型（发言等）计数跳过，任务不再失败（230 测试通过）
- A1 e169ea8：视频+图集(含实况)计数与首次/更新判定（236）
- A2 422f2cf：首次采集 >1000 进入“数量确认”，与画质确认分开（243）
- A3 98d549e：更新任务优先出队，更新任务传 earliest（252）
- A4 f8b3a91：RateLimited 异常；本次完成 >600 且限流 → 暂停作者，1 小时后自动恢复（264）
- A5 55aa4f9：回归测试与文案（266，node 13/13）
- 待做：B 部分（油猴评论新通道，默认优先、失败回退服务器）；部署到飞牛 NAS 后实测
- B3 fd371c1：userscript: on logged-in douyin work page hook page's own first comment request as template, re-issue via in-page XHR replacing aweme_id/cursor/comment_id (page SDK signs), fetch top-level and replies, pace 2-4s per request and extra 8-15s every 15 requests, upload pages to B2 API; single work only. On login expired/captcha stop and report via existing pause style. Bump userscript @version (1.11.1 -> 1.12.0) per repo convention and add CHANGELOG.md entry. Keep old server comment channel unchanged. Both channels share comment table, field mapping (_comment_item) and comment-ID dedup.
- B4 14d2352：channel selection: default assistant channel first; if assistant unavailable/fails/times out, auto fallback to server channel; login expired/captcha uses existing pause with clear message. Keep old server comment channel unchanged. Both channels share comment table, field mapping (_comment_item) and comment-ID dedup.
