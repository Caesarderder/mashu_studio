# 麻薯 Studio（mashu_studio）

**一个管理电脑的 agent team。** 你在网页里跟「麻薯」聊天，她带着一队 Claude Code 会话，管这台电脑上所有工程的开发：登记项目、拆功能、派执行者、盯进度、拦危险操作、写简报、复盘、自我迭代。

> English: *Mashu Studio is a local, chat-driven agent team for your computer. One lead agent ("Mashu") manages per-project leads and per-feature executor sessions (Claude Code, optionally Gemini via Antigravity CLI) running in tmux, with a local web dashboard, approval gates, handoffs at 75% context, and a file-based memory you customize. macOS + tmux + claude CLI + python3 (stdlib only).*

## 它是什么

- **麻薯**：所有工程的负责人，一切由她主导完成。三级负责人：
  - **主脑**（CEO）：权限最高，可以直接做任何事。管跨项目的优先级、并发名额、登记项目、工作流模板、简报和自我迭代，在主对话里和你沟通。
  - **子脑**（项目负责人）：每个项目一个，也是麻薯，统筹这个项目的所有功能，可以直接动手，在这个项目的对话里和你沟通。有活时启动，空闲就停。

  主脑和子脑共用同一份**记忆**和守则。三根支柱：**全流程**（每个项目从登记到上线、复盘都有人推进）、**自迭代**（复盘后优化工作流和守则）、**上下文**（一切落盘，换哪个会话都能接上）。
- **执行者**（本会话负责人）：每个功能一个，从头做到尾（分阶段的功能也不换人），会话内的技术决定自己拍板。上下文到 75% 时写 `handoff.md`，同一个身份在同一个窗格换新会话接着做。
- **你**：麻薯的助手。平时看主对话里的进度和提醒，在麻薯需要真人帮忙时出手。

一个工程是一个**项目**，项目里的一个需求是一个**功能**。执行者直接在工程文件夹里开发，不建分支。

每个功能按**工作流**推进：工作流规定有哪些阶段、每个阶段由谁做、做什么、进入下一阶段要谁批准。模板在 `playbooks/`（直接做、快速三阶段、标准六阶段）；每个项目有自己的一份，可以迭代，有版本，能回滚。

每个项目有自己的**资料库**，放在工程里的 `.mashu/` 目录：项目说明、工作流、项目规则、项目笔记、自动检查脚本，以及每个功能的状态文件、需求、设计、验收标准和测试结果。

## 安装

需要：macOS、[tmux](https://github.com/tmux/tmux)、[Claude Code](https://docs.claude.com/en/docs/claude-code)（`claude` 命令，先登录一次）、python3 ≥ 3.9（只用标准库，不用装依赖）。可选：Antigravity CLI（`agy`，给会话用 Gemini）。

```bash
git clone https://github.com/Caesarderder/mashu_studio.git
cd mashu_studio
./bin/mashu init                          # 检查依赖，建 state/ 和 state/studio.json；可加 --scan-root ~/code
./start.sh                                # 启动并打开 http://127.0.0.1:8770/
```

想在任意终端里直接敲 `mashu`，把 `bin/` 加进 PATH（受管会话会自动加）。

## 定制你自己的麻薯

仓库里是一个「出厂设置」的麻薯：有完整的流程、手册和守则，但没有任何人的记忆和项目。拉下来之后按需改：

| 想改什么 | 改哪里 |
| --- | --- |
| 她知道的关于你的事、你的偏好 | `docs/memory/用户.md` |
| 全局方针、项目优先级、并发怎么分 | `docs/memory/方针.md` |
| 她的工作守则（会拼进主脑、子脑的系统提示） | `docs/memory/守则.md` |
| 主脑 / 子脑 / 执行者的身份与入口 | `mashu/prompts/*.md` |
| 手册、命令参考、上报与权限流程 | `docs/indexes/docs.md` 开始的知识地图 |
| 工作流模板 | `playbooks/*.json`（项目专用的放在工程里的 `.mashu/playbooks/`） |
| 并发、模型、哪些操作要你审批、扫描哪些目录 | `state/studio.json`（字段和默认值见 `mashu/studio.py` 的 `DEFAULTS`，示例 `studio.json.example`） |
| 头像 | 替换 `web/mashu.jpg`（换成别的格式的话同时改 `web/app.js` 的 `AVATAR` 和 `web/index.html` 的 icon） |
| 知识地图用的 docs skill | `skills/caesar-docs/`；换成别的 skill 就改 `studio.json` 的 `docs_skill`，设为 `""` 不加载 |

记忆会随使用增长：麻薯会自己往 `docs/memory/` 里记你的偏好、复盘和教训。这些是你的私人数据，如果你 fork 后公开仓库，记得别把它们推上去。

## 麻薯主导，harness 不卡死

- 执行者的上报先交给子脑。技术取舍、方案定稿、各阶段批准、进测试环境、发布计划、生产操作、推送受保护分支、合并，都由麻薯自己拍板，决定记进**决策记录**，你可以事后追认或推翻。
- **只有不可逆操作**（删库、删工程目录、强推覆盖历史等）会拦下来等你审批。审批卡出现在对应项目的对话里，麻薯只能附意见，不能代批。
- 麻薯在需要真人的时候找你：登录账号、提供密钥或付款、线下操作，或者她判断不了的产品方向。
- 自动检查（`check.sh`）、验收标准、未提交改动、改动范围冲突，这些都**只提醒、不打回**：问题会交给子脑，由她判断要不要返工。
- 想要回到严格模式，在 `state/studio.json` 里设 `"strict_checks": true` 和 `"user_gates": ["prod", "irreversible", "preview", "release-plan", "budget", "stage-entry"]`（`stage-entry` 指工作流里标为用户批准的阶段进入）。

**注意**：所有会话都以你的身份运行，默认用 `bypassPermissions` 权限模式（`studio.json` 的 `permission_mode` 可改）。闸门拦截只是防误用的护栏，不是安全边界。只在你信任的电脑上用，网页服务只监听 127.0.0.1，并校验每次请求的令牌。

## 启动与运维

```bash
./start.sh            # 启动并打开 http://127.0.0.1:8770/
./start.sh restart    # 改了 Python 代码后只重启网页服务，所有会话不受影响
./start.sh stop       # 停掉全部（包括所有 Claude 会话）；自动启动也会暂停，直到下次手动 ./start.sh
./start.sh autostart on|off   # 开机（登录后）自动启动，每分钟检查一次，网页服务挂了自动拉起；日志在 state/autostart.log
tmux attach -t mashu-studio    # 看 / 接管所有会话：desk = 主脑，film-<id> = 每个项目的子脑和执行者
```

端口和 tmux 会话名可用环境变量 `MASHU_PORT`、`MASHU_TMUX` 改。

## 用法

Codex 账号池支持兼容 OpenAI Responses 的 API 号：在「账号池」选择「加 API 号」，填写名称、请求地址和密钥并通过连接测试。API 号按量计费，只有手动选中或设为默认号时使用；密钥不会出现在普通配置导出中。

在主对话里直接跟麻薯说，例如：

- “把 ~/workspace/my-app 登记成项目，加一个导出 CSV 的功能”
- “今天怎么样” / “my-app #2 先停” / “这周用了多少”

网页上还有这些：

- **左侧栏**：顶部是全局状态（待审批 / 待回答 / 忙碌 / 空闲），点一格只看这一类。下面是麻薯：主脑（主对话）和每个项目的子脑；再往下是项目列表，每个功能右边标着执行状态。
- **主对话**：各项目的阶段完成、功能完成、会话意外退出、等权限确认，会以一行动态的形式出现在这里（只给你看，不打扰麻薯）。
- **看板**：每个功能在哪个阶段。
- **会话监控**：所有会话的实时终端画面。
- **决策记录**：追认或推翻麻薯（主脑、子脑）自己拍板的事。
- **资料库**：麻薯的记忆、工作流模板、每个项目的资料库。
- **自迭代**：每天用便宜模型总结一次，提出守则 / 工作流的改进，主脑处理。
- **用量**：token 和费用。

左上的“＋”和“未登记的文件夹”可以直接登记项目；项目里可以“＋ 新功能”。

可选集成：`integrations/weixin/`（微信 ClawBot 桥接，见其中 README）。

## 配置

- `state/studio.json`：全局配置，例如执行者并发槽、子脑并发数、每个项目同时跑几个功能、扫描哪些目录、各角色默认模型、模型单价、`user_gates`、`strict_checks`。字段说明见 `mashu/studio.py` 的 `DEFAULTS`。
- `state/films/<id>/film.json`：每个项目的配置，例如受保护分支、额外的危险命令规则、项目规则。

`state/` 是运行数据（数据库、令牌、日志、会话记录），不进 git。

## 开发

```bash
python3 -m unittest discover -s tests -v     # 用假 claude + 独立 tmux 服务器，不碰你的 tmux
```

| 路径 | 内容 |
| --- | --- |
| `mashu/studio.py` | 核心：数据库、tmux、上报链、调度、hooks、CLI |
| `mashu/server.py` | 网页服务、SSE、调度器线程 |
| `mashu/selfiter.py` | 每日自迭代 |
| `mashu/prompts/` | 主脑 / 子脑 / 执行者的系统提示（只放身份 + 入口路由） |
| `playbooks/` | 工作流模板（JSON） |
| `docs/indexes/docs.md` | 麻薯的知识地图入口：角色手册、命令参考、上报与权限、上下文交接（caesar-docs 格式） |
| `docs/memory/` | 麻薯的记忆（index、用户、方针、在办、复盘、守则；docs 节点） |
| `skills/caesar-docs/` | 受管会话加载的知识地图 skill |
| `web/` | 前端 |

代码里的一些英文标识（`films`、`sequences`、`cameras`、`producer`、`crew`、`/api/greenlight` 等）是早期命名，为了兼容没有改。

## License

MIT
