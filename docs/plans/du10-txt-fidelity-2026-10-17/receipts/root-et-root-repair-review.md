# DU10 ET ROOT 修复独立 delta 复核

审查员 `/root/du10_et_reception_review`；结论 **PASS，零未决问题**。仅复核 ROOT 两处根因修复与新增测试，不重复作者完整回归。

受审工作树：`C:\Users\郑曾波\Projects\earnings-transcripts-du10-root-reception`，HEAD `8ae2f5633bf0fa13aea19645fc2d69784956bb01`。源码修复尚未提交，复核绑定实际文件字节：

- `transcript_extraction.py`：`c6c7a0d4d5e411b616bc3a90a16158183ba83977cb6ac92fb14e1e73da591abd`
- `tests/test_root_du10_fidelity_boundaries.py`：`fb75e0cad7fec4230d17b1081a83553b0bee5690fb7bcc60783ea0b83b0f0842`

ROOT 后续提交时只要上述字节不变，可在收据回填提交号，无须为了文档自引用再开一轮审查。

## 修复判断

ET-ROOT-01：标准库 HTMLParser 在 close 之前保留未完成词法输入，替代按最后一个 > 分隔。独立反例中的单/双引号、多 >、未闭注释和未结束 PI 均正确抛出 `truncated_markup`；完整 script/style/comment/带尖括号属性均正常接受。

ET-ROOT-02：main 自身作为 fallback owner，直接正文和 inline speaker 得以保留；混合 p/直接文本顺序、隐藏容器过滤均正确；恢复新正文诚实标 `/2`，旧 v1相等页仍标 `/1`。冻结 block集合、v1参考与 comparator 均未修改。没有翻译、下载、provider 或预算接口修改。

## 实际独立验证

以 `python -B -` 从 ROOT 接收源码运行 **15 个独立控制，15 PASS**：引号/注释截断五例（含未结束PI），完整 script/style/comment/属性四例，literal/entity两例，main inline-only/hidden/mixed三例，legacy相等一例。另两项边界探针：未知声明之后未闭属性正确拒绝；未闭 script 后普通 rawtext 可接受，如下限度所述。

ROOT 已执行的 304 PASS/80.05s 作为 ROOT 回归收据复用，本审查没有重复完整回归。初版独立PI控制误把“PI中已有 >”当作未闭属性；HTML PI以 >结束，不采用属性引号规则。已改为真正无 > 的未闭PI，修正的是审查测试期望，无需改生产代码。此中间过程保留在 JSON 中。

## 成本与未作出的保证

新增预检会先对整份输入进行一次 Latin-1解码和标准库 HTMLParser词法扫描，然后再由 BeautifulSoup解析。**仍只构建一次 DOM，但不再是只 tokenize 一次**；额外有一个随输入大小增长的解码缓冲及正常输入遍历成本。本次不测性能，也不宣称所有畸形输入的最坏时间复杂度或零额外空间。

这是一项未完成标记检测，不证明整页、所有元素或每句话完整。未闭 script 后普通 rawtext（例如 `const marker="<XYZ >";`）仍可接受；没有 business正文丢失的本探针不构成新增阻塞，原先“纯文本句中截断不可判定”界限继续成立。若后续需要严格拒绝所有未闭 rawtext，须另立明确行为期望，不能把此 PASS 描述为已经实现。

作者工区未写，接收树源码/测试亦未由审查员修改；无生产全文读取、网络/供应商/LLM、bytecode/cache写入。只写 ROOT 自有本报告及 JSON。
