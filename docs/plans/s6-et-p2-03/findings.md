# ET P2-03 入口盘点

| 正式入口 | 现有边界 | 需要补齐 |
|---|---|---|
| transcript_tool.py 的 fetch/discover/fetch-candidate | 真实 Worker、一次原 request 与使用量、stdout JSON、stderr usage JSON | 启动和无请求阶段，保证单份机器用量原样。capabilities 和 flush 不联网 |
| scraper.py 的普通批次、list、dry-run、显式 translate | 作业、列表和批次预算，部分拒绝已写 F4；默认不翻译 | 启动与配置错误、子模块共用 file sink、保持由用户选择的只读行为 |
| transcript_audit.py 的默认 JSON、独立报告、可选收据 | 已有逐来源结论与普通拒绝，原件不写 | 缺配置、解析及报告写入的可追溯异常 |
| reader.py 的本机 HTTP、/、/index.html、/api/data | 首页、全文和可选双语旁路 | 启动、bind、读配置/模板、写响应、服务器异常；未知路由仍 404 |
| translate.py 的独立显式翻译 | 已有付费选择和共享本地原件锁 | 启动、实际后端、原始异常脱敏、可选对照文档失败 |
| make_interleaved.py 本地转换 | 原双语 JSON 生成 TXT | 配置、读取、输出的 typed failure |
| tools/feedback_owner.py 的 triage/repair/replay/report/reconcile/get-action/get-usage/runtime-history | 原身份查询、回放和 SQLite；runtime-history DB-less | 失败可诊断，正常冷读无写入，不写回同一反馈积压 |
| retrieval_worker.py 父子协议 | 私有原生 worker，父持有收据并保原尝试 | 早期 typed failure 可查，私有 stderr 不透传，不生产第二份反馈 |

工程 fixture、评测、replay 工具不计入生产。关闭翻译的两个 legacy 旗标继续同义。无生产原件、API 或桌面窗口参与。
