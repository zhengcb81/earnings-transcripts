# ET S3/S4 预算和预检的 owner 边界

基线为远端 main `d69448614aa0e02b5059d232b9d981bcee6fc89a`。原主工区已有 PWF 在途，隔离工区独立推进。保留本仓原件、回执、CLI、下载限额、截止、使用计数和付费状态。

现有 CLI 已通过纯 `preflight_transcript_fetch` 做父进程预检。生产消费者仍取 `retrieval_budget._BudgetSession`、`transcript_preflight._*`，形成同仓私有 API 耦合。先写 owner 接口和模块接线回归，再公开该层现有行为并保留旧预算兼容导入。末尾运行真实 worker 的离线预算/用量及原回执恢复。独审后发布。

本卡先不重新设计 replay 历史故障生成。现有 launcher/session 注入覆盖 HTTP；两条历史负例仍注入 period 误归属或抑制 stderr 用量发布。生产源码不会因为测试增加一个能关闭真实验证或回执的开关。

RED 实测 1 failed、7 deselected。旧 worker 缺公开预算适配器，既有 provider 模块只能经私有函数导入预检和结果构造。

实现将预算 wrapper 作为标准公开类型，旧隔离试验/维护 import 仍是同一对象。schema 归一化、非法输入类型、合法 ticker、原结果构造、provider 能力验证由 pure preflight 公开。生产调用继续用原运算和原 budget 实例，未增加额度、字段、许可或 CLI 参数。

## 验收

8 个定向测试文件跑出 230 passed 和 1 项旧测试失败，145.19 秒。失败在准确 d694486 的 Git 导出上单独复现。它依靠旧的 stderr 人类日志，而 S2 已令 `--report-usage` 模式只输出一个机器 JSON。用真实 worker 保留原超字节状态、请求 1 次、实读 20,386 字节及目录遗留断言，cold read 持久 detail 中的脱敏清理类型、返回的同一 usage 和有效回执。该尾验与既有 S2 的正反场景合计 3 项通过，13.74 秒。

作者的组合还覆盖已观测用量的 F4 owner 冷读、丢回告后的原动作查询、已付状态重用、不重复 provider 请求、真实 worker 的额度与请求超时。用途和额度都走合成数据，未访问真实配置、原件或支付，临时测试目录收尾清理。Ruff 定向语法/API 扫描、git diff 检查通过。非作者复核通过。

后续仍包括 replay 历史故障注入，已核实它会直接改 provider period parser 和用量 publisher。现有 HTTP 注入不足以等价替代这两例，无须为此向生产入口增加业务校验开关。发布以主线实际 Git SHA 及 CI 为准。
