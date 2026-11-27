---
km_id: workflow.project-assets
km_type: workflow
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
  - reference.role-producer
  - reference.mashu-commands
---

# 项目资料制作规范（docs、工作流、行为模板、笔记）

## 范围与何时读

子脑要给自己的项目建 / 改文档、工作流、行为模板、项目笔记时读。麻薯和各项目的东西**分开放**：项目的数据一律放在那个工程文件夹里，跟着工程走；麻薯的 `mashu_studio/` 只放麻薯自己的和跨项目通用的东西。网页「资料库」也按这个分区显示（麻薯一块，每个项目一块）。

## 放哪里

| 东西 | 只属于一个项目 → 放工程里 | 跨项目通用 → 放麻薯 |
| --- | --- | --- |
| 知识地图 / 文档 | `<工程>/docs/`，入口 `docs/indexes/docs.md` | `mashu_studio/docs/` |
| 工程法典与协作板 | `<工程>/.mashu/codex.md` 由子脑维护共同目标、区域分工和接口。`board.jsonl` 记录项目进展，网页项目频道可展开查看 | `mashu codex`、`mashu sync` |
| 项目说明、规则、检查、热点 | `<工程>/.mashu/README.md`、`rules.md`、`check.sh`、`hotspots.txt` | — |
| 本项目默认工作流 | `<工程>/.mashu/playbook.json` | — |
| 工作流模板（同项目里有几类活） | `<工程>/.mashu/playbooks/<id>.json`（历史版本自动存 `history/`） | `mashu_studio/playbooks/<id>.json`（direct / quick / standard） |
| 行为模板 | `<工程>/.mashu/playbooks/behaviors/<id>.md` + `README.md` 对照表 | `mashu_studio/playbooks/behaviors/`（目前没有） |
| 工作流面板配置 | `<工程>/.mashu/playbooks/dashboards/<工作流id>.json` | `mashu_studio/playbooks/dashboards/<id>.json`（目前有 direct） |
| 按账号分组（一个项目运营几个账号） | `<工程>/.mashu/accounts.json` | — |
| 接外部系统（工单系统等）的调度扩展 | `<工程>/.mashu/adapter.py` + `adapter/`（见下文「项目适配器」） | — |
| 长期记住的事、做过的决定 | `<工程>/.mashu/producer-notes.md` | 麻薯记忆 `docs/memory/`（只放跨项目的用户偏好和守则，主脑维护） |
| 给人看的成品 | `<工程>/.mashu/deliverables/` | — |

判断不了归属就问一句：「换个项目还用得上吗？」用不上就放工程里。

## 谁维护

- 子脑维护本项目的全部资料，包括工作流和行为模板，直接改、直接提交，不用先报主脑。每次改动都有版本号，并记进决策记录，用户推翻就自动回滚。
- 改麻薯通用模板、麻薯记忆、麻薯知识地图：`mashu report` 交给主脑。
- 执行者不改工作流和模板，有建议 `mashu tell` 给子脑。

## 新工程登记（主脑）

1. 建工作区目录、克隆仓库；工作区本身也要记事时 `git init`，用 `.gitignore` 排除各工程仓库。
2. 先把克隆下来缺的东西补齐再判断「能不能跑」：依赖（`npm ci` 等）、不进 git 的工具链产物（如 Builda SDK：`./.builda-agent/builda check` 后 `builda sdk install`）。缺 SDK 时类型检查会报找不到模块，不是代码坏了。
3. 留意会自己改跟踪文件的工具：`builda check` 会把项目里的 Builda CLI 升到最新版，克隆后立刻出现 `.builda-agent/` 改动。这类改动单独提交或请用户定，不混进功能提交；SDK 版本是锁定的，只在用户要求时升级。
4. `mashu add-project <路径> --title …`，再补 `.mashu/README.md`、`rules.md`、`check.sh`（工程自检，失败行带 ERROR）、`hotspots.txt`，跑一遍 `check.sh`。登记时会同步已存在的 caesar-docs 副本。带 check 的阶段另有基线校验，项目脚本可在 `MASHU_DOCS_MANAGED=1` 时跳过重复的 docs 校验。
5. 用 caesar-docs 建或接上知识地图；多工程工作区用两级地图：工作区只放工程目录和跨工程约束，各工程保留自己的地图和六技能副本。
6. `mashu decide` 记一笔，进度写进项目的 `producer-notes.md`。

依据：2026-11-26 建 caesar_games（克隆 caesar_engine 后 tsc 报找不到 builda-sdk；builda check 把 CLI 从 0.4.156 升到 0.4.171）。

## 项目文档（docs/）

- 用 caesar-docs skill 初始化和维护（受管会话已加载）：入口 `docs/indexes/docs.md`；节点带 frontmatter（`km_id`、`km_type`、`status`、`last_verified`…）。改完跑 `python3 <skill>/scripts/caesar_docs.py validate`。
- 入口按「按角色 / 按任务」路由到节点，一页能看完；细节放节点，不往入口追加日志。
- 项目有了 `docs/indexes/docs.md`，它就自动成为子脑和执行者的项目入口；没有时用 `.mashu/README.md`。

## 工作流模板（.mashu/playbooks/&lt;id&gt;.json）

```json
{
  "id": "xhs-post", "name": "小红书·图文帖子制作", "version": 1,
  "summary": "一句话：用在什么活上、阶段顺序",
  "stages": [
    { "id": 1, "name": "素材与调研", "plain": "一句人话", "role": "调研", "entry": null,
      "behavior": "xhs-researcher",
      "do": "行为模板：.mashu/playbooks/behaviors/xhs-researcher.md——开工先读…（之后写这一阶段具体做什么）",
      "exit": "退出条件：产出了什么 → mashu feature set 1 已完成 → handoff → finish",
      "writes": true, "check": false, "require_ac": false }
  ],
  "changelog": [{ "version": 1, "ts": "2026-11-16", "by": "子脑（项目名）", "note": "初版：依据…" }]
}
```

- `stages[].id` 从 1 连续编号；每个阶段必须有 `name / plain / role / do / exit`；阶段 1 不设 `entry`。`entry` 填 `producer / assistant / executive` 表示进这个阶段要谁批。
- `behavior` 写行为模板 id（一个或列表），网页据此显示模板链接。`do` 的第一句写模板的**相对路径**（相对工程根目录，执行者就在工程根目录里干活）。
- 执行者任何阶段都能写验收标准（`mashu ac add`）；`require_ac: true` 表示进下一步前验收标准要全部通过。
- 只有一个执行者从头做到尾的活，不用建模板，默认 `direct` 就够了。
- 命令：
  - 校验：`mashu playbook check template:<项目id>/<id>`
  - 提交改动：`mashu playbook commit template:<项目id>/<id> --note "改了什么、为什么"`（版本号 +1、存历史、记决策）
  - 设成项目默认：`mashu playbook apply film:<项目id> --template <id> --note "…"`
  - 按某个模板开功能：`mashu new-feature <项目id> <slug> --playbook <id> …`（先找本项目的，再找通用的）
  - `mashu playbook list` 全部列出
- 新建模板：把 JSON 写进 `.mashu/playbooks/`，跑 `check`，再 `commit` 一次留下 v1 历史。

## 行为模板（.mashu/playbooks/behaviors/&lt;id&gt;.md）

工作流管「先做什么、后做什么」，行为模板管「这一阶段的人怎么干」。一个行为一份；几条工作流里出现同一个行为就共用同一份，改一处全生效。项目里的硬性规则（标准、格式）留在 `docs/`，模板只引用、不抄。

骨架（标题格式固定，网页从第一行取名字）：

```markdown
# 行为模板：<角色中文名>（<id>）

**用在**：<工作流> <阶段号>「<阶段名>」、…
**一句话**：这个角色要交出什么、好坏标准。

## 开工先读
## 做法（按顺序）
## 产出骨架
## 自检
## 不做
## 交接写什么
## 改动记录
- <日期>：建立 / 改了什么。依据：<哪次实作、用户哪句话>。
```

- `README.md` 是对照表：「工作流 · 阶段 → 模板」一张表，增删模板时同步改。
- 写的依据要来自真实做过的活（哪次功能、用户原话），不凭空写规矩。
- 模板里的路径一律相对工程根目录。

## 工作流面板配置（.mashu/playbooks/dashboards/&lt;工作流id&gt;.json）

网页项目页顶部「对话 / 工作流」切到「工作流」：每个工作流一个标签（项目默认工作流、项目 `.mashu/playbooks/` 里的模板、功能用过的模板都列出来，没功能的是空看板）。每个标签都有通用看板：概况（进行中 / 等你处理 / 阻塞 / 本周完成）+ 阶段流水线（列 = 阶段，最后一列「已完成」，列头写角色、要不要确认才进），卡片 = 一个功能，点开进功能对话，已归档的收在底下。

工作流自己想在卡片和概况上多显示什么，在模板旁边的 `dashboards/` 子目录写一份 `<工作流id>.json`（不和模板放同一层，免得被当成模板）。项目的先找，找不到再用麻薯通用的。只读、声明式，不写代码；写错或文件不在只让那一项空着，配置不是合法 JSON 时那个标签顶上提示一行。数据由 `mashu/dashboard.py` 算，接口 `GET /api/dashboard?film=<项目id>`。

**功能的工作目录**：从开功能时的 `--touches` 推（取第一个指向工程内目录的，如 `docs/operations/…/<主题-日期>/**`）。字段里的相对路径默认先在工作目录找，再在功能资料目录 `.mashu/features/<key>/` 找。

```json
{
  "workflow": "xhs-post",
  "title": "小红书·图文帖子",          // 标签名（不写用模板名）
  "order": 1,                          // 标签顺序（项目默认工作流总在最前）
  "summary": "一句话说明",
  "aliases": ["film:xxx"],             // 这些工作流 id 的功能也并进本标签（可选）
  "columns": {"1": "这一步看什么", "3": "…", "done": "已完成列看什么"},
  "card": [ 字段, … ],                 // 卡片上的专属字段，最多 12 个
  "overview": [ {"label": "已发布", "field": "published", "agg": "count", "unit": "篇"},
                {"label": "累计赞", "field": "stats", "metric": "赞", "agg": "sum"} ]
}
```

字段（`key` 是字段名，`label` 显示名，`type` 类型）：

| type | 用途 | 关键写法 |
| --- | --- | --- |
| `image` | 缩略图（放卡片顶上） | `path`: glob 或列表，取第一张图 |
| `text` | 取一行字 | `path` + 取法：`json`（JSON 字段，点号分隔）/ `kv`（`名称：值` 行）/ `heading`（首个 Markdown 标题）/ `match`（正则，取第一组）；都不写取第一行正文。`"as": "title"` 显示成标题引用，`"as": "badge"` 显示成标签，`tone` 按值上色 `{"须": "warn", "不用": "ok"}`（ok / warn / bad） |
| `count` | 数文件，如成图 6/9 | `path`（可列表）；`total` 写数字或一个取数字的 text 写法；`unit` |
| `link` | 点开看的文件或网址 | `path`（文件），或 `match` 从文本里取 `http…`，或 `url` |
| `metrics` | 几个数字，如赞 / 藏 / 评 | 公共的 `path` / `section`，`items`: `[{"label": "赞", "match": "赞\\s*(\\d+)"}]` |

通用写法：`from`（`work` 默认 / `feature` 功能资料目录 / `project` 工程根目录）；`section` 只看某一节（字符串 = 标题含这几个字，`{"has": "{workdir_name}"}` = 正文含这几个字的节，取最后一个，适合在 STATUS.md 这种多篇共用的记录里找本篇）；`last: true` 取最后一处；`max` 截断字数；`try: [ {…}, {…} ]` 依次试几种取法，第一个有值的为准。字符串里可用 `{key}` `{num}` `{title}` `{workdir}` `{workdir_name}`。路径不能带 `..`，只在工程和功能资料目录里找。

样板：项目里的 `.mashu/playbooks/dashboards/<工作流>.json`（最完整：封面、标题、张数、AI 声明、发布包、审片、发布、帖子链接、发布后数据）。改完不用重启，网页上点工作流标签右边的刷新就重读。

## accounts.json（按账号分组）

一个项目里有几类活分属不同账号（如一个自媒体项目里的「手账号」「生活号」）时，在 `<工程>/.mashu/accounts.json` 里写账号，网页按账号分组显示。没有这个文件、或文件写坏，都当作没有账号，页面和以前一样，不报错。

```json
{
  "accounts": [
    {"id": "handbook", "name": "手账", "account": "我的手账号", "paths": ["docs/operations/2026-11-handbook/"], "order": 1},
    {"id": "life", "name": "生活号", "account": "我的生活号", "paths": ["docs/operations/2026-11-life/"], "order": 2}
  ],
  "workflows": {"xhs-post": ["handbook", "life"]},
  "features": {"1-hobby-books-2": "handbook"},
  "default": "handbook"
}
```

- `accounts[]`：`id`（开功能 `--account` 用）、`name`（网页显示）、`account`（平台上的账号名，鼠标悬停显示）、`paths`（工程内路径前缀，按改动范围归类用）、`order`（排序）。
- `workflows`：工作流 id → 哪些账号用它。选某个账号时只显示映射里含它的工作流；共用的写全部账号。没写映射的工作流：项目默认工作流总显示，其他的有这个账号的功能才显示。
- `features`：功能 key → 账号 id。过渡用：新功能开的时候带 `mashu new-feature … --account <id>`，直接写进功能 state.json 的 `account`。
- `default`：都匹配不上时归哪个账号（不写取第一个）。

功能归属（`studio.feature_account`，后端统一算，网页和 `mashu status` 只读结果）优先级：① 功能 state.json 的 `account` → ② `features[key]` → ③ 开功能时的 `--touches` 按 `paths` 前缀匹配（最长前缀胜）→ ④ `default`。

网页：侧栏和项目右栏的功能列表按账号分组（组标题 + 计数，侧栏组可收起）；项目页「工作流」视图顶部有「全部 / 各账号」切换（按项目记在浏览器里），选账号后工作流标签、看板、概况行（含面板配置的 overview）都只算这个账号；选「全部」时看板每列按账号分段。改 accounts.json 不用重启，网页刷新就重读。

## 项目适配器（.mashu/adapter.py，可选）

项目要把外部系统（例如研发工单系统）接进麻薯调度时，在 `.mashu/adapter.py` 写几个约定好的函数；没有这个文件就完全按默认行为。麻薯按需调用，**适配器出错只记运行日志（`mashu logs`，kind=`adapter-error`），不影响调度**；`adapter.py` 或 `adapter/*.py` 改了自动重新加载，不用重启服务。适配器归项目自己维护（写在哪个功能里就由那个功能的执行者改），例子：cysj 工作区的 wf 适配器。

每个函数第一个参数是 `ctx`（麻薯给的接口）：`ctx.root` 工程目录、`ctx.docs` 即 `.mashu/`、`ctx.get(k)` / `ctx.set(k, v)` 持久键值（存麻薯数据库，重启不丢）、`ctx.notice(text, key=None)` 发到项目 / 功能频道、`ctx.log(kind, **detail)` 记运行日志、`ctx.features()` / `ctx.feature(key)` 功能列表。`feature` 是 dict：`key`、`num`、`slug`、`title`、`data`（state.json）、`adapter`（state.json 里适配器自己的字段）、`workdir`、`dir`（执行者工作目录的绝对路径）、`done`、`open_cards`（这个功能名下还开着的外部卡片 ref）。

| 函数 | 什么时候调 | 返回 |
| --- | --- | --- |
| `on_new(ctx, feature)` | 开功能后、派执行者前 | 要并进 state.json 的字段，例如 `{"workdir": "issues/1069-ticket", "adapter": {...}}`；`workdir` 必须是工程里已存在的子目录 |
| `kickoff_confirmed(ctx, feature)` | 子脑 / 主脑开功能、发开工审批卡前 | `{"confirmed": true, "ref": "#1153/req/2", "who": "…"}` = 方案已在项目系统里由用户确认，跳过开工审批卡直接开工；`None` 照常发卡。只在用户本人确认过时返回 confirmed |
| `launch_env(ctx, feature, session)` | 启动执行者 | 追加的环境变量（不能改 `MASHU_*`、`PATH`）。`session["resume"]` 为真时 `session["env"]` 是这个会话上次的值，恢复会话要沿用 |
| `launch_prompt(ctx, feature)` | 启动执行者 | 系统提示附加文本；或 `{"text": …, "replace": true, "first": …}`：`replace` 换掉默认的「怎么做」一节，`first` 接在开场「继续 …」后面 |
| `pretool(ctx, feature, tool, input, risky)` | 执行者每次调工具（在麻薯自己的 git 规矩、写文件检查之后） | `None` 没意见（照麻薯的闸门）；字符串 = 拒绝原因；`True` = 适配器担保放行，跳过麻薯的 prod 审批（不可逆操作照样要用户审批）。`risky` 是麻薯的判定 `[闸门, 说明]` 或 `None`。hook 只有 10 秒，外部调用要更短 |
| `sync(ctx, known)` | 每 `SYNC_SECONDS` 秒（默认 20，后台线程） | `{"cards": [...], "close": [...], "messages": [...]}`。`known` 是麻薯里还开着的外部卡片 `[{ref, key, rid}]` |
| `answer(ctx, card, text)` | 用户在麻薯网页回答外部卡片 | `{"status": "answered"/"closed"/"error", "answer", "deliver", "notice", "message"}` |
| `on_idle(ctx, feature, session)` | 随 sync，对每个空闲（本轮结束、没有后台任务）的执行者 | `None`，或 `{"action": "continue"/"stop"/"done", "note", "prompt"}`：`continue` 关掉本会话、换一个新执行者（新名字、不恢复会话）带着 `prompt` 接手；`stop` 收工；`done` 收工并把功能标为完成 |
| `cron(ctx)` | 随 sync | 无。是否到点由适配器自己用 `ctx.get/set` 判断（先记再跑，重启不重复） |

外部卡片：`cards` 每项 `{ref, key, title, text, summary, options, url, web_only, source}`。麻薯建成直接到用户手上的上报（`meta.external=1`），**只有用户能在网页回答**，子脑 / 主脑不能代答；按 `ref` 去重，用户静默过的不再出；`key` 对不上功能的进项目频道；`web_only` 的只显示「去外部网页处理」。`close` 每项 `{ref, answer, deliver}`：外部那边已处理，麻薯关卡片，`deliver` 投给该功能的执行者（没在跑就带着消息拉起一个）。`messages` 每项 `{key, text}`：有 `key` 投给执行者，没有就发到项目频道。

## 项目笔记（.mashu/producer-notes.md）

记长期要记住的事和做过的决定：用户对这个项目的偏好、定下的取舍、踩过的坑，每条带日期和依据。交接不靠它（交接写 `handoff.md`）。项目进展不往麻薯记忆里写，写在这里。

## 相关节点

[子脑手册](../references/roles/producer.md) · [命令参考](../references/commands.md) · [知识地图入口](../indexes/docs.md)
