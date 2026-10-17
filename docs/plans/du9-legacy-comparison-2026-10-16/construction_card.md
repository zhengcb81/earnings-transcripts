# DU9-ET-LEGACY-COMPARISON：移除兼容比较的病态重复遍历

工作目录：`C:/Users/郑曾波/Projects/earnings-transcripts-du9-legacy-comparison`。ET已发布main基线`0cb71731cef6c6b3582f595d44f5b8a077392ac1`，不要在旧DU7作者树施工。PLAN_ID=du9-legacy-comparison-2026-10-16。

## 已证实根因

DU7和ROOT已将主body收集改为一次DOM/迭代栈、修误删业务和inline数字空格；228项和10gold已验收，99隔离测试/7跨cwd通过。但extract末尾为判/1或/2仍调用冻结_legacy_v1_reference：每个嵌套block再次get_text，且构建旧重复正文。即使主walker线性，整个提取仍可因合法深嵌套出现二次时间/空间。该限制已在ROOT接收报告明确OPEN。

目标是让**生产版本兼容判断**复用同一次DOM，以有界额外工作比较旧输出是否等于当前title/body，不构造整份旧重复正文；不改真实body，不降低旧兼容准确度，不以提高递归/响应/时间上限解决。

## 排他白名单

- transcript_extraction.py（仅legacy比较及必要薄接线；主提取/过滤语义保持）。
- 必要一个新纯 legacy_projection_comparison.py。
- 新 tests/test_du9_legacy_comparison.py 与 tests/fixtures/du9_legacy_comparison/。
- 自己 docs/plans/du9-legacy-comparison-2026-10-16/。

旧gold和_legacy_v1_reference函数体保留作独立基准，不修改其行为来迁就新比较器。provider/API/supervisor/parallel scraper/common/config/翻译/模型/response budget/文件落盘/默认关闭Motley完全只读。CWP/FF及DU8不进入。

## 实施细则

1. A阅读原作者/ROOT PWF和实际函数，统计嵌套p/h/li多层重复文本访问/legacy materialization，构造有效小输入与200/1200/2400层/大量兄弟block的行为RED。期望锚点从冻结v1和现v2分别独立生成，不能只互相比较新函数。
2. 只需回答legacy tuple是否等于现title/body；可以对比较长度/字符/分隔符证据早停，不能把“没有算完”伪装equal。可用同DOM事件的流式比较/一次摘要后精确确认，hash碰撞不能宣称字节同等。禁止第二次BeautifulSoup.parse、逐block反复get_text、保存全部重复字符串。
3. 保持旧h1/main选择、get_text(separator=' ',strip=True)、SKIP_TEXT/短段/换行/嵌套重复等**真实旧语义**。旧v1本来缺title/短正文等返回None也要同等。当前新body的inline邻接规则绝不能退回旧插空格。
4. extraction_version必须与旧精确比较的/1或/2一致，已有正常10gold的文本/label/diagnostics不变；不能为了速度一律标/2、跳过复杂输入或删主body。冻结旧参考仅用于可控测试和基准，不在生产大输入后备重跑。

## A/B/C验证

B覆盖混合inline/中文数值/负号/%/实体/NBSP/BR/嵌套段落和列表/隐藏/广告/UI业务近似词/重复同文/多h1与main、注释/异常DOM。对有界生成样本逐一验证新比较==(冻结v1==(实际v2title,body))，包含等于和不等于双侧；旧10gold及DU7/ROOT相关集中套件原样。

C用真实extract公开入口及实际ET API/已有TXT复用/失败清理的本地fake HTTP seam，0外网/0翻译；计数DOM构建次数、节点访问、额外比较字符和峰值中间材料，证明大嵌套不会物化旧重复正文且数字不变。用算法计数+相邻规模，不靠单机秒数作为唯一PASS。C库DOM构造、整个HTTP supervisor硬限额的未测范围写清，不能把纯比较线性写成全系统硬实时。

必须按INTERFACES调用独立审查agent全面审查最终差异/兼容/效率和所有测试，所有发现修复且复核PASS后交接。正常commit，不改主线或技能安装，不推远端main；最终报告完整SHA、独审报告和ROOT回归/并线步骤，冻结STOP。
