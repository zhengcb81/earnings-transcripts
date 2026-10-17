# DU10 ET ROOT 独立接收审查

审查员 `/root/du10_et_reception_review`。交付 HEAD `cb8e29cddb3836fe670d93d65a02678f5ce8e466`；基线 `7d06777e250ee3f7ae58f0be2cee62944bd2c104`。结论 **REQUEST_CHANGES**：发现两项共用根因，应在 ROOT 隔离接收树修复并复核后再并线。

作者两轮独审实际绑定 `42310b7c69b34c1153cc45da401b67cc8c069804`。到最终交付 HEAD 仅四份 PWF 文件发生变化，源码与测试均无 delta，因此原 294 PASS、561 PASS、golden 与保全收据可复用。原独审记录的时序套件是 46 PASS、1 项负载下失败后隔离通过；不把它改写成首次全绿。本 ROOT 审查没有重复完整回归。

## 实际窄测

`PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider tests/test_du10_transcript_fidelity.py -q`：**10 passed in 1.20s**。

另以 `python -B` 从只读作者源码运行纯内存合成反例；没有读取生产全文、下载、API、LLM、作者索引或缓存写入。结束时作者 HEAD 不变且 `git status --porcelain` 为空。完整 SHA 与检查数据见同名 JSON。

## ET-ROOT-01 — P2：新增截断检测依赖最后一个 >，引号/注释会绕过

`_ends_in_unfinished_markup` 先 `rpartition(b">")`，没有 HTML 词法状态。构造含 335 字节正文的完整标题/main/p 前缀，分别追加以下尾部：

- `<p title="unfinished >`
- `<!-- unfinished >`
- `<p title="unfinished > CEO: outlook remains`

三者全部返回成功、只含此前正文并标 `/1`；尾部被静默丢掉。本卡要求截断失败，且作者新增代码宣称未完成标签不会产生貌似完整 transcript，因此这是新功能未覆盖的真实缺陷。普通 `<p title="unfinished` 正确拒绝，证实根因就在引号/注释中的 `>` 被当作真实结束符。

应使用线性 HTML 词法状态判定未完成标签、属性引号、注释和 rawtext；保留单 DOM 与旧 comparator，不引入另一套下载/全文抽取框架。至少加入上述三个反例，并验证完整 script/comment/属性值含 `<XYZ` 或 `>` 不被误拒。

## ET-ROOT-02 — P2：main 的直接/inline 正文仍会无声消失（基线既有，非本卡新回归）

`extract` 将 main 交给 `_collect_runs(main, None, ...)`，直接文本与其 inline `<strong>` 等子孙始终没有 owner。具体输入：`<h1>ACME</h1><main><p>[足够长的有效正文]</p><strong>CFO:</strong> Revenue outlook rises.</main>` 返回成功但 CFO 和展望全部丢失并标 `/1`；如果全部正文仅由 main 与 inline 标签承载，则 `empty_body`。通过 `git show 7d06777:transcript_extraction.py` 内存加载实测，基线同样丢失，不能将其归罪为 DU10 新回归。

这是本卡扩展 block-owner 保真能力时仍未补齐的共用根因。建议 main 自身作为合法的 fallback owner，保持 drop/filter 规则；测试直接正文、inline speaker、main文本与p混排及被过滤块。冻结 `_BLOCK_TAGS` 和 v1参考不动，新内容恢复后诚实标 `/2`。两个算法都漏掉同一内容而字节相等，不能证明语义保真。

## 误拒专项复核与边界

六个完整合法对照均接受且正文无变化：闭合 script 内 `x<XYZ`、script字符串内 `<XYZ >`、闭合 comment 内 `<XYZ` 或 `>`、闭合属性值内 `<XYZ >`、尾部转义 `&lt;XYZ`。未发现完整合法 HTML 的误拒实例。`</html>` 后裸 `<XYZ` 与未闭 script 含 `<XYZ` 被拒，均非完整合法页，符合作者已披露边界，不新增误报指控。

原独审有效部分可继续使用，但未覆盖上述两类反例。真实 MF HTML 泛化仍 NOT_RUN；中文原生语料缺失、FMP真实 JSON未测均已诚实披露，不能用合成 CJK 或TXT重新包成HTML来声称原站DOM已验收。收到 ROOT 修复后，只需对上述根因和相邻冻结契约作 delta 复核。
