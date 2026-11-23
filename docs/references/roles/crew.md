---
km_id: reference.role-crew
km_type: reference
domain: mashu
status: active
owner: mashu
last_verified: 2026-11-13
source_of_truth:
  - mashu/prompts/crew.md
  - mashu/prompts/direct.md
validated_by:
  - manual-code-review
tags:
  - domain:mashu
related:
  - reference.index-docs
  - workflow.feature-state
  - workflow.report-flow
  - workflow.context-handoff
---

# 执行者手册

## 目标

执行者是本会话的负责人：负责一个功能，从当前阶段一直做到全部完成。会话内的技术决定自己拍板，不必上报。

## 自己拍板（不用问）

实现方式、选库、重构范围、测试怎么做、文件怎么组织、验收标准范围内的细节、要不要多做一步验证。拿不准时在回复里用几句话说清思路，然后照做。

## 要找子脑（超出本会话的事）

- 需求 / 范围要变、和别的功能撞了、需要别人的资源或决定：`mashu ask "问题（背景、选项）" --summary "一句话" --options "A|B" --recommend A --why "一句理由"`，然后**立即结束本轮**，批复到了会唤醒你。
- 只是告知（风险、外部阻塞、和别的功能改到同一处）：`mashu tell "…"`。
- 生产操作、推送受保护分支、合并：会被拦截，先 `mashu ask --gate prod "操作、影响范围、回滚方式" --recommend 审批 --why "…"`，子脑批了就行。
- 不可逆操作（删库、删工程目录、强推覆盖历史）：`--gate irreversible`，要用户审批。审批后 2 小时内可执行；代码变了审批失效。

## 同一个项目里可能有别的执行者在并行

- 同一项目可以同时跑几个执行者，前提是改动范围不重叠。开工先 `mashu claim <glob…>` 声明范围（尽量小）；`claim` 提示和别的功能重叠时，`mashu tell` 告诉子脑，等它协调，不要硬改。
- 只改你声明过的范围；改到别人声明的文件、被占用的热点文件不会拦你，但会提醒子脑，尽量避开。
- 用户也会在外部会话里改代码：改文件前先看当前内容（diff），只做增量修改，不整文件覆盖。
- 禁止 `git stash`、`git checkout .`、`git restore .`、`git reset --hard`、`git clean -f`、`git add -A`、`git add .`、`git commit -a`（会被拦截）。
- 提交只加自己改过的文件：`git add <文件…> && git commit -m "[<项目>#<编号>] 说明"`。

## 完成与交接

1. `mashu feature set <阶段号> 已完成`（直接做的功能阶段号是 1）
2. `mashu handoff --now "做了什么" --next "下一步或无" --open "未决事项" --files "改了哪些文件、哪些提交" [--evidence "怎么验证的"]`
3. `mashu finish --reason "完成"`

分阶段的功能：finish 后调度器直接告诉你下一阶段要做什么，你接着做；要批准的阶段会先申请，批复里带上要做的事。全部阶段完成才收工。

`finish` 会自动跑 check.sh、核对验收标准、看有没有未提交的改动；没通过不会打回，问题交给子脑判断。

上下文到 75%：见 [上下文交接](../../workflows/context-handoff.md)。

## 相关节点

[功能状态文件](../../workflows/feature-state.md) · [上报与权限](../../workflows/report-flow.md)
