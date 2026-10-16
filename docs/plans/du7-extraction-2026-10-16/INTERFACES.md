# DU7 共同接口、隔离与交付

## 固定分工

- PDF作者仅新增自己的组件/测试/fixture/PWF；ROOT独占PDF parser、adapter、pipeline、TABLE binder、source_catalog和公共wire接线。
- ET作者仅新增纯抽取模块/新测试/自有PWF，并窄改 `transcript_api.py` 中抽取函数委派、抽取版本及两处调用连接。预算、HTTP监督、翻译、缓存原件验真及产物收据保持。
- DU6的RF四目标模块、AUDIT摘要导入、FF矩阵oracle/cli均不属于本轮。Dayu、StockWiki、invest-quick-scan及生产数据完全不动。
- 工作树必须按launch固定base；同文件上有不明变更先报告ROOT，不reset或吸收他人WIP。禁止在主仓、旧作者树里施工，禁止自行更新合并兄弟分支。
- 测试imports指向当前工区；不全局editable-install，不复制主仓AUTO、.env、API key、公司原件或全库备份。CodeGraph按所在仓AGENTS使用；已知实际路径可直接读，不因索引缺失宣称代码不存在。

## 统一执行方式

只设三个大节点，日常只跑相关快测；已有正确断言不为新实现洗绿。

**A：实际调查与RED。** 读当前代码、原计划与既有交付，记录旧正常行为、原wire/identity及行为反例。接口未导入不是唯一RED。真实/合成/未运行分开，不把旧stale PWF当未完成源码依据。

**B：所属层一次实现与集中回归。** 从共同根因改造，不按公司/产品/页码打补丁，不另建解析器、身份库、任务库、预算、权限/签收链。所属层无法补回的已丢信息保留unknown，列出ROOT需要的接口，继续完成独立部分。

**C：真实入口、清理与交接。** 执行卡内端到端和错误路径，核对旧正确行为。真实样本只能只读，输出在自己创建的TemporaryDirectory；预存sentinel及文件前后按字节保持，原本不存在的下载/派生文件测试结束删除。只清理解析后的绝对路径确在本次owned测试根内的材料，不做完整备份恢复。

预算和时间按现配置执行；这两包默认0供应商调用、0生产写入。必要公开网页只允许无凭证/无付费小样本，必须先写入自己的测试目录，记录URL/SHA/取样范围并结束清理；不得为通过测试启用生产provider或写生产配置。获取失败诚实NOT_RUN，不把拼造网页称真实线上验证。

保留首个行为RED、最终集中结果和C节点清理收据；大日志压缩后逐字节核验，交接资料目标≤5MiB/包。原件、全页全文、重复输出不提交；不能压缩时删掉原失败或改变分母。正常commit源码/测试与必要证据，作者不修改CI/hooks，不跳检查，不推安装副本。ROOT负责最终合并/推送/精确CI。

## 交接输出（两包同一格式）

自有PWF内提交 `handoff.md`、`handoff.json`、`receipts/`，正文含：做了什么/根因/接口/如何复现/测试结果/局限/ROOT接线步骤。JSON：

```json
{"schema_version":"du7-handoff/1","card_id":"DU7-...","base_commit":"40sha","branch":"codex/...","implementation_commits":[],"changed_paths":[],"entrypoints":[],"interfaces":{"inputs":[],"outputs":[],"unchanged":[]},"tests":[{"command":"...","exit_code":0,"passed":0,"failed":0,"skipped":0,"receipt":"relative/path"}],"red_receipts":[],"fixtures":[{"kind":"real|synthetic","source_sha256":"...","scope":"...","expected_basis":"..."}],"old_behavior_preserved":[],"known_limits":[],"not_run":[],"root_integration_steps":[],"supplier_calls":0,"production_writes":0,"cleanup_receipt":"relative/path"}
```

交付后冻结owned源码，给ROOT最终commit和两份handoff路径，STOP。细卡/冻结接口有不足时交具体反例与提案给ROOT，不自行跨白名单扩张。
