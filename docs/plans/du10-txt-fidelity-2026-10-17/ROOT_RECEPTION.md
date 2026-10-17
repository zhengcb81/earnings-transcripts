# DU10 ET ROOT 最终接收

状态：PASS（修复后集中回归与独立复审）；发布提交见 Git 主线。

作者最终 cb8e29cddb3836fe670d93d65a02678f5ce8e466 的全部历史保留。ROOT 独审发现并修复两项：

1. 最后 `>` 的字符串判断无法识别带 `>` 的未闭引号属性/注释：改用标准 HTMLParser pending-token 状态。只检查词法残留，不声称能够判定任意文档/句中截断。
2. main 直挂或 inline 文本没有 owner：由 main 作为 fallback owner，保持原次序，新增恢复内容诚实输出 /2。旧冻结 comparator、标签和 golden 不变。

TDD：新边界先 6 FAIL /4 PASS；修复后 ROOT+作者 DU10+DU9+DU7/robustness 合计 304 PASS /80.05 秒。独立复审 15 个控制 PASS、0 未解决，受审源码/新测 SHA 见 receipts/root-et-root-repair-review.json。ruff 两文件通过。FF companion/transport/launch/budget 117 PASS /17.09 秒（W07_ET_TOOL 明确指向 ROOT ET 工区），未用旧已安装工具冒称联调。

成本事实：新增一个 latin1 完整输入视图和标准 tokenizer 预检，然后一个 BeautifulSoup DOM；不是单次 tokenization，也没有第二份 DOM。真实线上 MF DOM 未下载重测；不将真实 TXT+合成标记往返冒称线上 DOM 验收。现有本地原文与配置未改、未翻译，0 供应商调用。ET 没有 CI workflow，不冒称 CI 绿。

后续：正常快进主仓 main 并 push；母 PWF 记录远端 SHA。无新增安装技能同步要求。
