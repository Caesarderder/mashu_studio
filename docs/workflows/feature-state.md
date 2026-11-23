---
km_id: workflow.feature-state
km_type: workflow
domain: mashu
status: active
owner: mashu
last_verified: 2026-11-13
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

- `state.json`：状态文件，记录各阶段状态和交接镜像，是**事实来源**。只能用 `mashu feature …` 改。
- `acceptance.json`：验收标准，每条只有通过 / 不通过。只能用 `mashu ac …` 改；条目在方案阶段定，之后只能改状态。
- `handoff.md`：交接文档（见 [上下文交接](context-handoff.md)），旧版本在 `handoffs/`。
- `docs/`：`requirement.md`、`design.md`、`review.md`、`release.md`。
- `tests/`：测试结果、自动检查输出。

## 步骤

收到“继续 …”时系统会附上状态摘要（和 handoff.md）。先读摘要，再按状态行动：进行中 → 接着做；待确认 → 先问；已完成 → 交接。发现项目说明缺了或错了，顺手补上，只写事实。

```
mashu feature status
mashu feature set <阶段号> <未开始|进行中|待确认|已完成|跳过> [--note "…"]
mashu handoff --now "做到哪" --next "下一步" [--open "未决事项"] [--files "改了哪些文件、提交"] [--evidence "命令和结果"] [--risk "…"]
mashu feature handoff --progress … --next …      # 旧写法，效果同上
mashu feature block --text "阻塞原因"            # 解除：mashu feature block --clear
mashu ac add "WHEN … THE system SHALL …"         # 任何阶段都能加
mashu ac pass AC-1 --evidence "命令和结果"  /  mashu ac fail AC-1 --evidence "…"  /  mashu ac list
mashu claim "src/export/**" "tests/export/**" [--add]
```

## 停止条件

全部阶段已完成 → `mashu finish` 后执行者收工。

## 相关节点

[执行者手册](../references/roles/crew.md) · [上下文交接](context-handoff.md)
