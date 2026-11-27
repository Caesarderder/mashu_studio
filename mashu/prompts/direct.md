## 怎么做

这是一个**直接做**的任务。开工按需读 `docs/indexes/docs.md`（没有就读 `AGENTS.md`），用 `caesar-docs` 定位目标与验证命令，找到方向就动手；项目规则附在末尾。

- 并行协作：先看 `mashu codex` 和 `mashu sync`，共享接口或提交有变化就 `mashu send @crew "…"`；撞车先 `mashu send #<编号> "…"`，需要裁决再 `mashu send up --type fyi "…"`。多任务同目录用 `mashu claim "src/x/**"` 声明范围。
- 读需求 → 直接改 → 自测（有 `check.sh` 就跑）→ 只提交自己改的文件；拿不准先说思路再做，真定不了再 `mashu send up --type ask`。
- 改了界面：按页截图，用 `mashu send up "界面改好了，请看截图\n![说明](绝对路径)" --type ask --options "确认|还要改（请写清哪页哪里）" --recommend A` 确认；**收到确认前不标完成、不 finish**。
- 做完对照验收标准：`mashu feature set 1 已完成` → `mashu handoff --now … --next "无" --files … --evidence … --docs …` → `mashu finish --reason "完成"`。
- 收到「继续 …」按状态摘要和 handoff.md 接着做；`mashu feature status` 看状态。
