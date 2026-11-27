---
km_id: workflow.feature-state
km_type: workflow
domain: mashu
status: active
owner: mashu
last_verified: 2026-11-27
source_of_truth:
  - mashu/studio.py
validated_by:
  - manual-code-review
tags:
  - domain:mashu
related:
  - reference.index-docs
  - reference.role-crew
  - workflow.context-handoff
---

# 功能状态文件、验收标准与改动范围

## 目标

每个功能的事实都落在 `.mashu/features/<编号-名称>/`，换哪个会话都能接上。

## 输入

- `state.json`：状态文件，记录各阶段状态和交接镜像，是**事实来源**。只能用 `mashu feature …` 改。带 `workdir`（相对工程根目录的子目录）时，执行者在那个子目录里启动，这个子目录也自动算进改动范围；带 `adapter` 的是项目适配器自己的字段。
- `acceptance.json`：验收标准，每条只有通过 / 不通过。只能用 `mashu ac …` 改；条目在方案阶段定，之后只能改状态。
- `handoff.md`：交接文档（见 [上下文交接](context-handoff.md)），旧版本在 `handoffs/`。
- `docs/`：`requirement.md`、`design.md`、`review.md`、`release.md`。
- `tests/`：测试结果、自动检查输出。

## 步骤

收到“继续 …”时系统会附上状态摘要（和 handoff.md）。先读摘要，再按状态行动：进行中 → 接着做；待确认 → 先问；已完成 → 交接。发现项目说明缺了或错了，顺手补上，只写事实。

```
mashu feature status
mashu feature set <阶段号> <未开始|进行中|待确认|已完成|跳过> [--note "…"]
mashu handoff --now "做到哪" --next "下一步" [--open "未决事项"] [--files "改了哪些文件、提交"] [--evidence "命令和结果"] [--risk "…"] [--docs "回写节点或无需修改的理由"]
mashu feature handoff --progress … --next …      # 旧写法，效果同上
mashu feature block --text "阻塞原因"            # 解除：mashu feature block --clear
mashu ac add "WHEN … THE system SHALL …"         # 任何阶段都能加
mashu ac pass AC-1 --evidence "命令和结果"  /  mashu ac fail AC-1 --evidence "…"  /  mashu ac list
mashu claim "src/export/**" "tests/export/**" [--add]
mashu sync "接口已提交，等待联调"
mashu peer 12 "请确认导出接口"
```

## 停止条件

全部阶段已完成 → `mashu finish` 后执行者收工。

- 功能标签下既有 `docs/` 外的提交又没有文档改动时，在 `handoff --docs` 写一句无需回写的理由即可。
- 本功能删除其他功能提交过的 Markdown 一级到三级标题时，完成检查会报告原功能编号。恢复小节，或在获得用户确认后通过 `--docs '允许删除 #N「标题」及其依据'` 说明。
- 带 check 的阶段在工程脚本之外校验涉及的知识地图。以该仓库首个功能提交之前的版本为基线，原有错误只提示。项目可在 `.mashu/docs-governance.json` 写 `{"validate":"warn"}` 改为只提示，cysj 默认使用该模式。

## 相关节点

[执行者手册](../references/roles/crew.md) · [上下文交接](context-handoff.md)
