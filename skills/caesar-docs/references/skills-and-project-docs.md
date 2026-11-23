# Skill 与项目知识地图协作合同

Skill 保存可迁移的方法、模板、脚本与适用条件；项目 docs 保存该项目的决定、工作流、实现落点、配置来源、记录及证据。项目名、端口、账号、商品、地图参数、发布状态和用户对某款游戏的偏好不能成为通用 skill 的默认值。

## 三类与六入口

| 类别 | 入口 | 每次任务优先读取的项目索引 |
| --- | --- | --- |
| Builda 官方接入 | caesar-builda | docs/indexes/builda.md |
| Docs 知识管理 | caesar-docs | docs/indexes/docs.md |
| Caesar 游戏开发 | caesar-design | docs/indexes/design.md |
| Caesar 游戏开发 | caesar-art | docs/indexes/art.md |
| Caesar 游戏开发 | caesar-program | docs/indexes/program.md |
| Caesar 游戏开发 | caesar-producer | docs/indexes/producer.md |

caesar-builda 是本地通用接入方法；官方 builda、builda-online、builda-online-diagnostics 及锁版 SDK 指南仍由官方工具链拥有。接入方法只路由到它们，不复制 API 手册、不修改锁版交付物、不自称官方发布。

## 双向工作顺序

1. 确认当前项目根与本轮任务，读 AGENTS 和对应职能 index。已有索引只读本轮需要的链接。
2. index 指向本项目的约束/决定、工作流、实现或资产入口、运行手册与记录。沿这些链接核对事实，再按任务读取 skill 内的专项方法。
3. 在项目中实施；跨职能只读受影响职能的 index。跨项目任务逐个确认根目录和授权范围。
4. 通用方法决定如何做，项目合同决定做什么；已确认需求优先于案例。API 语义来自锁版官方文件，实现现状来自当前源码，验收结论来自对应版本证据。
5. 把项目结果写回 index 指向的记录节点；只有确有复用价值、已经验证且去除项目身份的经验才回流 skill。不要每轮向索引、AGENTS、工作流头部重复追加日志。

## Index 的最小合同

每个职能 index 写清：负责范围、什么时候读、当前权威入口、重复工作流、实现/配置/资产位置、记录与证据目录、验证命令、待核实项及何时回写。用链接路由，不抄一套状态、数值和整个 GDD。尚无事实时写明未建立，不能用旧案例填空。

六个 index 均从 docs/index.md 或 docs/map/index.md 可达，km_id 使用 reference.index-docs / reference.index-builda / reference.index-design / reference.index-art / reference.index-program / reference.index-producer。具体文件保持项目相对路径。

## 大项目和多项目

父级地图只保存共享模板、工程目录、跨模块接口与公共约束；游戏自己的地图保存产品、配置、实现与证据。子项目可独立迁移，交付携带所需通用 skill 的真实副本及内容指纹，不依赖父目录软链接、个人绝对路径或运行时 skill 文件。

大项目在六个职能 index 下按稳定领域继续分索引，使用领域＋文件归属定位，不一次加载全部 docs。源代码属于一个真实维护位置，领域之间通过明确接口和交付物衔接。技能方法共享，父项目的通过结果不能当作子项目验收。

## 更新和迁移

有共享技能库时在其已知来源修改，先比较项目副本与基线，合并本地独有内容，再分发。项目锁记录来源、版本、文件 SHA256 和对应 index。默认只检查差异；同步不得覆盖未解决的本地改动。官方技能按各项目 SDK 锁更新，不能用统一副本升级官方文件。

已有专属 skill 的项目规则移到 docs/workflows 或 references；保留迁移对应表、原件归档和必要的调用方修改。通用专项进入六入口下面的普通模块文档，不再以多个 SKILL.md 自动加载。

## 既有文档的渐进迁移

新地图节点使用 frontmatter。历史文档不批量伪造验证日期；可在 docs/map/legacy-documents.json 逐文件登记 version:1 与 documents 列表。验证器报告受管节点数和未复核正文的旧文档数，拒绝将已有 km_id 的节点排除。新增未登记的 Markdown 仍需符合节点合同；升级一个旧文档时移出登记表、验证事实并接索引。

归档保留原内容与来源，当前入口只写最新事实的链接。结构通过只说明节点/路径/引用可解析，不代表旧记录的全部内容、游戏或真机已通过。

## 可重复同步

`python3 <caesar-docs目录>/scripts/sync_skills.py --source <六技能源目录> --project <目标项目>` 默认只列计划；核对后加 `--apply` 写入。已有副本偏离项目锁时拒绝覆盖，先审阅并合并差异；不要为通过检查直接刷新锁。首次接管已有目录也必须先比对与备份。`scripts/check_organization.py --project <目标项目>` 检查六技能/index双向路由、内容指纹与模块链接。工具只同步六个 Caesar 入口，不处理官方 Builda 文件。

制作人统筹“策划 → 美术方案 → 模板建项 → 程序与美术并行 → autotest体验迭代”。producer index 连接产品合同、制作状态、跨职能依赖与试玩基线；专项方法继续由 design/art/program/builda 承担，docs 维护地图，不复制多个进度账本。

## 配置与学习回路

数值配置与美术映射分别由design/art负责语义，program负责同源消费、导出和校验，producer负责阶段交付。项目配置导航登记实际编辑源、消费者、生成与验证入口；不复制一套可漂移数据。收到需求先检索，发现重复流程与反复要求按[检索与沉淀规则](discovery-and-learning.md)处理。
