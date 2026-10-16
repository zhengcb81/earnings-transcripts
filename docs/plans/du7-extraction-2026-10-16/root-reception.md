# DU7 ET：ROOT接收与三个共因修复

**测试依赖补充已收尾：99项集中及7项跨cwd通过，0SKIP；旧3FAIL/1SKIP证据保留并已通过Temp/虚构key/fake adapter修复。见[root-reception-test-hygiene.md](root-reception-test-hygiene.md)。**

状态：**大节点通过，源码冻结，等待ROOT正常提交/并线/推送**。作者HEAD `511fe4968e3be86dd3c548b231a7a19a57020582`，基线 `d0ac42c60dcca992db78860b8469cfed3dd527e5`。本记录是作者READY页之后的接收状态；详细SHA/argv/范围见root-reception.json，原RED与集中日志见root-reception-receipts.zip。

## 实际完成

作者原交付198项集中独立复跑通过，原10gold匹配。独立审查又找出三个共因，并在独立ROOT树用TDD修复：

1. 广告判据从宽data-ad前缀/任意业务词，收敛到真实UI/ad结构和显式广告属性；业务adoption/adjusted/subscription/advertising/cookie/banner不再被误删，false标记不删除。
2. DOM递归变迭代iterator栈，不抬递归深度，隐藏/广告子树剪枝；1200层visible/hidden和2400层一次DOM观察控例通过。
3. 同owner原始text nodes先按真实邻接组合，再统一空白规范化；不猜节点间空格。$1.<strong>2</strong>、负号/%、中文亿元、英文词、原有空白/NBSP、BR、标题和block/list边界有独立期望。BR作为原block内部的空白分界，不合并相邻词；不同结构block仍分段。

ROOT新增30项。先保留2共因10FAIL/5PASS→213集中PASS，再保留跨inline10FAIL/20PASS→30控例PASS。最终 **原198+ROOT30 = 228 PASS /132.69s**，旧 **10 goldens matched**，Ruff和diffcheck通过。不是每小步重复全仓；前213是新发现数字问题之前的冻结收据。

## 原测试没有为了实现洗绿

原gold和HTML/expected.json全部未改。ROOT基于原DOM明确批准只改作者新edges用例两条错误空格断言：`Good afternoon ,`→`Good afternoon,`，`$1.2 billion ,`→`$1.2 billion,`。原测试/fixture/期望SHA已zip封存并逐字节读回核验。最初作者LF/ROOT新checkout CRLF使rawSHA guard拒绝写入，已证明只有Git换行差异再按原CRLF定点修改，收据保留。

冻结v1参考函数源码与作者交付完全相同；只在实际标题/body字节等同时标/1，其余标/2。FMP、默认关闭Motley、HTTP/费用/截止预算、翻译/并发/缓存原件层不改。两fetch仍经同一个owner，需ET发布同时包含新纯模块，不需CWP/FF wire扩展。

## 旧基线失败单列保留

不能按作者“忽略4失败”处理。隔离真实基线源码与交付对照均为 **3 FAIL +1 SKIP**：2个scraper假定生产transcripts目录存在；1个MSFT资料用例SKIP；1个translator测试没有受控key却断言必须LLM。不是本卡新回归，也不是228通过的一部分。建议后续测试卫生节点用临时TXT及虚构key/availability mock，不能删除断言或依赖真实付费调用凑绿。

## 验收边界与清理

实际CLI/supervisor/worker走fake HTTP seam，原TXT不重抓、不翻译，provider默认不改。0外网/供应商/API费用，作者树未改；所有Temp/新下载/receipt已删、sentinel保留，源/fixture/配置SHA守护通过；本次Ruff生成缓存已精确校验目录后删。

没有已有真实Motley HTML，live DOM适应性仍NOT_RUN。新所有权walk有迭代线性遍历性质，但冻结v1按block get_text仍继承病态嵌套的二次扫描风险，不宣称整个parser的CPU/内存严格线性或完整hard budget。原first-main/global-first-h1选择语义保留。

## ROOT后续

正常提交精确3份源码/测试与本接收记录，合入ET主线，推远端并核精确HEAD；此Git树没有CI工作流，保留实际本地测试收据；master PWF更新三个共因和上述旧测试债/未测范围。作者树、生产配置和原件不动，不加人工许可链。本agent无Git提交。
