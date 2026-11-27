---
km_id: reference.index-docs
km_type: reference
domain: mashu
status: active
owner: mashu
last_verified: 2026-11-13
source_of_truth:
  - mashu/studio.py
  - mashu/prompts/
validated_by:
  - manual-code-review
tags:
  - domain:mashu
related:
  - reference.role-assistant
  - reference.role-producer
  - reference.role-crew
  - reference.mashu-commands
  - reference.platforms
  - workflow.report-flow
  - workflow.project-assets
  - workflow.feature-state
  - workflow.context-handoff
  - memory.mashu-index
---

# 麻薯的知识地图

## 范围与何时读

麻薯电脑秘书（本仓库）的入口。主脑、子脑、执行者的系统提示只写身份和“先读哪里”，其余都在这里按需读：像读 skill 一样，用到哪节读哪节，不要一次全读。

## 按角色

| 你是 | 先读 | 需要时再读 |
| --- | --- | --- |
| 主脑（CEO） | [记忆索引](../memory/index.md) → [主脑手册](../references/roles/assistant.md) | [上报与权限](../workflows/report-flow.md)、[命令参考](../references/commands.md) |
| 子脑（项目负责人） | 项目入口（项目有 `docs/indexes/docs.md` 就读它，否则 `.mashu/README.md`）→ [子脑手册](../references/roles/producer.md) | [上报与权限](../workflows/report-flow.md)、[命令参考](../references/commands.md) |
| 执行者（本会话负责人） | 状态摘要（系统自动附上）→ [执行者手册](../references/roles/crew.md) | [功能状态文件](../workflows/feature-state.md)、[上报与权限](../workflows/report-flow.md) |

## 按任务

| 要做的事 | 读 |
| --- | --- |
| 谁拍板、什么找用户、怎么上报 / 审批 | [上报与权限](../workflows/report-flow.md) |
| 上下文到 75%、换会话 | [上下文交接](../workflows/context-handoff.md) |
| 状态文件、验收标准、claim | [功能状态文件](../workflows/feature-state.md) |
| 查某个 `mashu` 命令 | [命令参考](../references/commands.md) |
| Claude / Gemini / Codex / OpenCode 怎么接、hooks 怎么注入、会话记录在哪、已知限制 | [平台说明](../references/platforms.md) |
| 改麻薯的记忆 | [记忆索引](../memory/index.md)（记忆文件也是 docs 节点，保留 frontmatter，只改正文） |
| 改提示词 / 调度器 | `mashu/prompts/`（只放身份 + 入口）、`mashu/studio.py` |
| 登记新工程；项目的文档、工作流模板、行为模板、项目笔记怎么做、放哪 | [项目资料制作规范](../workflows/project-assets.md) |
| 工作流模板、每个阶段的行为模板 | 通用模板在麻薯 `playbooks/*.json`；只给某个项目用的工作流和行为模板放在那个工程里：`<工程>/.mashu/playbooks/*.json` + `behaviors/`（`do` 首句指向它；开功能时先找本项目的，`mashu playbook list` 全列出来）。项目数据一律放在工程文件夹内部 |


## 记录、验证与回写

- 维护 docs 时可以用 caesar-docs skill（受管会话已加载）：`python3 <skill>/scripts/caesar_docs.py validate`。
- 只改对应节点，不往本索引追加日志；行为变了就同步改节点和 `last_verified`。
