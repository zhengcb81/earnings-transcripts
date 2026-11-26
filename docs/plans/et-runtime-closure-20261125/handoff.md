# ET 隔离候选独审交接

候选以 `7f4c1e7df865581babef3365c36ec763ba71e2d6` 为基线。总控可在 `.planning/r25/et` 核对实现，在同级目录读取 `et-runtime-remediation.patch` 和 `et-runtime-remediation.manifest.json`。

## 核对内容

| 场景 | 原行为与新证据 |
|---|---|
| 父进程准备、写信封或创建进程失败 | 原版异常越过进程结果边界，丢业务流及用量。当前均从同一结算路径返回原命名失败，完整零用量，回收所属暂存 |
| 已进入 HTTP 传输后强停父进程 | 真实合成子进程持有请求证明。只终止父进程，子进程通过原管道断开自行退出。两次新进程按原身份读回已派发阶段，F4 保持未知费用，同一请求仅一次 |
| 日志或 F4 出口不可用 | 普通 stderr 仍可见结构化故障，机器 stderr 保持单份 JSON。禁用投递仍记录本地故障。迁移证据使用现有 owner 根及轮转日志 |
| 批量例外 | 保留已下载原件、原用量及实际失败退出。日志与聚合结果记录同一脱敏异常链 |
| 依赖边界 | 监督与 worker 只通过内部协议及同属 ET 的结构日志协作。阅读器从配置及解析模块取值。离线回放通过显式注入构造旧错期次和旧丢回执场景 |
| 能力和补投 | `--capabilities` 为纯能力查询。用原环境身份执行 `--flush-observations` 补投。冷热主键以 `run_id`、`attempt_id`、`step_id` 对齐 |

## 复跑

在候选根用同一 Python 环境，指定一个新短测试根和独占 `RESEARCH_FEEDBACK_STATE_ROOT`。本机使用 Python 3.13.9。日常 CI 的目标列表在 `.github/workflows/ci.yml`。

```powershell
python -B -m pytest -q -p no:cacheprovider tests/test_s2_worker_bootstrap.py tests/test_runtime_contract_isolation.py tests/test_feedback_observation.py tests/test_feedback_owner.py tests/test_batch_runtime.py::test_unexpected_batch_exception_leaves_a_redacted_native_cause
```

日常目标本机通过 109 项，最终实现再验强停、监督、补投和隔离共 24 项。兼容门面、阅读器、能力查询和 owner 组合 80 项通过。较早的 `test_retrieval_runtime.py` 宽组在共享宿主上有五个极短预算用例在走到原测试信号前耗尽。产品仍执行该请求的真实硬截止，此组的稳定性另以隔离宿主或原 CI 环境复验。

本次使用合成数据、替身与底层拒绝注入。真实供应商请求和费用均为零。远端执行与独审签票由总控在并线后记证。
