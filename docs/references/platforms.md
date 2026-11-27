---
km_id: reference.platforms
km_type: reference
domain: mashu
status: active
owner: mashu
last_verified: 2026-11-27
source_of_truth:
  - mashu/studio.py
  - mashu/platforms.py
  - mashu/live_parsers.py
  - mashu/accounts.py
  - mashu/config_file.py
  - integrations/opencode/mashu.js
validated_by:
  - tests/test_platforms.py
  - tests/test_live_parsers.py
  - tests/test_accounts.py
  - tests/test_gemini_quota.py
  - tests/test_config_file.py
  - manual-real-session
tags:
  - domain:mashu
related:
  - reference.index-docs
  - reference.mashu-commands
---

# 平台说明：Claude / Gemini / Codex / OpenCode

## 目标

说明麻薯的会话能跑在哪几家 CLI 上、每家怎么启动和恢复、麻薯的 hooks（安全拦截、回复转发、状态）怎么注入、会话记录在哪、已知限制。命令用法见 [命令参考](commands.md)。

## 选哪家

> #14 模型池已升级：原「账号池」改名「模型池」，新增预设与备选预设自动切换。

- 网页顶部「模型池」页维护**预设**（`CLI + 模型 + 思考 + 账号 + Code Mode + 备选预设`）与**角色默认**（主脑 / 子脑 / 执行者各选一个预设），存放在 `state/studio.json` 的 `presets` 和 `role_presets`（`schema_version: 2`，首次升级自动从旧 `defaults` 迁移并备份）。
- 对话头部 ⚙、新会话弹框、新功能弹窗、开工审批卡第一格均为「预设」下拉，选了自动填好其余几格；单独改任意一格后预设格显示「自定义」。
- 解析优先级（统一由 `resolve_exec` 计算）：对话里单独改过的字段 > 对话选的预设 > 功能开工时定的 `exec`（预设或字段）> 角色默认预设 > CLI 默认。
- **备选预设自动换**：仅在开新会话时触发（恢复会话 `--resume` 不换）。当主预设 CLI 不可用、指定号状态异常，或已知额度任一窗口已用 ≥ `settings.preset_fallback_pct`（默认 95%，额度未知视为可用；预设账号为自动且同 CLI 池有可用号时不换）时，沿备选链找第一个可用的预设（最多 3 跳，防环）；切换只在对话内发一条系统消息并记审计，不改对话偏好。
- 子脑开功能优先用 `mashu new-feature … --preset <名或id>`（可用 `mashu preset list [--json]` 查询，不能与 `--cli / --cli-account` 同时给）；开工审批卡默认选中该预设，用户可在卡上修改后再批准。
- 平台只对新会话生效；恢复会话沿用开会话时那家（`meta` 的 `provider:<会话 ID>`、`camprov:<机位>`）。
- 思考程度共用一组（low / medium / high / xhigh / max），Codex / OpenCode 按模型支持的档位取最接近的（`platforms.nearest_effort`）。
- **能不能选**：Codex、OpenCode 要装了、版本在已验证范围内（`studio.VERIFIED_VERSIONS`：Codex 0.166.x、OpenCode 1.18.x；`studio.json` 的 `platform_versions` 可追加）。不满足的在网页上灰掉并写原因，`set_pref` / 默认设置 / 开工卡都拒绝；偏好里写着但现在不能用且无可用备选的，新会话退回 Claude 并提示。CLI 升级后先实测安全拦截，再把版本加进去。
- `mashu platforms --check` 看各家状态，并跑一遍安全拦截自检（只验证输入格式转换和判定，不调模型）。

## 对照表

| | Claude Code | Gemini（agy） | Codex | OpenCode |
| --- | --- | --- | --- | --- |
| 启动 | `claude --settings … --append-system-prompt … --session-id <新 ID>` | `agy --settings … -i <首句>` | `codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust -C <目录> --add-dir … -m … -c model_reasoning_effort=… -c developer_instructions=… -- <首句>` | `opencode <目录> --auto --prompt <首句>` |
| 恢复 | `--resume <ID>` | `--conversation <ID>` | `codex resume <ID> …` | `opencode <目录> --auto -s <ID>`，首句由调度器等界面就绪后粘贴（`-s` 时 `--prompt` 不会自动提交） |
| 会话 ID | 麻薯预先指定 | SessionStart hook 回写 | SessionStart / UserPromptSubmit hook 回写 | 插件第一轮回写 |
| 系统提示 | `--append-system-prompt` | SessionStart hook 注入 | `-c developer_instructions=`（每次启动、恢复都传） | 插件 `experimental.chat.system.transform` 追加 `MASHU_SYSTEM_FILE` |
| hooks 在哪 | `--settings state/claude-settings.json`（只对受管会话） | 全局 `~/.gemini/config/hooks.json` 的 `mashu` 项 | 麻薯专用 `CODEX_HOME`（`state/codex-home`）的 config.toml 里内联 `[[hooks.*]]` | 麻薯插件（`OPENCODE_CONFIG_DIR=state/opencode-config` → `plugins/mashu.js`） |
| 会话记录 | `~/.claude/projects/<目录>/<ID>.jsonl` | `~/.gemini/antigravity-cli/brain/<ID>/.system_generated/logs/transcript.jsonl`（及 `transcript_full.jsonl`、`messages/`） | `state/codex-home/sessions/YYYY/MM/DD/rollout-*-<ID>.jsonl` | OpenCode 自己的库 `~/.local/share/opencode/`；麻薯读插件原子写的 `state/live/<机位>.json` |
| 网页实时同步 | transcript 完整解析（工具/思考/待办/子代理/后台命令） | `live_parsers.parse_agy`（工具列表/状态/耗时、中间文字、思考、后台任务、子代理、待办、`agy_last_reply` 真实格式） | `live_parsers.parse_codex`（rollout 完整事件含 Code Mode 内层命令/改文件、推理摘要、`update_plan` 待办、后台 shell、子代理） | 插件写入 + `live_parsers.parse_opencode`（工具列表/状态/耗时、推理、待办、`task` 子代理及子工具、中间文字） |
| 上下文 / 用量 | 状态栏 + transcript | 状态栏 | rollout 里的 `token_count`（Stop 时 + 长轮每 10 分钟） | 插件：每条助手消息完成调 `statusline --opencode`（占比 = 这条消息 tokens / `opencode models` 的窗口），每轮结束把会话累计 tokens 和花费交给 `hook-stop` |
| 打断 | Esc | Esc | Esc | 连按两次 Esc |

## Codex 怎么接（不改 `~/.codex`）

- **独立 CODEX_HOME**（`platforms.build_codex_home`）：Codex 在完全放开的模式下会把工作目录的信任写进 `$CODEX_HOME/config.toml`（实测 0.166：`-c projects.….trust_level` 也挡不住），所以麻薯会话不用 `~/.codex`：
  - `config.toml`：用户 config.toml 的副本（模型提供方、MCP、默认模型都一样），去掉 `hooks` / `plugins` / `marketplaces` / `projects` 这几节，末尾加麻薯的 hooks；用户的改了就重抄。工作目录的信任由麻薯写进这份副本（不然 TUI 会弹「Trust this folder?」）。
  - `auth.json`、`auth.lock`、`AGENTS.md`：软链接到用户的（登录共用；Codex 刷新令牌时写回同一个文件，锁也共用）。如果某天 `auth.json` 变成了普通文件，麻薯会提醒，删掉后下次启动重新链接。
  - `skills/caesar-docs`：麻薯的 docs skill。
- **hooks**：`SessionStart`（matcher `startup|resume|clear|compact`）、`UserPromptSubmit`、`Stop`、`PreToolUse`（matcher `*`），命令是 `mashu hook-… --codex`。Codex 的 hook 输入和 Claude 同格式（`session_id`、`transcript_path`、`tool_input`、`last_assistant_message`），输出必须为合法 JSON（`SessionStart` / `UserPromptSubmit` 走 `hookSpecificOutput.additionalContext` 注入上下文与状态文件；`PreToolUse` 拒绝用 `hookSpecificOutput.permissionDecision=deny`）。内联 hooks 没在 `/hooks` 里点过信任，所以带 `--dangerously-bypass-hook-trust`（它也会放行项目级 `.codex/hooks.json`，麻薯的工程里一般没有）。
- **工具名**：shell / `exec_command` 报成 `Bash`（`command`）；`apply_patch` 的 `command` 是补丁全文，麻薯拆出每个文件当 `Write` 检查（一个被拦就整次拦下）；`write_stdin` 往已开终端里打的字按 Bash 命令查。code mode（JS `exec` 里调工具）下内层的 exec_command / apply_patch 照样过 PreToolUse（实测）。
- 用户自己的 `~/.codex/hooks.json`（Muxy、Tower 等）不进麻薯会话；用户手开的 Codex 不受麻薯 hooks 影响。

## OpenCode 怎么接（不改 `~/.config/opencode`）

- `OPENCODE_CONFIG_DIR=state/opencode-config`：OpenCode 把它当一个额外的配置目录，只多加载 `plugins/mashu.js`（软链接到 `integrations/opencode/mashu.js`）。用户的 `~/.config/opencode/opencode.json` 和插件照常加载、不改。
- 模型和思考档位：`OPENCODE_CONFIG_CONTENT={"agent":{"build":{"model":…,"variant":…}}}`（TUI 没有 `--variant`；只设 variant 不设模型不生效，所以思考程度要选了模型才有用）。
- `XDG_STATE_HOME=state/opencode-state`：OpenCode 的 TUI 把「每个模型上次用的思考档位」、最近模型、输入历史存在 `$XDG_STATE_HOME/opencode/`，启动时这个档位优先于配置。麻薯会话用自己的一份，启动前写好要用的档位；不往用户的 `~/.local/state/opencode` 写。插件的 `shell.env` 把执行者 bash 命令里的 `XDG_STATE_HOME` 还原成用户的。
- 插件做的事：`tool.execute.before` 同步调 `mashu hook-pretool --opencode`，拒绝就抛错拦下；`chat.message` 调 `hook-prompt`（本进程第一轮 / 压缩后带上下文恢复，「继续」带状态摘要，作为一段 synthetic 文本加进这一轮）；主会话 `session.idle` 调 `hook-stop`；子代理的会话不算。
- 工具名：`bash`（`command`）、`edit` / `write` / `multiedit`（`filePath`）、`apply_patch`（`patchText`，拆成每个文件）；MCP 工具叫「服务器_工具」，和项目登记的 `mcp__服务器__工具` 对上就按那个名字查，其余加 `mcp__` 前缀。
- 启动前去掉 `TMUX` / `TMUX_PANE`：用户装的 opentmux 插件在 tmux 里会自己开窗格，会打乱麻薯的窗口布局。其他用户插件（dcp、handoff、agent-memory …）照常加载。
- 会话记录在 OpenCode 自己的库里（`~/.local/share/opencode/`，和用户手开的会话在一起，`opencode` 的会话列表里能看到麻薯开的会话）。

## caesar-docs 分发

麻薯仓库的 `skills/caesar-docs` 是唯一来源。Claude 受管会话用 `--plugin-dir` 加载本地插件，并保留麻薯知识地图的 `--add-dir`。Codex 从各账号的 `CODEX_HOME/skills` 读取。agy 从 `~/.gemini/skills` 读取。OpenCode 从用户级、受管配置的 `skills/` 或兼容目录读取。受管会话启动时列出项目现有的职能索引路径。

执行 `mashu skills sync` 可预览同步。用户级位置在 `--apply` 时用软链接替换，原件保存在 `state/backup/caesar-docs-*`。工程副本由各自的 `.agents/skills-lock.json` 跟踪，检测到本地修改时保留原样。其余 Caesar skill 由 Caesar 游戏工作区维护。每日自迭代汇总版本漂移、阅读、校验和写入观测。

## 已知限制

- Codex 走订阅，没在 `prices` 里配价格就不算钱；OpenCode 的花费用它自己按 models.dev 价格算的累计值。
- Codex、OpenCode 不回报思考档位：麻薯记下启动时实际传的那档。
- 安全拦截和 Claude 一样是护栏，不是安全边界（hook 出错时放行；正则 / 结构判定见 `risky`）。CLI 换了 hook 输入格式就可能失效，所以版本不在已验证范围内不让选。
- `--dangerously-bypass-hook-trust` 对 Codex 是整次会话生效：工程里如果有 `.codex/hooks.json`，它也会不经确认就运行。

## 额度与花费来源（#3 3-quota-all）

网页左下角纯数字展示各家额度与花费：

- **Claude**：来自受管会话状态栏回报的 `rate_limits`，写入 `state/ratelimits.json`；包含 5 小时（`five_hour`）与本周（`seven_day`）的百分比及重置时间。
- **Codex**：来自 rollout 会话记录（`state/codex-home/sessions` 与 `~/.codex/sessions`）中 `token_count` 事件携带的 `rate_limits` 字段（`primary` 为 5 小时 300 分钟窗口，`secondary` 为周 10080 分钟窗口）。只读扫描最新 rollout 文件。
- **OpenCode**：按量付费，从本地 `~/.local/share/opencode/opencode.db` 只读汇总今日（0 点起）与本周（周一 0 点起）的会话花费（`cost`）。
- **Gemini（agy）**：模型池里有本机 Gemini 号时，麻薯优先跑 `agy -p /quota --output-format json`（不耗 token，超时 30 秒），从 `command.data.groups[].buckets[]` 按模型组解析 5 小时与每周两个窗口（1970 重置时间表示未用过，记为空），左下角显示 Gemini 组的「5 小时剩 X% · 周剩 Y%」，模型池卡片按「各组剩余额度」展示各窗口；`agy /quota` 失败或超时时保留上一次真实值并标注时间；旧的 Code Assist `fetchAvailableModels` 对个人号仅返回 100% 占位值，仅作兜底（标 `source: "api_placeholder"`），没有真实值时显示「额度未知」，不显示 100%。
- **模型池**：Codex 显示默认号的数字，多号时名字后面标「N号」，悬停列出所有号；Gemini 只有本机当前登录这一个号。
- **服务端节流与缓存**：`studio.read_limits()` 对各平台只读汇总，内存缓存 60 秒，避免前端轮询频繁扫盘或开库；Claude 收到新状态栏时立即失效缓存。

## 账号池（#4 4-account-pool）

> 网页标签与说明现已统一改称「**模型池**」（#14），账号管理与预设、角色默认同在顶部「模型池」页；`mashu account …` 子命令名保持不变。

Codex 也支持兼容 OpenAI Responses 的 API 号：在模型池点「加 API 号」，输入名称、请求地址和 key，保存前会检查模型列表。命令行可用 `mashu account add codex --api --name 名称 --base-url 地址`，从标准输入读取 key，或用 `--key-file` 指定权限为 0600 的文件。API 号按量计费，不参与自动额度比较；手动指定或设为默认号才使用。密钥存于主配置和该号的运行时登录文件，不带密钥导出时会省略。旧的 `providers.zhaolaoban` 在服务启动时备份主配置后迁入模型池。

麻薯自己管多个 Codex 账号：加号、续期、按号额度、开会话自动挑号或手动指定。Gemini（agy）只管本机当前登录的**一个号**：续期 + 额度。Claude、OpenCode 不在范围内（「模型池」标签同时管理预设、角色默认以及各家的登录和额度 / 花费）。代码：`mashu/accounts.py`（纯逻辑，网络走可替换的 `HTTP`）+ `studio.py`；网页在顶部「模型池」标签；命令 `mashu preset list` / `mashu account …`。

> **Gemini 为什么不做多号 / 切号**（用户 2026-11-26 定，#156 / #157）：Google 的 Antigravity 条款第 6 条把「用第三方软件、工具接入」算违约，可停用 Antigravity / Gemini CLI 账号；2026-02 起一直在封走第三方工具（OpenClaw、OpenCode、CLIProxyAPI 等）用 Antigravity 登录的号，11 月又有一波（403 `TOS_VIOLATION`，有报告整个 Google 账号被停、申诉只有一次机会），第三方整理还点名「多账号自动轮换」「把 agy -p 包在第三方 agent 里」。所以 Gemini 的加第二个号、自动挑号、全局切号入口都关了（`account_action` 直接拒绝，网页 / 命令行没有入口；`accounts.Gemini` 里的导入 RT、网页授权、写钥匙串的代码留着但调不到）。要换 Gemini 号：用户自己在终端运行 agy 登录，麻薯体检时发现钥匙串换了号就跟上（`gemini_sync_local`，旧号记录删掉）。要让第三方工具用 Gemini，走 AI Studio / Vertex 的 API key。

### 存放（#7 合并进主文件 `state/studio.json`，目录 0700、文件 0600）

| 路径 | 内容 |
| --- | --- |
| `state/studio.json` | 唯一主配置文件（0600，文件锁原子写）：`settings`、`defaults`、`accounts`（账号元数据 + Gemini RT/AT + 非本机 Codex `auth.json` + Gemini OAuth 客户端缓存）、`providers`、`web.token` |
| `state/accounts/codex/<号>/` | 运行时 `CODEX_HOME`：`config.toml` 和 hooks 与 `state/codex-home` 同样生成；`auth.json` 开会话前由主文件生成，续期后的新 RT 同步回主文件 |
| `state/backup/` | 迁移前旧文件（`token`、`accounts/index.json`、`accounts/gemini/*/cred.json`、`clients.json`）与导入前 `studio.json` 的备份 |

- 本机默认号：Codex 的「本机」号**不复制**，就是 `state/codex-home`（`auth.json` 软链接到 `~/.codex/auth.json`），由 Codex 自己续期；Gemini 的「本机」号是钥匙串里那个 RT 的副本（Google 的 RT 续期不换新，两边都能用）。
- 第一次往池里加号时，先把本机当前登录登记进来（`ensure_local_account`），不然自动选号用不到它。
- 令牌只在 `auth.json` / `cred.json` / 钥匙串里；网页、接口、日志、报错一律经 `mask()`（前 4 位 + …）/ `redact()`。

### 加号

| | 命令行 | 网页（「模型池」标签） |
| --- | --- | --- |
| Codex | `mashu account add codex [--device]`（前台跑 `CODEX_HOME=<号目录> codex login`） | 「模型池」标签：「加号（设备码）」显示 `https://auth.openai.com/codex/device` + 一次性码（点了才生成，15 分钟有效）；「加号（本机浏览器）」在本机浏览器打开登录页。后台任务读 `codex login` 的输出，完成后自动登记 |
| Gemini | 不能加号（#157） | 只有「登记 / 同步本机 agy 登录」 |
| 本机 | `mashu account import-local codex|gemini` | 「登记本机登录」/「登记 / 同步本机 agy 登录」 |

- Gemini 登记本机登录时先用钥匙串里的 RT 换一次 access_token 校验、取邮箱、查额度。RT 必须是 Antigravity 的 OAuth 客户端签发的（agy 二进制里有三组候选，实测本机 RT 只有其中一组认）；Gemini CLI 的 `~/.gemini/oauth_creds.json` 是另一个客户端，**待确认**，大概率不行。
- Codex 同一个号（邮箱 + 账户 ID 都一样）再登录一次会被拒（池里已有）。

### 续期与体检（调度器每 60 秒起一轮后台体检，各号再按 10 / 30 分钟节流；网页「体检」立即做）

- Gemini：access_token 离过期不到 5 分钟就用 RT 续期（加锁、原子写），Google 回了新 RT 就换上；`invalid_grant` 标「已撤销」，其它失败标「续期失败」。体检顺便查额度。
- Codex：读各号 `auth.json` 的登录状态、邮箱、套餐。池里麻薯自己持有的号超过 7 天没续期、又没有会话在用，就用 RT 调 `auth.openai.com/oauth/token` 续一次（写回 `auth.json`，在 `<号目录>/auth.lock` 上加锁）；有会话在用时交给 Codex 自己续。**本机默认号麻薯不续**。
- **RT 轮换的坑**：OpenAI 的 RT 每次续期都会换新、旧的作废，同一个号的 RT 只能有一个持有者。所以本机号只软链接不复制；池里的号只放在麻薯自己的号目录里，别再拷给别的工具用。

### 选号与切号（只在新会话开工时选，会话中途不换号）

- 优先级（Codex）：对话 ⚙ 里指定的号（`pref:<频道>` 的 `xacct`）> 功能开工时定的（`state.json` 的 `exec.account`，开工卡上的「账号」下拉；`mashu new-feature … --cli codex --cli-account cx-2`）> 自动。
- 自动（`accounts.pick`，只用于 Codex）：状态正常、没禁用的号里额度用得最少的（取 5 小时和本周里大的那个；未知按 0），用满（≥100%）的排最后，一样多时默认号优先。指定的号不能用（禁用 / 撤销 / 被删）就改为自动并记日志。池里这个平台没有号就按以前的单号行为。
- Codex：会话的 `CODEX_HOME=<该号目录>`，每个号互相隔离，几个执行者可以同时用不同的号。rollout、额度、恢复会话都在所有号目录里找（`codex_homes()` / `plat.find_codex_session`）；恢复会话回到 rollout 所在的那个号目录。
- Gemini：agy 全机只认钥匙串（服务 `gemini`、账户 `antigravity`，值 `go-keyring-base64:` + base64 JSON `{token{access_token,token_type,refresh_token,expiry,expires_in},auth_method,id_token_email}`）里的一个登录，麻薯**不切号**（#157）：Gemini 会话一律用钥匙串当前的号，只记下 `camacct:<机位>`。（曾实现过「没有 agy 在跑时写钥匙串全局切号」，代码 `Gemini.activate` / `gemini_switch_for` 留着但不再调用。）
- **`JETSKI_OAUTH_TOKEN` 不能按会话注入账号**（agy 1.5.15 实测 2026-11-26）：设成一个无效的 access token 跑 `agy -p`，结果和不设完全一样（照常走钥匙串里的登录），说明这个变量不会覆盖钥匙串，所以 Gemini 没法像 Codex 那样按会话隔离。
- 每次开会话记 `camacct:<机位>`；网页 ⚙ 悬停显示现在的会话用的是哪个号；正在被会话用的号不能删。

### 额度

- Codex：每个号自己目录里 rollout 的 `rate_limits`（本机号再加 `~/.codex/sessions`），用这个号跑过会话才有。
- Gemini：优先跑本机 `agy -p /quota --output-format json`（不耗 token，超时 30 秒，仅对本机 agy 当前登录号），按模型组解析 5 小时与每周两个窗口并记录 `source: "agy_cli"` 与 `groups`（`1970-01-01` 重置时间记为空，`remaining` 取所有窗口最低值供备选预设判断）；失败或超时时保留上一次真实额度并标出更新时间；旧的 `POST cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels` 对个人号返回 100% 占位值，仅作最后兜底并标 `source: "api_placeholder"`，前端与 CLI 显示「额度未知」。

### 回滚

- 仅停用多账号：在主文件里清空 `accounts.items` 或用 `mashu account remove` 删除号后，Codex 会话退回 `state/codex-home`（软链接 `~/.codex/auth.json` 不变），Gemini 用钥匙串里当前的登录。
- 整套主配置改动回滚见下节「主配置文件与导出导入」的回滚说明。

### 风险

多号轮换躲额度可能违反 OpenAI / Google 的服务条款，有封号风险，号由用户自己提供、自己承担。Antigravity 的 OAuth 客户端是从 agy 二进制里扫出来的公开桌面客户端，Google 改了会跟着 agy 升级自动重扫；扫不到能用的就退回「先用 agy 自己登录，再导入本机」。

## 主配置文件与导出导入（#7 7-one-config-file）

### 1. 主文件结构（`state/studio.json`，0600）

- `settings`：全局设置（并发槽 `stages` = 每个账号同时跑的执行者上限、备选切换阈值 `preset_fallback_pct` 默认 95、扫描根目录、端口、价格 `prices`、平台版本 `platform_versions` 等 `DEFAULTS` 键）。
- `presets` / `role_presets`：模型池预设列表及各角色默认预设（`schema_version: 2`；首次迁移自动把旧 `defaults` 转成预设并备份到 `state/backup/model-pool-*`，旧 `defaults` 保留只读供回滚）。
- `accounts`：模型池默认号（`defaults`）、账号清单及凭据（`items`：Gemini 的 RT/AT/过期时间，非本机 Codex 的 `auth.json` 内容；本机 Codex 号只记 `auth_ref: "local_cli"`，不复制 RT）、Gemini OAuth 客户端缓存（`gemini_clients`）。
- `providers`：本机 CLI 引用标记与自定义 provider API key。
- `web`：网页接口口令 `token`（原 `state/token`）。
- **并发安全**：所有写入经过 `config_file.config_lock`（线程 `RLock` + 进程 `fcntl.flock`）和 `write_private`（同目录临时文件 `0600` + `fsync` + `os.replace`）。
- **首次迁移**：`server.py` 启动、`mashu init`、访问模型池或 `mashu config migrate` 时自动执行（幂等）：把旧扁平 `studio.json`、`state/token`、`state/accounts/index.json`、`state/accounts/gemini/*/cred.json`、`clients.json` 合并进 `state/studio.json`，旧文件备份到 `state/backup/one-config-*` 后移除；v1 → v2 升级自动将旧 `defaults` 迁移成 `presets` 和 `role_presets`（备份到 `state/backup/model-pool-*`）。

### 2. 哪些东西不在主文件里

- **本机 CLI 自己的登录存储**不搬家，主文件只记「来源：本机 CLI」：`~/.codex/auth.json`、macOS 钥匙串里的 Claude Code（`Claude Code-credentials`）和 agy（`gemini` / `antigravity`）、OpenCode 的 `~/.local/share/opencode/auth.json`。只在「带密钥导出」时把它们打进加密包，导入时再写回对应位置。
- **运行时状态**留在 `state/studio.db`：每个对话 / 功能单独选的平台 / 模型 / 思考 / 账号（`meta` 表 `pref:*`）、项目与功能列表、消息、上报、用量统计。

### 3. 导出与导入

- **命令行**（口令走交互输入，不进命令行参数和日志）：
  - 不带密钥导出（默认，可发同事）：`mashu config export --out config-public.json` — key / token / RT / 网页口令全部替换为「已省略」，账号仅保留平台、标签、邮箱。
  - 带密钥导出（换电脑用）：`mashu config export --out config-secret.json --with-secrets` — 交互输入口令，整包加密（`0600`）。
  - 导入预览（不写入）：`mashu config import config.json --dry-run`
  - 导入写入：`mashu config import config.json`（显示差异后确认；写入前自动备份当前 `state/studio.json` 到 `state/backup/pre-import-*`；「已省略」项不覆盖本机已有值）。
- **网页**：顶部「会话监控」页右上角提供「导出配置」（勾选「带密钥」）和「导入配置」（选文件后先预览差异再确认）。
- **RT 轮换与防冲突**：Codex / Gemini 的 refresh_token 会轮换，带密钥导入新电脑后，旧电脑上的同号要停用，否则两边会互相顶掉登录；因此导入的账号默认标为「已禁用」，在新电脑确认旧电脑已停用后再手动启用。
- **加密格式与离线解密**：纯 Python 标准库实现（`PBKDF2-HMAC-SHA256` 200,000 轮派生 64 字节密钥，前 32 字节用于 `SHA256-CTR` 解密 zlib 压缩明文，后 32 字节用于 `HMAC-SHA256` 校验 `b'mashu-cfg-v1' + salt + nonce + ciphertext`）。任意装有 Python 3 的电脑即可不依赖第三方库解密查看：
  ```python
  import base64, getpass, hashlib, hmac, json, sys, zlib
  b = json.load(open(sys.argv[1]))
  pw = getpass.getpass('口令: ').encode()
  dk = hashlib.pbkdf2_hmac('sha256', pw, base64.b64decode(b['salt']), b['iterations'], 64)
  salt, nonce, tag, ct = (base64.b64decode(b[k]) for k in ('salt', 'nonce', 'tag', 'ciphertext'))
  assert hmac.compare_digest(tag, hmac.new(dk[32:], b'mashu-cfg-v1' + salt + nonce + ct, 'sha256').digest())
  ks = b''.join(hashlib.sha256(dk[:32] + nonce + i.to_bytes(8, 'big')).digest() for i in range((len(ct) + 31) // 32))
  print(zlib.decompress(bytes(x ^ y for x, y in zip(ct, ks))).decode())
  ```

### 4. 回滚办法

1. 代码回滚：`git revert <提交号>`（或在分支上回退 `config_file.py`、`accounts.py`、`studio.py`、`server.py`、`web/` 等改动）。
2. 数据恢复：从 `state/backup/one-config-<时间>/` 把备份的 `studio.json`、`token`、`accounts/index.json`、`accounts/gemini/*/cred.json`、`accounts/gemini/clients.json` 拷回 `state/` 对应路径（保持 `0600` 权限）；若只是刚做完一次误导入，从 `state/backup/pre-import-<时间>/studio.json` 覆盖回 `state/studio.json`。
3. 点击网页右上角「重启服务」使服务端进程加载回滚后的代码与配置。

## 稳定运行快照（stable-runtime，#17）

运行中的麻薯（CLI、hooks、调度器、网页服务、提示词、`skills/caesar-docs`）默认运行在 `state/runtime/` 下的只读 git worktree 快照（detached HEAD），与工作区隔离；执行者在工作区修改、未提交或测试未通过的代码不会影响线上会话与服务。

- **代码 vs 内容分离**：
  - **代码**读快照（`CODE` = `state/runtime/`）：`mashu/**`（含 `mashu/prompts/**`）、`web/`、`skills/caesar-docs`、`integrations/`。改动需提交并发布才生效。
  - **内容**仍读工作区（`MASHU_CONTENT` 默认指向工作区根目录）：`docs/**`（含 `docs/memory/**` 守则与偏好）、`playbooks/**`，以及各项目的 `.mashu/**`。修改后立即对新会话生效，无需发布。
  - **状态**显式指向工作区下的真实 `state/`（`MASHU_HOME`），快照内无独立 `state/`。
- **入口分流**：
  - `bin/mashu`（工作区）：当未设 `MASHU_DEV=1` 且 `state/runtime/mashu/studio.py` 存在并支持 `MASHU_CONTENT` 时，自动 `exec` 快照里的 `studio.py` 并传入 `MASHU_HOME`、`MASHU_CONTENT`、`MASHU_MEMORY`；否则（快照不存在或快照版本过旧）回退运行工作区代码，确保 `docs` 仍从工作区读且 `mashu release` 可用。已有会话的 `PATH`、hooks 和提示词里的绝对路径无需修改，切换后自动走快照。
  - `bin/mashu-dev`：固定设置 `MASHU_DEV=1` 运行当前工作区代码，供开发手测使用；手测时须配合独立的临时 `MASHU_HOME`（如 `MASHU_HOME=/tmp/mashu-dev bin/mashu-dev status`），不要碰正式 `state/`。
  - `start.sh`：存在支持 `MASHU_CONTENT` 与 `release_runtime` 的快照时，启动快照里的 `mashu/server.py`。在 `./start.sh restart` 模式下，若检测到 `state/runtime/` 是不支持 `MASHU_CONTENT` 或 `release_runtime` 的旧快照，会先调用工作区 `studio.py release --no-restart` 自动从 `HEAD` 校验并升级快照，再重启服务（检查失败则在停服前中止）；也可直接由主脑/用户执行 `bin/mashu-dev release` 完成旧快照升级。
- **发布即重启**：
  - `mashu release [--sha <提交>] [--dry-run] [--no-restart] [--json]`：默认发布工作区 `HEAD`（只发布已提交内容，忽略未提交改动）。先在临时 worktree 里对目标提交跑 `.mashu/check.sh`（单测 + docs 校验），失败则拒绝发布并返回 `code=check_failed`、线上 sha 不变；通过后将 `state/runtime/` 切到目标 sha、原子写入 `state/runtime.json`（记 `sha`、`released_at`、`released_by`、`prev_sha`），随后调用 `start.sh restart` 重启网页服务与调度器（加 `--no-restart` 时仅更新快照不重启）。
  - `mashu release --rollback`：切回 `state/runtime.json` 中的 `prev_sha` 并重启，跳过检查，用于救火。
  - `mashu release --same` / `mashu restart-server --same --confirm`：不换版本，仅重启服务。
  - 权限与 `restart-server` 一致：仅主脑和终端用户可用，子脑和执行者不可发布；网页右上角显示「线上版本 `<短 sha>` · 工作区已领先 N 个提交」与「有 N 个提交待发布」按钮，点击等价于发布并重启，失败时弹窗展示完整失败原因且不卡住。
- **回滚本特性**：`git revert` 对应提交后删除 `state/runtime/`，`bin/mashu` 与 `start.sh` 在快照不存在时自动回到旧行为。

## 验证记录（2026-11-24）

- 单测：`tests/test_platforms.py`（格式转换、拦截、会话 ID / 用量 / 回复、独立 CODEX_HOME 不改用户目录、OpenCode 启动参数、未验证版本不能选、开工卡配置）。
- 真实会话（隔离的麻薯实例：临时 `MASHU_HOME`、独立 tmux socket、临时工程）：Codex 0.166.0（gpt-6-luna）、OpenCode 1.18.31（opencode/deepseek-v4.1-flash-free，免费）各一个执行者：启动、收消息、`mashu feature set` / `finish`、上下文和 token 有数据；`git stash`、改 state.json、`git push origin main`、`git push -f` 四项都被拦下，普通新建文件放行；杀掉窗格后发消息，用原会话 ID 恢复且记得上文。开工卡上把 Codex 改成 OpenCode 再批准，执行者按 OpenCode + 所选模型 + 思考档位启动。前后对比 `~/.codex/config.toml`、`hooks.json`、`~/.config/opencode/*` 哈希不变。

## 相关节点

[命令参考](commands.md) · [知识地图](../indexes/docs.md)
