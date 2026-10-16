# DU9统一接口、排他边界及独立审查

三卡均可独立实施和测试；POLICY/PLANNER使用本页固定的纯数据接口和各自自有夹具，不等待/导入兄弟尚未提交实现。ROOT最后装配两者及来源port。它们同属CWP但实际工作目录和源码白名单无重叠。ET是第三仓工作树。DU8的RF/FF/AUDIT仍施工，不进入、不吸收其未提交内容。

## 三大节点

A：读本卡、AGENTS、基线源码及记录，建立独立行为RED/手算期望，不为立项制造失败。B：实现共同根因与相邻集中回归。C：真实所属层公开入口、异常/恢复/资源、清理和独立全面审查；通过后才交接。只这三个大节点，不增加小节点人签。

## POLICY → PLANNER → ROOT

普通JSON兼容dict/不可变值对象即可，不建签收合同系统、新registry、Store或守护进程。

`compile_geometry_policy(resolved_profile) -> du-geometry-policy/1`：含profile_id/profile_version、default_intent、rules。每rule含rule_key、section_role、完整heading_path/aliases、reading_priority、geometry_intent。rule_key是该实际规则规范内容的稳定SHA，不能只用section_role，否则同名不同Part会混。`geometry_intent`为：

```json
{"discover":"on_tabular_evidence|context_only|none|unknown","fine":"on_candidate|explicit_only|unknown","demand_class":"operating|project|mixed|context|administrative|unknown"}
```

枚举字符串分隔线表示可选值，不是实际值。缺字段保守unknown；priority=1不等于需要fine。保留完整规则与原因果component，不另建词库。编译输入是已有registry解析出的profile，禁止读取公司目录/配置文件/网络。

PLANNER输入：以上policy + ROOT按**已有唯一章节匹配语义**绑定的windows。每window含window_key、scope_id、rule_key、physical_start/end、native_navigation_refs、navigation_status(native_destination/conflict/unknown)。原生目的地可作请求导航，不能当正文事实；不猜TOC印刷页偏移。source/body确认和原件identity由ROOT层负责，作者不再做一套身份认证。

建议入口 `plan_geometry_requests(policy, windows, *, page_count, limits, work_budget)` 返回`du-geometry-plan/1`（版本、因果摘要、有限window配额、部分/未规划原因）；`admit_geometry_request(plan,state, *, page,window_keys,tabular_evidence,remaining)` 返回同一调用内更新state及请求决定。limits至少max_pages/max_requests；work_budget使用已有charge/exhausted协议，不能重置/复制额度。state只是一次parse的调用内值，不是新任务系统。tabular_evidence取observed_candidate/absent/unknown，来自实际页观察而非关键词猜数值真伪。若一个函数已足够可用薄session封装，保持这些语义和直接可测试入口。

PLANNER不自行解释heading_path、不从role重新猜政策，不调find_tables/TextPage、不读文件。POLICY不实现调度或章节匹配；ROOT独占matcher facade、parser/source/automation及最终生产接线。若接口确实不足，给出可复现反例和最小建议，保持自己代码独立，不写兄弟卡。

## 隔离与保护

全部测试在owned Temp，预存sentinel，开始/结束核对输入/配置SHA+mtime，finally清除本次新建文件/DB/输出并恢复初始目录；不要完整恢复备份演练。原件、生产配置、Dayu、IQS、安装技能副本、CI/hooks、其他工区均不改。0真实供应商/0付费/0翻译，不读取或输出密钥。正常commit，不跳hook。只保留首次RED、最终集中日志、反例和小汇总，压缩读回核验，目标≤5MiB，不复制资料湖。

## 强制独立审查（用户明确新增要求）

实现完成后必须调用**独立审查agent**，由不同于实施者的agent全面检查最终实现：架构边界、接口兼容、所有差异、删除/重复/硬编码、正常与异常/恢复、原文与数字语义、预算/效率/空间、测试是否真正覆盖、夹具及断言是否被放宽、PWF和交接是否与实际一致。审查agent自己阅读代码和运行必要独立反例，不能只复述作者报告。可继承模型配置，无需指定更便宜模型。

每项问题都要根因修复，交同一独立审查agent复核修复和新增风险；直到审查结论PASS且无未解决问题才可标READY/交接。若发现白名单外问题或缺必要条件，保持OPEN并告知ROOT，不能隐去、用NOT_RUN伪装PASS或宣称完成；不擅改其他仓库。平台不能启动独立agent时明确BLOCKED_BY_REVIEW_CAPABILITY，不伪造审查，不找用户小节点签收替代。

审查实施提交SHA和实际源码/测试SHA清单；审查后修改源码、测试或fixture必须对delta再复核。只补handoff文档可另commit，注明代码SHA未变。独立审查是C节点质量检查，不新增运行时审批/权限。

## 交接格式

自有PWF含task_plan/findings/progress、handoff.md/json、review/independent_review.md/json和receipts。JSON至少：schema_version=du9-handoff/1、card_id、base_commit、branch、implementation_commits、changed_paths、interfaces、root_integration_steps、tests[{argv,exit_code,passed,failed,skipped,receipt}]、red_receipts、independent_expected_basis、known_limits、not_run、supplier_calls=0、production_writes=0、cleanup_receipt；以及independent_review{reviewer_agent_id,reviewed_commit,reviewed_paths_sha256,report,findings:[{id,severity,cause,status,fix_commit,verification_receipt}],final_status:PASS,unresolved_count:0}。真实未覆盖限制与实现缺陷分清，不能隐瞒。

正常提交实现/测试/PWF，最后交接commit后冻结STOP。作者不并主线、不推远端main、不安装技能；ROOT按最终SHA验收、生产联调、并线和推送。
