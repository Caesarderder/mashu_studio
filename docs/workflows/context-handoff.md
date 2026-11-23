---
km_id: workflow.context-handoff
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
  - workflow.feature-state
---

# 上下文交接（75%）

## 目标

上下文快满时，同一个身份换一个干净的新会话接着做：名字、角色、负责的对象、tmux 窗格都不变，靠 handoff.md 接上。

## 输入

| 角色 | handoff.md 位置 |
| --- | --- |
| 主脑 | `state/handoff/handoff.md` |
| 子脑 | `<项目>/.mashu/handoff.md` |
| 执行者 | `<项目>/.mashu/features/<编号-名称>/handoff.md` |

固定四节：`## 现状`、`## 下一步`、`## 未决事项`、`## 关键文件`。旧版本自动归档到同级 `handoffs/`（保留 50 份）。项目笔记、记忆只放长期知识，不承担交接。

## 步骤

1. 上下文到 75%（`studio.json` 的 `context_handoff`），调度器给会话发一次提醒。
2. 会话完成手头最小的一步，`mashu handoff --now "现状" --next "下一步" --open "未决事项" --files "关键文件"`。
3. `mashu finish --reason "上下文交接"`，结束本轮。
4. 调度器在同一个窗格里换上新的 claude 会话，启动上下文里附上 handoff.md 全文；新会话先读它，核对「未决事项」，按「下一步」续做。手上的上报不会卡住交接，批复会投给新会话。

## 停止条件（兜底）

提醒后又跑了一轮且上下文 ≥ 90%（`context_force`），或提醒后超过 10 分钟（`handoff_grace_minutes`）还没 finish：调度器在会话空闲时强制交接；没写 handoff.md 就用状态、上报、最近消息自动生成一份（标明“自动生成”）。不在一轮进行中杀会话。

## 验证

频道里出现“—— 上下文交接：「名字」换新会话接着做 ——”分隔线（强制的标“强制”，主对话另有一条动态）；执行者的功能资料里有「交接」文档。

## 相关节点

[功能状态文件](feature-state.md)
