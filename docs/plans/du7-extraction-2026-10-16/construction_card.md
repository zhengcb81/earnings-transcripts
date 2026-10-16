# DU7-ET-EXTRACTION：既有电话会网页抽取忠实度

## 0. 位置、证据与价值范围

工作目录：`C:/Users/郑曾波/Projects/earnings-transcripts-du7-extraction`。
实际原仓：`C:/Users/郑曾波/Projects/earnings-transcripts/earnings-transcripts`，不要在外层同名父目录施工。
自有计划：`docs/plans/du7-extraction-2026-10-16/task_plan.md`。
分支：`codex/du7-et-extraction-20261016`；基线d0ac42c，full SHA/种子见launch。

此卡不是N5原文复用、W06截止/预算或翻译开关重派。现 `_extract_body` 把main内p/h2/h3/li全部平铺，再用SKIP_TEXT子串和长度<5删除；ROOT独立纯HTML反例已复现：

- `Advertising revenue grew 20% and advertisement pricing declined.`业务整句被丢。
- li>p的同一业务段落被输出两遍。
- 短标题 `Q&A` 被删除。

共同根因是DOM语义所有权和粗文字过滤，不能分别补这三个字面例外。**问题属于默认关闭的既有Motley网页能力；不代表FMP API正文失败，不允许开provider、增加订阅或改默认回退策略。** 当前即可离线独立实施，与DU6/ROOT均无文件重叠。

## 1. 排他写入范围

- 新根模块 `transcript_extraction.py`（必要的小私有模块仍放同一新包，先在A列明）。
- `transcript_api.py`只限 `_extract_body`委派、抽取版本常量及当前两处抽取调用所需窄连接。不得顺手改HTTP/FMP/请求规范化/抓取预算/并发监督/URL验真/缓存写入/翻译。
- 新 `tests/test_du7_extraction_*.py`、`tests/fixtures/du7_extraction/`，自己的PWF/e2e/receipts。

旧tests/gold、N5产物收据、worker/supervisor、provider flags/config、CI/skill安装、FF/CWP及其他仓只读。不提交.env、日志中的key或不明已有WIP（原仓.workbuddy-ai、eval_results等不属于本卡）。

## 2. 接口与版本

保留旧 `_extract_body(page: bytes)->tuple[str,str]`端口及两条现有调用，不新增CWP parser或canonical speaker库。可在新纯模块提供含有限diagnostics的内部typed结果，再由旧端口按现错误合同映射；public /1、/2请求/响应字段不增加。

- 一次BeautifulSoup/DOM观察、原顺序、原语言，不翻译，不补写speaker/问题/答案的归属。
- 避免祖先与子节点双重发射；保留真正业务段落以及短结构标题/姓名。文本同样但真实出现在两个不同位置时不得全局按字符串去重。
- 广告/导航/赞助/脚本/隐藏内容按有证据的节点作用域过滤，不因业务正文出现advertisement、sponsored、cookie等词就整句丢弃。区分有明确容器的广告与普通业务描述。
- main缺失、歧义、结构破损按现失败/诊断合同诚实处理，不回退整个网页噪声，不“尽力”伪造完整transcript。
- Unicode/HTML entity/标点、业务数字、Q&A顺序保留；只做现有可解释的空白规范化。
- 实质改变提取结果必须使用诚实的新extraction_version，不能悄改`motley-fool-main-text/1`语义。保留旧v1冻结gold和原raw bytes/历史恢复入口，不重抽或覆盖既有原件。FMP的`fmp-api-content/1`不改。

A节点检查当前ET返回/产物及CWP lineage对extraction_version的真实接受合同。若新版本需要消费者更新，只提交具体ROOT接线清单，不写CWP/FF；纯组件/ET离线实际入口先完成，不新加人工许可JSON。历史已下载TXT在新抽取代码存在后仍复用，不重复下载。

## 3. A：冻结正确行为并复现共因

先查现README/PWF/git和实际 `_extract_body`、fetch两入口与existing fakeHTTP测试。旧PWF的W06 pending被d0ac42c实现取代，不把旧文字当待做任务。

在自己的新fixtures冻结业务句误删、嵌套重复、短标题三RED，再加旧正常小页面作兼容基线。保存title/body UTF-8 SHA和顺序，量值手工独立期望。不能仅凭import错误称完成A，不改旧fixture/gold。

## 4. B：一次DOM所有权实现及集中回归

新增测试至少覆盖：

1. 广告业务词正常保留，同时真广告容器删除；词置于句首/句中和中英文混合不走单例特判。
2. li>p、div>p、混合inline/strong/span、连续短块；只去结构重复，真实重复台词保留。
3. Q&A、CEO/CFO/Operator、短姓名/标题、英文标点/Unicode实体、原数字及章节次序。
4. main缺失/多main/空正文/脚本与hidden、畸形HTML、现最大响应字节/截止预算拒绝保持。
5. 两个实际fetch调用都委派同一个抽取owner、metadata版本匹配实际body；FMP提取和provider disabled状态完全不变。
6. 新旧抽取版本、历史raw TXT/JSON复用/hash、错误路径不写半个成功文件；不测试翻译或支付任何LLM。

复用仓内原pytest/HTTP fixture，在B集中跑实际相关旧测试，不重构全仓、删旧断言或调大预算。离线HTTP是fixture，不冒充真实网站触达。

## 5. C：公开入口和清理

自有e2e用临时下载目录、隔离配置、fake HTTP response走现fetch/CLI/结果校验，验证一次抽取、不重复抓已有TXT、原始payload SHA、新extraction_version、真实provider flags、失败文件清理。测试先有sentinel/旧v1文件，结束按字节恢复；原先没有的新下载/正文/receipt全部删除。

如能取得既有公开网页小样本，可额外做原DOM观察与抽取逐段独立试读，明确来源/SHA/取样范围。当前provider默认禁用，不为凑真实C更改生产策略；没真实网页则报告NOT_RUN/live页面适应性未验证，三个合成共因仍可独立交付。禁止伪造线上成功或拿公司现有TXT冒充网页DOM样本。

交付按共同handoff接口，另列新/旧提取版本兼容表、两fetch实际路径、默认disabled观察、CWP/FF需要同步的具体字段（如有）和真实范围限制。正常commit后冻结源码并STOP，ROOT统一并线/推送/跨仓验收。
