# DU9 ET ROOT接收 — 2026-10-17

**组件验收通过。** 作者6d94c38；最终源码/测试仍是第5轮独审da82f3a，之后仅交接文档变化。沿用五轮独立审查PASS/0未解决，ROOT核实际版本和最终回归；未新增重复审查或许可链。

ROOT实际454PASS（118.70秒）、10gold一致、ruff通过；FF117PASS（12.58秒），W07_ET_TOOL显式指向本次真实ET CLI，测试无skip。CWP原11PASS/1FAIL是遗留unknown-date_fmp_文件名断言：当前canonical writer已用内容SHA/Windows路径预算命名，来源/日期由元数据负责。改验.json、provider=fmp、filing_date=None，保留原件完整字节/SHA、source ID、publication=None/as-of未知、reader读取、历史截止拒绝、重复复用与清理。原RED保留，受影响CWP12项最终通过，不反复跑不变ET/FF套件。

联调是离线分段真实入口合同：ET真实worker＋fake HTTP；FF真实ET pre-HTTP失败路径＋fixture成功/真实CWP CLI；CWP假provider→原件入库→reader→证据。它不等于真实供应商或单条全真实成功链，也不代替FF完整41例C。

0真实网络/供应商/LLM/翻译/原件复制；临时目录清理、sentinel字节mtime和配置/源码SHA不变。作者工区只读，主仓.workbuddy-ai与eval_results.json保持。ET无已配置CI/提交前钩子，不声称远端CI成功、不为收包临时加新门。没有独立ET技能安装副本，FF继续指向同一主仓工具路径，无须覆盖其他技能。

ROOT正常保留完整历史并线推送；实际发布SHA、远端核对和自建工作树清理见CWP母PWF最终接收记录。随后回到CWP生产装配、真实PDF深页/摘要/缓存恢复/holdout；这些仍OPEN。
