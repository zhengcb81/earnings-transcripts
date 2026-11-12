# 独立卡统一交接格式

交接目录为各卡自己的 `docs/plans/mb11-<lane>-2026-11-11/`。只需 handoff.md、handoff.json、review.md、测试与恢复证据索引；同一内容不复制到多套签收文件，不生成授权JSON。

## handoff.md

1. 实际代码基线、最终commit/branch/remote推送结果、目标主线；确切允许改动与实际diff文件表。
2. 每项卡要求：原调用链根因、先红行为、修复归属、保持兼容、最终证明。无代码变更的已满足项明确NO_CHANGE_PROVEN及实际用例。
3. 公开接口变化（默认none）：当前schema/CLI/签名，是否新增可选项、如何兼容；需ROOT消费的小fixture路径和执行命令。
4. 实际测试：命令、版本、退出码、pass/fail/skip、耗时、未执行部分与原因；日志可以压缩但不能裁掉失败内容。
5. 独立审查：agent标识、审查范围/源码commit、发现、修复commit与复核结果。交付前修复全部本卡可行动问题；范围外问题单列且不能冒称全局通过。
6. 隔离恢复：测试目录初始/结束状态、保护原件/配置/既有WIP未变、实际网络/模型调用数、费用unknown、临时文件清理归属。
7. 安装/部署/CI/合并状态分开；ROOT下一步一条可执行命令和必须使用的输入/配置。作者只交分支，不宣称ROOT已合并。

## handoff.json 示例

```json
{
  "schema_version": "module-boundary-handoff/1",
  "lane": "MB-ET",
  "status": "REVIEWED",
  "repository_root": "C:/Users/.../mb11-et",
  "base_commit": "<actual>",
  "commit": "<final code commit>",
  "branch": "codex/mb11-et",
  "target_branch": "main",
  "pushed": true,
  "changed_paths": [],
  "requirements": [{"id": "ET-01", "result": "PASS", "red_evidence": "...", "green_evidence": "..."}],
  "public_contract_changes": [],
  "test_runs": [{"command": "...", "exit_code": 0, "passed": 1, "failed": 0, "skipped": 0, "seconds": 1.0, "log": "..."}],
  "independent_review": {"reviewer": "...", "reviewed_code_commit": "...", "report": "review.md", "open_in_scope_findings": []},
  "environment_restored": true,
  "network_calls": 0,
  "model_calls": 0,
  "out_of_scope_findings": [],
  "next_root_action": "Run the public fixture against the selected CWP consumer"
}
```

字段未知填null并解释，不填伪造零值。最终代码审查之后仅补文档提交时，分别记录代码版本与文档commit；不追逐自引用hash，也不要求每次文档提交重审。文件大小不作交付许可。

ROOT接收只验证范围、证据对应、公开兼容与组合影响；不会按历史固定测试数量要求凑数。真正退出条件在acceptance.md。
