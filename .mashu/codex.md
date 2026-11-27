# 麻薯电脑管家工程法典

> 子脑维护。最后更新 2026-11-27（星期五）。这个工程就是正在运行的麻薯系统本身，规则见 `.mashu/rules.md`（不重启服务、不动 `state/`、测试只用 `python3 -m unittest discover -s tests`）。

## 共同目标与验收

**目标**：让麻薯稳定地同时跑多个项目、多个执行者，用户少操心（少排队、少重复请示、网页和终端一致）。

| 功能 | 执行者 | 交付 / 状态 | 方案 |
| --- | --- | --- | --- |
| #18 gemini-quota 额度改读 agy /quota + 左下角用量面板重做 | 已完成并上线（4685784、2c9fec4、e35ab4c） | accounts.py `GeminiOps.quota`/`parse_agy_quota`；studio.py 只改 Gemini 体检循环里失败时保留旧值的处理，status_data 只增用量字段；web 改左下角整个 `#sideFoot`、模型池 Gemini 卡片 | `.mashu/plans/gemini-quota.md` |
| #17 stable-runtime 线上跑稳定快照（发布 = 重启服务） | 已上线（线上 13bad01，15:10 发布） | `bin/mashu` 分流器、`bin/mashu-dev`、`start.sh` 服务路径、studio.py 路径常量 + `MASHU_CONTENT` + `cmd_release`/`runtime_info`/`cmd_restart_server`、`status_data` 只加 `runtime` 字段、server.py 重启接口、web 右上角。不碰 #16 的通信命令/hook_prompt/卡片，不碰 #15 的左栏 | `.mashu/plans/stable-runtime.md` |
| #16 agent-cli 统一收发 + 收件箱分拣 + agent 友好输出 | 已上线（b1b5703…e164ae5） | 新增 `cmd_me/inbox/send/reply/forward/close`、`resolve_address`、`triage_reports`、`fail()`；旧通信命令改为别名；`reports` 加列；hook_prompt 摘要；web 卡片类型/过时；提示词瘦身。`peer` 只调用 #15 的函数，不改其表 | `.mashu/plans/agent-cli.md` |
| #15 左栏状态细化 + 点对点等待 | 已上线（2e81a8b、6522542） | 改 `seq_status`/`status_data`/`bg_tasks_of`/`cam_view_bg`/`launch_queue`（记排队原因）/`cmd_collaboration`（peer 等待）/`collaboration_context`、新表 `peer_waits`、web 左栏与对话头部 | `.mashu/plans/status-detail.md` |
| #14 模型池（预设 + 备选预设） | 已上线（1fb06e9、1694bfd） | 改 `session_pref`/`code_pref`/`choose_account`/`set_pref`/launch 取配置/defaults 动作/`pitch`、`config_file.py` 迁移、web 模型池页与各选择器 | `.mashu/plans/model-pool.md` |
| #13 caesar-docs 治理 | 很好 | 已完成（d2a859a），待用户点「重启服务」生效 | `.mashu/plans/docs-governance.md`，依据 `.mashu/drafts/caesar-docs-audit-2026-11-27.md` |
| #11 并行调度 + 法典 + 协作板 + 点对点 | — | 已完成（6e00cb8），服务 04:05 重启后已生效 | `.mashu/plans/parallel-codex.md` |
| #12 semrush | — | 已归档 | 无 |

**验收**：每个功能的 AC 写在方案里；改 Python 的功能完成后，由用户点「重启服务」，子脑用 curl 核对接口后才算上线。

**待决**：
- **#17 ↔ #18 并行分工（2026-11-27 星期五）**：
  - #18 在 studio.py 里只改 Gemini 体检循环（`refresh_accounts` 一带，约 2250–2300 行）的额度失败处理，以及 `mashu account` 的 Gemini 输出。主要改动放在 `mashu/accounts.py`。
  - #17 不碰这些地方。#18 也不碰路径常量、`cmd_release`/`cmd_restart_server`、`status_data`、argparse 里的 release 段。
  - 网页：#18 只改左下角额度条的 Gemini 项和模型池页的 Gemini 卡片，#17 只改右上角。
  - #18 调用 agy 时用 `shutil.which('agy')`，不依赖 `CODE`/`BIN` 路径，这样 #17 改路径常量不会影响它。
- **#16 ↔ #17 并行分工（2026-11-27 星期五）**：
  - `cmd_restart_server` 归 #17，改成走 release。#16 第 2 步加的确认信封（不带 `--confirm` 返回退出码 4）要保留：#17 改函数体时沿用 #16 的 `confirm_or_fail`/`MashuError`，不另写一套。
  - `cmd_release` 的报错和 JSON 输出用 #16 的 `fail`/`out_ok`/退出码枚举（检查失败归 code=`check_failed`）。
  - 路径常量（`CODE`/`HOME`/`KMAP`/`PLAYBOOKS`/`BIN`）、`bin/*`、`start.sh` 归 #17。#16 不碰。
  - 通信命令、`hook_prompt`、`triage_reports`/`evaluate_stale`、`schema`/`help` 归 #16。#17 新增的 `release` 子命令要在 #16 的 schema 和 help 里登记：#17 照 #16 的格式自己补上。
  - 网页：#16 只改上报卡片，#17 只改右上角版本和重启区域。argparse 段两边各加自己的子命令，用 `git add -p` 暂存。
- **#15 返工 ↔ #16 并行分工（2026-11-27 星期五）**：
  - #15 只改左栏、对话头部的状态文字：studio.py 的 `seq_status`/`status_data`/`assistant_detail` 等 detail 文案函数，以及 web/app.js、web/style.css 中左栏和对话头部的渲染与样式。
  - #16 不碰上面这些函数和左栏渲染。#16 的网页改动只限上报卡片（类型标签、取代 / 过时标记）。
  - #16 把 `peer` 包成 `send #N` 时只调用 #15 的 `cmd_collaboration`/`peer_waits` 辅助函数，不改它们的签名和表结构；#15 返工期间也不改这些签名。
  - 两边都改 web/app.js 和 style.css：用 `git add -p` 只暂存自己的 hunk，编辑前重读文件。冲突时 `mashu peer` 对方，谈不拢交子脑。
- `mashu/studio.py` 工作区有一处未提交改动（`BROWSER_PROMPT` 加了「不要起无头 Chrome」一条），归属待确认。**不是你写的就不要暂存或提交它**。
- #13 在 studio.py 改 `skill_host`、`claude_cmd`/`agy_cmd`/`opencode_cmd`、check 流程、handoff/finish；别的功能要碰这些函数先 `mashu peer` #13。
- #13 只读 caesar 各仓库文档；caesar 工程副本同步要等 caesar #7 docs-hardening 结单或先和它确认。

## 模块分工与接口

### 区域

| 区域 | 说明 | 默认主人 |
| --- | --- | --- |
| `mashu/studio.py` | 调度器核心（8000+ 行），几乎每个功能都会改 | 按函数分：开工时用 `mashu sync` 写清要改哪些函数，别人正在改的函数不碰 |
| `mashu/accounts.py`、`mashu/config_file.py` | 账号池、主配置 `state/studio.json` 的读写与迁移 | 当前无人 |
| `mashu/prompts/**` | 主脑 / 子脑 / 执行者提示词模板 | 当前无人；改了要在 sync 里写清影响哪些角色 |
| `server.py` | 网页后端 / API | 当前无人 |
| `web/**`（app.js、style.css、index.html） | 网页前端，静态文件刷新即生效 | 当前无人 |
| `docs/**` | 麻薯文档；`docs/memory/**` 只由主脑改 | 改哪一篇在 sync 里说一句 |
| `tests/**` | 每个功能只加自己的测试文件或测试类，不改别人的用例 | 各功能自己 |
| `state/**` | 正式运行状态 | **所有人都不改** |

### 接口约定
- 新的 CLI 子命令、hook 注入段、`/api/*` 接口：在下面「接口变更记录」写一行（调用方、参数、返回、验证命令）。
- 改了数据库表结构或 `state/studio.json` 格式，要写迁移，并在 sync 里说明。
- `collaboration_context` / `project_codex` / `parallel_roster` 由 #11 定义，别的功能要改先 `mashu peer` #11（#11 已结单，现归子脑）。

## 共享文件

编辑前重读，只改约定区域。共享文件用 `git add -p` 暂存自己的改动，提交前看 `git diff --cached`，提交标题带功能标签，比如 `[mashu#12]`。子脑直接小修时标题写清改了什么，同样只提交自己的 hunk。
- 同一工作目录、同一条 main 分支，**git 暂存区是共用的**：暂存和提交连着做；整文件提交优先用 `git commit -- <路径>`；不用 `git add -A`、`git commit -a`；不 `stash`、`checkout`、`reset`、`rebase`。
- 推送 origin 前由子脑扫一遍要推的改动有没有真实密钥。
- 热点被占用时先做其他部分，用 `mashu peer` 协商，谈不拢 `mashu tell` 交子脑裁决。

## 接口变更记录

| 时间 | 功能 | 变更 |
| --- | --- | --- |
| 2026-11-27 | #11 | 新增 `mashu sync "…"`（写协作板 `.mashu/board.jsonl`，其他会话下一轮注入）、`mashu peer <编号> "…"`（点对点，子脑抄送）、`mashu codex [note]`（查看 / 记录法典）；启动不再因范围重叠排队，热点改为写入时短租约（6e00cb8） |
| 2026-11-27 | #13 | `mashu skills status [--json]`：列出 caesar-docs 各安装位置的版本和指纹；`mashu skills sync [--apply]`：默认只预览，`--apply` 才写（用户级、麻薯级做成软链接，工程副本按锁文件同步）。`mashu handoff --docs "…"` 写入 `handoff.docs`，结单时核验 docs 回写。SessionStart 只注入 docs 入口和索引的路径。实现在 `mashu/skill_sync.py`、`mashu/docs_governance.py`（d2a859a） |
| 2026-11-27 | #15 | 新表 `peer_waits`、配置 `peer_wait_minutes=30`；`mashu peer <编号> "…" [--fyi]`（默认记为等待对方回复）；`/api/state` 每个 sequence 加 `detail`/`detail_since`/`waits`/`waited_by` 和 `peer` 状态，子脑 / 主脑加 `detail`/`detail_since`（`assistant_detail`），`cam_view_bg` 加 `bg_tasks`（保留 `bg`）（2e81a8b） |
| 2026-11-27 | 子脑 | `hook_camera(conn, fmt)`：prompt / stop / session / notify 这几个 hook 改用它，嵌套子会话（平台和 camprov 不一致，或进程链上有两层以上 agent CLI）不认机位；pretool 安全拦截仍用 `env_camera`（2f15519） |

## 当前并行功能
`mashu codex` 与会话上下文根据调度状态列出当前功能。
