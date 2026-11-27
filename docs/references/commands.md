---
km_id: reference.mashu-commands
km_type: reference
domain: mashu
status: active
owner: mashu
last_verified: 2026-11-27
source_of_truth:
  - mashu/studio.py
  - mashu/server.py
validated_by:
  - manual-code-review
tags:
  - domain:mashu
related:
  - reference.index-docs
  - workflow.report-flow
  - workflow.feature-state
  - reference.platforms
---

# mashu 命令参考

## 目标

按角色列出 `mashu` 命令。会话里直接敲 `mashu`；找不到就用系统提示里给的完整路径。

## 所有角色

所有命令支持 `--json`（成功返回单行 `{"ok":true,"api_version":1,...}`，失败返回 `{"ok":false,"code":...,"error":...,"fix":...}`）；退出码：`0` 成功、`1` 一般错误、`2` 用法错误、`3` 找不到、`4` 需确认（确认信封）、`5` 被规则拦截、`6` 冲突。正文支持 `-` 从标准输入读取（避开 `$` 展开）。

### 核心通信（6 个动词）

| 命令 | 用途 |
| --- | --- |
| `mashu me [--json]` | 我是谁：角色、项目、功能、可发送对象地址与手头待办计数 |
| `mashu inbox [--all] [--json]` | 收件箱：发给我的待处理、已过时（标建议 `close`）与用户挂久提醒；纯通报类（`fyi`/`done`）看过自动结案 |
| `mashu send <对象> "…"\|- [--type ask\|approve\|fyi\|done\|blocked\|handoff] [--options "A\|B" --recommend A --why "…"] [--gate …] [--re <id>] [--supersedes <id>] [--key <去重键>]` | 按地址发消息：`user`、`boss`、`lead`、`up`、`#N`、`<项目>`、`<项目>#N`、`@crew`。`--supersedes` 和同功能新建同类卡会自动把旧卡标为「已被 #N 取代」并结案；`--key` 重试去重；权限由调度器按层级校验 |
| `mashu reply <id> [--choice A] ["补充"\|-]` | 回答 / 批复上报；用选项编号（`A`/`B`/`1`/`2`）精准匹配选项，可另附补充说明 |
| `mashu forward <id> <user\|up\|boss> "一句意见" [--recommend A --why "…"] [--confirm]` | 转交上报（原 `pass`）；审批卡转交用户需确认信封 `--confirm` |
| `mashu close <id> --why "过时：…" [--silent] [--confirm]` | 结案（含过时清理与静默结案）；结案用户手上的卡需 `--confirm` |

### 自描述与常用命令

| 命令 | 用途 |
| --- | --- |
| `mashu help [命令]` | 查看命令用法与真实例子 |
| `mashu schema --json` | 输出全部命令、参数、退出码、消息类型与地址约定 |
| `mashu status [项目]` | 全局或某个项目的状态（第一行显示线上快照版本与工作区领先提交数，含每个会话的详细状态与点对点等待） |
| `mashu handoff --now … --next … [--open … --files … --evidence … --risk … --docs …]` / `--file PATH` | 写交接文档。`--docs` 记录回写节点或无需回写的理由 |
| `mashu skills status [--json]` | 列出 caesar-docs 来源和各安装位置的版本、指纹及漂移 |
| `mashu skills sync [--apply] [--managed-only] [--project ID]` | 默认预览。应用时备份并替换用户级链接，`--managed-only` 跳过用户级位置，`--project` 限定工程副本 |
| `mashu finish --reason "…"` | 本阶段完成 / 交接 |
| `mashu release [--sha <提交>] [--rollback] [--same] [--dry-run] [--no-restart] [--reason "…"] [--json]` | 发布已提交版本到 `state/runtime/` 快照并重启服务（只给主脑和终端用户；先在临时 worktree 跑 `.mashu/check.sh`，失败拒绝发布返回 `code=check_failed`；`--rollback` 回滚上一版本跳过检查；`--same` 仅重启不换版本；`--no-restart` 只更新快照不重启；详见 [平台说明](platforms.md)） |
| `mashu restart-server [--same] [--sha <提交>] [--dry-run] --reason "…" --confirm` | 走 `release` 流程发布并重启网页服务与调度器（只给主脑，需确认信封 `--confirm`；只想重启不换版本加 `--same`；各会话不受影响） |
| `bin/mashu-dev <命令>` | 开发入口：固定跑工作区代码（设 `MASHU_DEV=1` 绕过 `state/runtime/` 快照）；手测须配合独立临时 `MASHU_HOME` |

### 兼容别名（旧通信命令全部保留）

`ask`=`send up --type ask`、`tell`=`send up --type fyi`、`report`=`send boss`、`memo`=`send <项目>`、`note`=`send <项目>#<编号>`、`peer`=`send #N --type ask`（`--fyi` 为 `fyi`）、`sync`=`send @crew --type fyi`、`pass`=`forward <id> up`、`answer`=`reply`、`reports`=`inbox`。

## 主脑

| 命令 | 用途 |
| --- | --- |
| `mashu scan` / `mashu add-project <path> [--title …]` | 看未登记的文件夹 / 登记项目 |
| `mashu send <项目> "…"`（旧 `memo`） | 给子脑下指示（没在运行会自动启动），推动项目的主要方式 |
| `mashu priority <项目>/<编号> <数字>` | 排队优先级，越大越先 |
| `mashu start / stop / restart <项目>/<编号>` | 启动 / 停止 / 换新执行者；agent 调 stop 必须带 `--reason`；有生效中的上线/不可逆批准时返回退出码 4，需 `--confirm`（兼容 `--force`） |
| `mashu inbox` / `reply <id> --choice A` / `forward <id> user "意见"` / `close <id> --why "…"` | 处理收件箱与过时卡 |
| `mashu decide "…" [--why …] [--ratify]` | 记下你的决定：默认记为已执行（简报列出，用户可推翻）；拿不准、要用户点头的加 `--ratify` 挂“待追认” |
| `mashu browser "任务" [--model …] [--cwd …] [--timeout 分钟]` | Code Mode 会话用不了浏览器：把浏览器的活交给不开 Code Mode 的独立 `claude -p --chrome` 会话，结果打印回来，记录在 `state/browser/`；长任务配 `run_in_background` |
| `mashu say "…"` | 长任务中途在主对话说一句 |
| `mashu gemini-models [--refresh]` | Gemini（agy）可选模型；`--refresh` 跑 `agy models` 更新缓存 `state/gemini-models.json`（要先登录 agy；没登录时用内置兜底列表）。网页「模型池」里也有刷新按钮 |
| `mashu platforms [--check]` | 各家 CLI（Claude / Gemini / Codex / OpenCode）能不能用、版本、模型数；`--check` 再跑一遍安全拦截自检（不调模型）。Codex / OpenCode 的接法见 [平台说明](platforms.md) |
| `mashu opencode-models [--refresh]` | OpenCode 可选模型（`provider/model`、支持的思考档位、窗口、是否免费）；`--refresh` 跑 `opencode models --verbose` 更新缓存 `state/opencode-models.json`。网页「模型池」里也有刷新按钮 |
| `mashu preset list [--json]` | 只读列出模型池预设（id、名字、CLI、模型、思考、账号、备选、哪些角色默认在用）；增删改在网页「模型池」页做 |
| `mashu config export --out <文件> [--with-secrets]` / `import <文件> [--dry-run] [--yes]` / `migrate` | 主配置文件（`state/studio.json`）导出、差异预览导入与迁移；`--with-secrets` 交互输口令加密，默认省略密钥。详见 [平台说明](platforms.md) |
| `mashu playbook list / show / check / commit / apply / restore` | 工作流管理；目标写 `film:<项目>`（项目默认工作流）、`template:<项目>/<id>`（项目专用模板，在 `<工程>/.mashu/playbooks/`）或 `template:<id>`（麻薯通用模板）。见 [项目资料制作规范](../workflows/project-assets.md) |
| `mashu retro [项目]` / `mashu digest` / `mashu self-iterate …` | 复盘数据 / 简报数据 / 每日自迭代 |

## 子脑

| 命令 | 用途 |
| --- | --- |
| `mashu new-feature <项目> <英文slug> --brief "需求原文" --plan <方案文件> [--title "中文标题"] [--workflow] [--touches <glob…>] [--account <id>] [--workdir <子目录>] [--preset <名或id>] [--cli claude\|gemini\|codex\|opencode] [--cli-account <id>] [--model <模型>] [--effort low\|medium\|high\|xhigh\|max]` | 新建功能（默认直接做）；优先用 `--preset <名或id>` 指定模型池预设（`mashu preset list` 可查，不能和 `--cli / --cli-account` 同时给），也可用 `--cli / --model / --effort` 单独指定，写进开工审批卡，用户可以在卡上改了再批准；批准时的配置存进 state.json 的 `exec`，执行者按它启动；`--workdir` 让执行者在工程里的某个已存在子目录里启动（项目适配器也可能在开功能时自己设，见 [项目资料制作规范](../workflows/project-assets.md)「项目适配器」）；麻薯开的功能必须带 `--plan`（需求 + 技术方案，已和用户谈清楚），开好后自动申请用户「批准开工」，批准后执行者才启动；`--touches` 预先声明改动范围，重叠时通知双方和子脑；`--account` 归哪个账号（项目有 `.mashu/accounts.json` 时，id 不存在会报错并列出可选；不写按 accounts.json 推，见 [项目资料制作规范](../workflows/project-assets.md)「accounts.json」）；有账号的项目 `mashu status` 的功能行带账号名 |
| `mashu preset list [--json]` | 只读列出模型池预设，供 `new-feature --preset` 选用 |
| `mashu start / stop / restart <项目>/<编号>` | 启动 / 停止 / 换新执行者（restart 会换一个新名字；有生效批准时 stop 需 `--confirm`） |
| `mashu send #<编号> "…"`（旧 `note`） | 给执行者补充指令 |
| `mashu feature status --ref <项目>/<编号>` | 看某个功能的状态 |
| `mashu feature block --ref <项目>/<编号> --text "暂停：等…"` / `--clear` | 标阻塞 / 暂停：执行者 finish 时不自动申请下一阶段、直接收工；解除后发消息再拉起 |
| `mashu inbox` / `reply` / `forward` / `close` | 处理收件箱；`forward <id> user` 发到项目对话给用户；`close` 清理过时卡 |
| `mashu waive <项目>/<编号> --reason "…"` | 自动检查失败但不是这个功能造成的，豁免一次 |
| `mashu send boss "…"`（旧 `report`） | 跨项目的事交给主脑 |
| `mashu decide "…" [--seq <编号-名称>]` / `mashu priority …` | 记决定 / 本项目优先级 |
| `mashu playbook show film:<项目>` / `mashu retro <项目>` | 看工作流 / 复盘数据 |

## 执行者

`mashu me` 查看身份与可发对象；`mashu inbox` 查看待办与点对点消息；`mashu send @crew "进展"`（旧 `sync`）发布项目进展；`mashu send #<编号> "消息" --type ask|fyi`（旧 `peer`）发给在线同项目执行者并抄送子脑（`ask` 记为等待对方回复，`fyi` 只通知不等回复）；`mashu codex` 显示法典和当前分工，子脑可用 `mashu codex note "约定"` 追加。

见 [功能状态文件](../workflows/feature-state.md)（feature / ac / claim）。

## 网页聊天里的图片、文件和打断

- 用户发来的图片 / 文件存在 `state/uploads/<日期>/`，消息里是 markdown 图片语法（图片）或带 📎 的 markdown 链接（其它文件），括号里是绝对路径，用 Read 打开。
- 给用户看图：存成文件，回复里用 markdown 图片语法、括号里写绝对路径；其它文件用 📎 加 markdown 链接（精确写法见角色提示词）。正文里裸写的图片绝对路径也会显示。能显示的位置：上传目录、项目目录、麻薯代码目录、临时目录、桌面 / 下载 / 图片；隐藏目录、`.env`、密钥文件不显示。
- 用户可以打断一轮（网页 Esc / ■ 打断 / `/stop`，等于 Claude Code 里按 Esc）：会话不关，已写出的部分留在对话里，看到 `Request interrupted` 就停下原计划，按下一句做。
- 网页斜杠命令：`/stop` `/stopall` `/attach` `/screen` `/handoff` `/new` `/provider` `/model` `/effort` `/compact` `/feature` `/digest`（`/provider` 可选 claude / gemini / codex / opencode；`/model` 按这个对话的平台用对应的模型名）。
- 交接 vs 新会话（网页对话头按钮或 `/handoff`、`/new`）：交接 = 调度器发提醒（同 75% 提醒，不照做 `handoff_grace_minutes` 后强制），你写 handoff.md + `mashu finish --reason "上下文交接"`，同窗格换新会话读交接续做；新会话 = 直接在原窗格开新会话，不带上一段对话、不读交接。
- 模型 / 思考程度按对话记在 `meta` 表 `pref:<频道>`，角色默认在 `state/studio.json` 的 `models` / `efforts`；会话空闲时调度器在原窗格 `--resume` 重开同一个会话让新的 `--model` / `--effort` 生效，对话不丢。
- Codex / OpenCode（2026-11-24，#2）：平台多了 Codex、OpenCode，模型分别记 `xmodel` / `omodel`，角色默认在 `studio.json` 的 `codex_models` / `opencode_models`；思考程度共用 `effort`，按模型支持的档位取最接近的。只有装了、版本做过安全拦截实测的才能选（网页上灰掉并写原因）。执行者的配置优先级：对话单独设 > 功能批准开工时定的 `exec` > 角色默认。怎么启动、hooks 怎么注入、会话记录在哪见 [平台说明](platforms.md)。
- Claude / Gemini（2026-11-23，#9）：模型选择上面多一层「平台」。按对话记在 `pref:<频道>` 的 `provider`（Gemini 模型记 `gmodel`，和 Claude 的 `model` 分开），角色默认在 `studio.json` 的 `providers` / `gemini_models`；没设 = Claude。平台只对新会话（新会话 / 交接）生效，恢复会话沿用开会话时那家（`meta` 里 `provider:<会话 ID>`、`camprov:<机位>`）。Gemini 会话用 Antigravity CLI：`agy --dangerously-skip-permissions --settings state/launch/<id>-agy.json --add-dir … --model --effort [--conversation <ID>] -i <首句>`；系统提示由 SessionStart hook 注入（agy 没有 `--append-system-prompt`），hooks 写在全局 `~/.gemini/config/hooks.json` 的 `mashu` 项（命令先判断 `$MASHU_SESSION`，用户手开的 agy 不受影响），状态栏走 `mashu statusline --agy`（上下文 %、模型、token、花费）。`mashu browser` 仍用 Claude。
- 账号 5 小时 / 周额度：受管会话的状态栏回调把 `rate_limits` 写到 `state/ratelimits.json`，网页左下角显示。

## 相关节点

[上报与权限](../workflows/report-flow.md)
