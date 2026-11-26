---
km_id: reference.platforms
km_type: reference
domain: mashu
status: active
owner: mashu
last_verified: 2026-11-26
source_of_truth:
  - mashu/studio.py
  - mashu/platforms.py
  - mashu/accounts.py
  - integrations/opencode/mashu.js
validated_by:
  - tests/test_platforms.py
  - tests/test_accounts.py
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

- 网页对话头的 ⚙（平台 · 模型 · 思考）按对话选；「会话监控 → 默认设置」按角色选；`studio.json` 的 `providers` / `models` / `gemini_models` / `codex_models` / `opencode_models` / `efforts`。
- 执行者的优先级：对话单独设 > 功能批准开工时定的 `exec`（state.json）> 角色默认 > Claude。
- 开工审批卡写明「执行者：CLI · 模型 · 思考」，卡上可以改了再批准；`mashu new-feature … --cli codex --model gpt-6-luna --effort high` 预先指定。用户在网页直接点「启动」也按卡上的配置。
- 平台只对新会话生效；恢复会话沿用开会话时那家（`meta` 的 `provider:<会话 ID>`、`camprov:<机位>`）。
- 思考程度共用一组（low / medium / high / xhigh / max），Codex / OpenCode 按模型支持的档位取最接近的（`platforms.nearest_effort`）。
- **能不能选**：Codex、OpenCode 要装了、版本在已验证范围内（`studio.VERIFIED_VERSIONS`：Codex 0.166.x、OpenCode 1.18.x；`studio.json` 的 `platform_versions` 可追加）。不满足的在网页上灰掉并写原因，`set_pref` / 默认设置 / 开工卡都拒绝；偏好里写着但现在不能用的，新会话退回 Claude 并提示。CLI 升级后先实测安全拦截，再把版本加进去。
- `mashu platforms --check` 看各家状态，并跑一遍安全拦截自检（只验证输入格式转换和判定，不调模型）。

## 对照表

| | Claude Code | Gemini（agy） | Codex | OpenCode |
| --- | --- | --- | --- | --- |
| 启动 | `claude --settings … --append-system-prompt … --session-id <新 ID>` | `agy --settings … -i <首句>` | `codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust -C <目录> --add-dir … -m … -c model_reasoning_effort=… -c developer_instructions=… -- <首句>` | `opencode <目录> --auto --prompt <首句>` |
| 恢复 | `--resume <ID>` | `--conversation <ID>` | `codex resume <ID> …` | `opencode <目录> --auto -s <ID>`，首句由调度器等界面就绪后粘贴（`-s` 时 `--prompt` 不会自动提交） |
| 会话 ID | 麻薯预先指定 | SessionStart hook 回写 | SessionStart / UserPromptSubmit hook 回写 | 插件第一轮回写 |
| 系统提示 | `--append-system-prompt` | SessionStart hook 注入 | `-c developer_instructions=`（每次启动、恢复都传） | 插件 `experimental.chat.system.transform` 追加 `MASHU_SYSTEM_FILE` |
| hooks 在哪 | `--settings state/claude-settings.json`（只对受管会话） | 全局 `~/.gemini/config/hooks.json` 的 `mashu` 项 | 麻薯专用 `CODEX_HOME`（`state/codex-home`）的 config.toml 里内联 `[[hooks.*]]` | 麻薯插件（`OPENCODE_CONFIG_DIR=state/opencode-config` → `plugins/mashu.js`） |
| 会话记录 | `~/.claude/projects/<目录>/<ID>.jsonl` | `~/.gemini/antigravity-cli/brain/<ID>/` | `state/codex-home/sessions/YYYY/MM/DD/rollout-*-<ID>.jsonl` | OpenCode 自己的库 `~/.local/share/opencode/`；麻薯读插件写的 `state/live/<机位>.json` |
| 上下文 / 用量 | 状态栏 + transcript | 状态栏 | rollout 里的 `token_count`（Stop 时 + 长轮每 10 分钟） | 插件：每条助手消息完成调 `statusline --opencode`（占比 = 这条消息 tokens / `opencode models` 的窗口），每轮结束把会话累计 tokens 和花费交给 `hook-stop` |
| 打断 | Esc | Esc | Esc | 连按两次 Esc |

## Codex 怎么接（不改 `~/.codex`）

- **独立 CODEX_HOME**（`platforms.build_codex_home`）：Codex 在完全放开的模式下会把工作目录的信任写进 `$CODEX_HOME/config.toml`（实测 0.166：`-c projects.….trust_level` 也挡不住），所以麻薯会话不用 `~/.codex`：
  - `config.toml`：用户 config.toml 的副本（模型提供方、MCP、默认模型都一样），去掉 `hooks` / `plugins` / `marketplaces` / `projects` 这几节，末尾加麻薯的 hooks；用户的改了就重抄。工作目录的信任由麻薯写进这份副本（不然 TUI 会弹「Trust this folder?」）。
  - `auth.json`、`auth.lock`、`AGENTS.md`：软链接到用户的（登录共用；Codex 刷新令牌时写回同一个文件，锁也共用）。如果某天 `auth.json` 变成了普通文件，麻薯会提醒，删掉后下次启动重新链接。
  - `skills/caesar-docs`：麻薯的 docs skill。
- **hooks**：`SessionStart`（matcher `startup|resume|clear|compact`）、`UserPromptSubmit`、`Stop`、`PreToolUse`（matcher `*`），命令是 `mashu hook-… --codex`。Codex 的 hook 输入输出和 Claude 同格式（`session_id`、`transcript_path`、`tool_input`、`last_assistant_message`；拒绝用 `hookSpecificOutput.permissionDecision=deny`）。内联 hooks 没在 `/hooks` 里点过信任，所以带 `--dangerously-bypass-hook-trust`（它也会放行项目级 `.codex/hooks.json`，麻薯的工程里一般没有）。
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

## 已知限制

- Codex、OpenCode 的网页「详细」模式只显示这一轮的文字和正在用的工具；子代理、后台任务、待办、思考片段只认得 Claude Code 的记录。
- Codex 走订阅，没在 `prices` 里配价格就不算钱；OpenCode 的花费用它自己按 models.dev 价格算的累计值。
- Codex、OpenCode 不回报思考档位：麻薯记下启动时实际传的那档。
- 安全拦截和 Claude 一样是护栏，不是安全边界（hook 出错时放行；正则 / 结构判定见 `risky`）。CLI 换了 hook 输入格式就可能失效，所以版本不在已验证范围内不让选。
- `--dangerously-bypass-hook-trust` 对 Codex 是整次会话生效：工程里如果有 `.codex/hooks.json`，它也会不经确认就运行。

## 额度与花费来源（#3 3-quota-all）

网页左下角纯数字展示各家额度与花费：

- **Claude**：来自受管会话状态栏回报的 `rate_limits`，写入 `state/ratelimits.json`；包含 5 小时（`five_hour`）与本周（`seven_day`）的百分比及重置时间。
- **Codex**：来自 rollout 会话记录（`state/codex-home/sessions` 与 `~/.codex/sessions`）中 `token_count` 事件携带的 `rate_limits` 字段（`primary` 为 5 小时 300 分钟窗口，`secondary` 为周 10080 分钟窗口）。只读扫描最新 rollout 文件。
- **OpenCode**：按量付费，从本地 `~/.local/share/opencode/opencode.db` 只读汇总今日（0 点起）与本周（周一 0 点起）的会话花费（`cost`）。
- **Gemini（agy）**：agy CLI 自己不报额度；账号池里有 Gemini 号时，麻薯用它的 RT 换 access_token 调 Code Assist 的 `fetchAvailableModels`，显示各模型里剩得最少的比例（见下节「账号池」）。没导入号时显示「无额度数据」。
- **账号池**：Codex 显示默认号的数字，多号时名字后面标「N号」，悬停列出所有号；Gemini 只有本机当前登录这一个号。
- **服务端节流与缓存**：`studio.read_limits()` 对各平台只读汇总，内存缓存 60 秒，避免前端轮询频繁扫盘或开库；Claude 收到新状态栏时立即失效缓存。

## 账号池（#4 4-account-pool）

麻薯自己管多个 Codex 账号：加号、续期、按号额度、开会话自动挑号或手动指定。Gemini（agy）只管本机当前登录的**一个号**：续期 + 额度。Claude、OpenCode 不在范围内（「账号池」标签只显示它们的登录和额度 / 花费）。代码：`mashu/accounts.py`（纯逻辑，网络走可替换的 `HTTP`）+ `studio.py` 的「账号池」一节；网页在顶部「账号池」标签（各家账号、额度、加号 / 导入、全部体检）；命令 `mashu account …`。

> **Gemini 为什么不做多号 / 切号**（用户 2026-11-26 定，#156 / #157）：Google 的 Antigravity 条款第 6 条把「用第三方软件、工具接入」算违约，可停用 Antigravity / Gemini CLI 账号；2026-02 起一直在封走第三方工具（OpenClaw、OpenCode、CLIProxyAPI 等）用 Antigravity 登录的号，11 月又有一波（403 `TOS_VIOLATION`，有报告整个 Google 账号被停、申诉只有一次机会），第三方整理还点名「多账号自动轮换」「把 agy -p 包在第三方 agent 里」。所以 Gemini 的加第二个号、自动挑号、全局切号入口都关了（`account_action` 直接拒绝，网页 / 命令行没有入口；`accounts.Gemini` 里的导入 RT、网页授权、写钥匙串的代码留着但调不到）。要换 Gemini 号：用户自己在终端运行 agy 登录，麻薯体检时发现钥匙串换了号就跟上（`gemini_sync_local`，旧号记录删掉）。要让第三方工具用 Gemini，走 AI Studio / Vertex 的 API key。

### 存放（`state/accounts/`，目录 0700、文件 0600）

| 路径 | 内容 |
| --- | --- |
| `index.json` | 元数据：号 ID（`cx-N` / `gm-N`）、平台、邮箱、套餐、来源（`local` 本机 / `login` 登录 / `rt` 导入）、状态（正常 / 等登录 / 未登录 / 续期失败 / 已撤销）、禁用、最近额度、各平台默认号。**不含令牌** |
| `codex/<号>/` | 这个号的完整 CODEX_HOME：config.toml 和 hooks 与 `state/codex-home` 同样生成（`build_codex_home(link_auth=False)`），`auth.json` 是这个号自己的 |
| `gemini/<号>/cred.json` | RT、access_token、过期时间、邮箱、签发它的 OAuth 客户端 |
| `gemini/clients.json` | 从 agy 二进制里扫出来的 Google OAuth 桌面客户端（按二进制路径 + 修改时间缓存，记住上次能用的那组） |

- 本机默认号：Codex 的「本机」号**不复制**，就是 `state/codex-home`（`auth.json` 软链接到 `~/.codex/auth.json`），由 Codex 自己续期；Gemini 的「本机」号是钥匙串里那个 RT 的副本（Google 的 RT 续期不换新，两边都能用）。
- 第一次往池里加号时，先把本机当前登录登记进来（`ensure_local_account`），不然自动选号用不到它。
- 令牌只在 `auth.json` / `cred.json` / 钥匙串里；网页、接口、日志、报错一律经 `mask()`（前 4 位 + …）/ `redact()`。

### 加号

| | 命令行 | 网页（「账号池」标签） |
| --- | --- | --- |
| Codex | `mashu account add codex [--device]`（前台跑 `CODEX_HOME=<号目录> codex login`） | 「账号池」标签：「加号（设备码）」显示 `https://auth.openai.com/codex/device` + 一次性码（点了才生成，15 分钟有效）；「加号（本机浏览器）」在本机浏览器打开登录页。后台任务读 `codex login` 的输出，完成后自动登记 |
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
- Gemini：`POST cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`（Bearer access_token），每个有 `displayName` 的模型的 `quotaInfo.remainingFraction` / `resetTime`；账号不能用 agy 聊天（例如要验证账号）时这个接口照样能查。

### 回滚

删掉 `state/accounts/` 就回到以前的单号行为：Codex 会话用 `state/codex-home`（软链接 `~/.codex/auth.json` 不变），Gemini 用钥匙串里当前的登录（麻薯从不改钥匙串）。在用池里号的 Codex 会话恢复时找不到原号目录，会退回 `state/codex-home`。

### 风险

多号轮换躲额度可能违反 OpenAI / Google 的服务条款，有封号风险，号由用户自己提供、自己承担。Antigravity 的 OAuth 客户端是从 agy 二进制里扫出来的公开桌面客户端，Google 改了会跟着 agy 升级自动重扫；扫不到能用的就退回「先用 agy 自己登录，再导入本机」。

## 验证记录（2026-11-24）

- 单测：`tests/test_platforms.py`（格式转换、拦截、会话 ID / 用量 / 回复、独立 CODEX_HOME 不改用户目录、OpenCode 启动参数、未验证版本不能选、开工卡配置）。
- 真实会话（隔离的麻薯实例：临时 `MASHU_HOME`、独立 tmux socket、临时工程）：Codex 0.166.0（gpt-6-luna）、OpenCode 1.18.31（opencode/deepseek-v4.1-flash-free，免费）各一个执行者：启动、收消息、`mashu feature set` / `finish`、上下文和 token 有数据；`git stash`、改 state.json、`git push origin main`、`git push -f` 四项都被拦下，普通新建文件放行；杀掉窗格后发消息，用原会话 ID 恢复且记得上文。开工卡上把 Codex 改成 OpenCode 再批准，执行者按 OpenCode + 所选模型 + 思考档位启动。前后对比 `~/.codex/config.toml`、`hooks.json`、`~/.config/opencode/*` 哈希不变。

## 相关节点

[命令参考](commands.md) · [知识地图](../indexes/docs.md)
