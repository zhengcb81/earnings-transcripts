# 进度

ROOT只建立独占代码工作树与启动PWF，0原件副本/0供应商调用/0生产写入。

A完成：调查 `_extract_body`、两fetch调用点、CWP消费合同（extraction_version任意≤128字符串，FMP仅精确匹配fmp-api-content/1，无需ROOT接线）。fixtures/du7_extraction/ 冻结三RED+兼容基线（手写期望+UTF-8 SHA，compat body SHA=97b5b19f…与旧golden一致）。首个行为RED收据 receipts/a_first_behavior_red.txt：3 failed+1 passed，非import级。

B完成：新增纯模块 transcript_extraction.py（一次DOM观察、最近结构块所有权发射、节点作用域过滤、v1参考投影逐结果标注 /1|/2）；transcript_api._extract_body 窄委派，两调用点经 `_extract_page` 共享owner；新增 behavior/edges/wiring 测试 20 项。集中回归 190/190 绿（receipts/b_concentrated_regression.txt）；golden check 10/10；ruff 绿。

C完成：真实入口 e2e 4 项（transcript_tool/scraper 经 supervisor+worker+fake HTTP seam）：一次抽取、原始payload SHA、新版本、默认disabled零HTTP、已有TXT不重抓+sentinel/旧v1字节恢复、失败无半文件。最终 24/24 绿（receipts/c_final_du7_suite.txt）。清理收据 receipts/c_cleanup.json（0泄漏）；handoff.md/json 提交。0供应商调用/0生产写入。源码冻结待ROOT并线。
