# 麻薯电脑管家工程法典

> 子脑维护。最后更新 2026-11-27（星期五）。这个工程就是正在运行的麻薯系统本身，规则见 `.mashu/rules.md`（不重启服务、不动 `state/`、测试只用 `python3 -m unittest discover -s tests`）。

## 共同目标与验收

**目标**：让麻薯稳定地同时跑多个项目、多个执行者，用户少操心（少排队、少重复请示、网页和终端一致）。

| 功能 | 执行者 | 交付 / 状态 | 方案 |
| --- | --- | --- | --- |
| #11 并行调度 + 法典 + 协作板 + 点对点 | 原来你什么都不要 | 已提交 6e00cb8，回归通过，用户已确认界面；需用户点「重启服务」生效 | `.mashu/plans/parallel-codex.md` |
| #12 semrush | 我怀念的 | 网页开的会话，目标待子脑确认；目前只声明了 `.mashu/features/12-semrush/**` | 无 |

**验收**：每个功能的 AC 写在方案里；改 Python 的功能完成后，由用户点「重启服务」，子脑用 curl 核对接口后才算上线。

**待决**：
- `mashu/studio.py` 工作区有一处未提交改动（`BROWSER_PROMPT` 加了「不要起无头 Chrome」一条），归属待确认。**不是你写的就不要暂存或提交它**。
- #12 要改代码前，先 `mashu claim` 声明范围并 `mashu sync` 一句说明要动哪些函数。

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
- `collaboration_context` / `project_codex` / `parallel_roster` 由 #11 定义，别的功能要改先 `mashu peer` #11（#11 结单后归子脑）。

## 共享文件

编辑前重读，只改约定区域。共享文件用 `git add -p` 暂存自己的改动，提交前看 `git diff --cached`，提交标题带功能标签，比如 `[mashu#12]`。子脑直接小修时标题写清改了什么，同样只提交自己的 hunk。
- 同一工作目录、同一条 main 分支，**git 暂存区是共用的**：暂存和提交连着做；整文件提交优先用 `git commit -- <路径>`；不用 `git add -A`、`git commit -a`；不 `stash`、`checkout`、`reset`、`rebase`。
- 推送 origin 前由子脑扫一遍要推的改动有没有真实密钥。
- 热点被占用时先做其他部分，用 `mashu peer` 协商，谈不拢 `mashu tell` 交子脑裁决。

## 接口变更记录

| 时间 | 功能 | 变更 |
| --- | --- | --- |
| 2026-11-27 | #11 | 新增 `mashu sync "…"`（写协作板 `.mashu/board.jsonl`，其他会话下一轮注入）、`mashu peer <编号> "…"`（点对点，子脑抄送）、`mashu codex [note]`（查看 / 记录法典）；启动不再因范围重叠排队，热点改为写入时短租约（6e00cb8） |

## 当前并行功能
`mashu codex` 与会话上下文根据调度状态列出当前功能。
