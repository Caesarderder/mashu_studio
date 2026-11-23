---
km_id: workflow.report-flow
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
  - reference.role-assistant
  - reference.role-producer
  - reference.role-crew
---

# 三级负责人与上报流程

## 目标

用户 ↔ 主脑 ↔ 子脑 ↔ 执行者之间最少层数、最少打字的协作方式。用户只碰三类事：不可逆审批、真人帮忙、产品方向。

## 角色与权限

```
用户（麻薯的助手）
  ▲  只有：不可逆操作审批 · 真人帮忙（登录 / 密钥 / 付款 / 线下）· 麻薯判断不了的产品方向
  │  回复方式：点卡片按钮（右侧「麻薯需要你」分为 要你审批 / 要你帮忙 / 在问你）
主脑 = CEO：最高权限，可以直接做任何事；管跨项目优先级、并发、工作流、记忆
  ▲ report（跨项目）        ▼ memo（指示）
子脑 = 项目负责人：统筹项目所有功能，可以直接动手；项目内权限等同主脑
  ▲ ask / tell / gate       ▼ new-feature / note / start / stop / restart
执行者 = 本会话负责人：会话内技术决定自己拍板；一个功能一个执行者做到底
```

## 什么事谁拍板

| 事 | 谁拍板 | 怎么走 |
| --- | --- | --- |
| 会话内技术决定（实现、选库、重构、测试做法、AC 范围内的细节） | 执行者 | 不上报，handoff 里写清楚 |
| 需求 / 范围变化、和其他功能冲突、要别人的资源 | 子脑 | 执行者 `mashu ask` |
| 阶段进入（工作流里标了批准的阶段） | 子脑 | 调度器在执行者 finish 时自动申请；批复后同一个执行者接着做 |
| 生产操作、推送受保护分支、合并、发布、进测试环境、预算内用量 | 子脑（项目内）/ 主脑 | 执行者 `mashu ask --gate prod`；子脑、主脑自己执行时不拦，自动记进决策记录 |
| 跨项目：优先级、并发名额、工作流模板、全局方针 | 主脑 | 子脑 `mashu report` |
| **不可逆操作** | **用户** | `mashu ask --gate irreversible`；卡片出现在项目对话（主脑的在主对话），麻薯只能附意见，不能代批 |
| 需要真人（登录、密钥、付款、线下操作） | 用户 | `mashu ask --human --options … --recommend …`（网页归到「要你帮忙」） |
| 麻薯判断不了的产品方向 | 用户 | `mashu ask --options … --recommend …` |

麻薯自己拍板的事都用 `mashu decide` 记下（批复上报时自动记），用户在「决策记录」里追认或推翻。

## 不可逆操作清单（硬拦截，等用户审批）

- 删除整个工程目录（`rm -rf <工程根>`）
- 删数据库 / 表（DROP DATABASE / DROP TABLE / TRUNCATE 等）
- 强推覆盖历史（`git push --force`）
- 项目 film.json 里额外标成 irreversible 的命令（例如停止 / 重启麻薯电脑秘书本身）

另外两类始终是硬拦截、不分角色：会动到别人改动的 git 命令（stash、checkout .、reset --hard、clean -f、add -A、commit -a），以及直接改 `state.json` / `acceptance.json`。

想收紧：`state/studio.json` 里设 `"user_gates": ["prod", "irreversible", …]`、`"strict_checks": true`。

## 沟通规矩（少轮次、一次说清）

- **一条消息说完一件事的全部**：结论 → 要对方做什么 → 选项和推荐（`--recommend` + `--why`）→ 依据（文件路径、上报编号、命令结果）。对方不用回问就能拍板。
- **不回“收到 / 好的”**：只是确认的回复不会被转给上级（调度器会丢掉），没有实质内容就回 `（无）`。
- **几件事合成一条**：同一时间要说的几件事写进一条 `memo` / `note` / `ask`，别连发。
- **能自己定的不问**：执行者在会话内、子脑在项目内、主脑在全局，权限内的事直接做，`mashu decide` 记一笔。
- **通报不打断**：`mashu tell`、阶段完成这类通报和下级的转呈回复是低优先级，调度器攒 90 秒（`batch_seconds`）、等对方空闲时合成一条送达；请示、批复、指示和用户的话立即送达。
- **同一会话的消息合并送达**：多条待投消息会合成一次投递，标明“一起送达 N 条，一轮处理完”。

## 步骤：上报怎么流

1. 执行者 `ask` / `tell` / `--gate` → 先到子脑。
2. 子脑：权限内 `mashu answer` 结案；不可逆 / 产品方向 / 真人帮忙 → `mashu pass <id> "一句意见" --recommend 选项 --why "…"` 给用户。
3. 用户在卡片上点按钮；批复自动投回发起的会话（交接后投给同一身份的新会话）。
4. 例行通报（阶段完成、功能完成）不唤醒任何人，进简报。
5. 完成提醒：自动检查、验收标准、未提交改动没过时不打回，变成一张给子脑的提醒，子脑决定返工（`note` / `restart`）还是接受（`answer`）。
6. 麻薯手上的事不会因超时推给用户。

## 验证

`mashu reports` 看手上的上报；网页「邮箱」看各信箱收发；右侧栏看等用户的事。

## 相关节点

[主脑手册](../references/roles/assistant.md) · [子脑手册](../references/roles/producer.md) · [执行者手册](../references/roles/crew.md)
