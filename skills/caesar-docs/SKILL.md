---
name: caesar-docs
description: 项目知识地图发现、初始化、维护、影响分析与审查；维护六职能 index、工作流与证据路由，并管理通用 skill 与项目 docs 的协作边界。入口为 docs/indexes/docs.md。
---

# Caesar Docs

## 核心规则：需求先检索，再实施

收到需求先读取相关skill与本项目docs索引，快速检索已有决定、规范、工作流、配置和记录，找到真实编辑源与验证路线后再实施；按任务最小读取，不加载全库。没有文档时先做必要发现，普通小修不强制建图。

发现可复用流程时，先查已有工作流，必要时提醒用户是否沉淀到docs；反复出现的要求判断哪些是项目约定，哪些是已验证的通用方法，再建议优化对应skill。已有整理授权直接维护，不重复确认，也不阻塞当前已授权任务。具体见[需求检索与经验沉淀](references/discovery-and-learning.md)。

## 项目知识入口

先读当前项目的 `docs/indexes/docs.md`，沿其中链接读取本轮所需的工作流、规则、实现/资产入口与记录。该 index 缺失时从 AGENTS 和已有 docs 导航继续最小发现；明确要求整理/初始化地图时再补索引，不为普通小修强制建图。Skill 只保存可迁移方法，项目身份、参数、端口、状态与验收证据回写本项目 docs。跨职能仅加读受影响 index；项目已有授权和最新决策优先，不重复审批。

组织、迁移或维护 skill 与 docs 时，读取 [六职能协作合同](references/skills-and-project-docs.md)。六个索引分别承接 docs、builda、design、art、program、producer；项目事实留在各自地图，技能保留通用方法。


Maintain a repository-local knowledge map as the shared system of record for humans and agents. Keep facts in the target repository's `docs/`, not in chat history or this Skill.

This Skill is deliberately limited to documentation knowledge maps. It does not provide Caesar Workbench/HQ, asset management, game-production loops, evidence ledgers, reviewers, browser capabilities, or external services.

## Commands

Recognize these explicit forms:

| Command | Route |
| --- | --- |
| `caesar-docs:docs-init` or `$caesar-docs docs-init` | Read [commands/docs-init.md](commands/docs-init.md). Create or normalize a knowledge map. |
| `caesar-docs:docs-update` or `$caesar-docs docs-update` | Read [commands/docs-update.md](commands/docs-update.md). Synchronize an existing map with verified repository changes. |
| `caesar-docs:docs-review` or `$caesar-docs docs-review` | Read [commands/docs-review.md](commands/docs-review.md). Review map correctness and maintainability. |

An unambiguous natural-language request to initialize, update, or review the Caesar knowledge map is equivalent to the matching command.

Without `docs-init`, never initialize a missing map or perform repository-wide normalization. Report that no map exists and continue with the smallest safe code/config discovery. Existing maps may be read for any non-trivial repository task and updated only when the task changes durable facts.

## Default Discovery Route

For non-trivial implementation, review, migration, architecture, or documentation work:

1. Read [references/docs-discovery.md](references/docs-discovery.md) completely.
2. Find lightweight entries such as `AGENTS.md`, `ARCHITECTURE.md`, `docs/index.md`, and `docs/map/index.md`.
3. Publish the required Docs Discovery Pass before implementation.
4. Follow indexes to the smallest relevant set of nodes. Stop when route, ownership, invariants, implementation locations, validation, and sync targets are known.
5. Verify behavior-sensitive documentation against current code, configuration, and tests.
6. For non-trivial modifications, use the repository's `KM:workflow.impact-map` before editing when available.
7. After implementation, synchronize only durable changed facts and run the map validator.

One-line edits, obvious renames, trivial regex changes, pure answers, or an explicit request to bypass docs may skip this route.

## Knowledge Operations

Load references progressively; never bulk-read them during normal use.

- New or incomplete map: [knowledge-map-structure.md](references/knowledge-map-structure.md)
- Directory placement/synchronization: [directory-operations.md](references/directory-operations.md)
- Navigation indexes: [index-structure.md](references/index-structure.md)
- Frontmatter and node sections: [node-schema.md](references/node-schema.md)
- Domain and tag governance: [domain-tags.md](references/domain-tags.md)
- Feedforward impact analysis: [impact-map.md](references/impact-map.md)
- Link and fact precedence: [link-validation.md](references/link-validation.md)
- `AGENTS.md`/`ARCHITECTURE.md`/README routing: [repo-entry-sync.md](references/repo-entry-sync.md)

Prefer sparse, verified maps over detailed speculation. Use `draft` for uncertain facts, `stale` for facts known to need verification, and `deprecated` for intentionally retired nodes.

## Managed Node Tool

Run from the target repository root. The tool uses only Python's standard library and confines writes to that repository.

```bash
python3 {baseDir}/scripts/caesar_docs.py template --output .caesar-input/node.json
python3 {baseDir}/scripts/caesar_docs.py node-upsert \
  --path docs/<area>/<node>.md --spec .caesar-input/node.json
python3 {baseDir}/scripts/caesar_docs.py validate
```

Updating an existing node requires exact identity:

```bash
python3 {baseDir}/scripts/caesar_docs.py node-upsert \
  --path docs/<area>/<node>.md --spec .caesar-input/node.json \
  --expect-km-id <current-km-id>
```

Delete only an unreferenced, non-required node with explicit identity, reason, and confirmation:

```bash
python3 {baseDir}/scripts/caesar_docs.py node-remove \
  --path docs/<area>/<node>.md --expect-km-id <km-id> \
  --reason "Superseded by <km-id>" --confirm delete:<km-id>
```

The tool owns frontmatter order, `last_verified`, repository containment, atomic replacement, identity preservation, validation, and rollback. The agent owns semantic facts, title, body, relationships, and evidence paths.

For initialization, create nodes in dependency order so every referenced `KM:*` exists when each managed write validates. Finish with a standalone `validate` call.

## Completion Contract

Before handoff:

1. Run the smallest repository validation required by the selected workflow.
2. Run `python3 {baseDir}/scripts/caesar_docs.py validate` after map changes.
3. Check actual changed files against the impact map.
4. Synchronize `AGENTS.md`, `ARCHITECTURE.md`, or README only when their routes changed.
5. Report docs route, nodes read, nodes changed, validation, unresolved stale/draft facts, and residual risk.

Validate this Skill after changing its implementation:

```bash
python3 {baseDir}/scripts/test_caesar_docs.py
```
