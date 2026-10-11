# W06 ET cutoff 共用修复交付（作者冻结）

TREE：C:/Users/郑曾波/Projects/_harness_worktrees/w06/et-cutoff-root；HEAD前后c91f5f54bc2a24e7b5d6e648dba3f9d578f5fa52不变。未commit/merge/push/install。

## 行为与接口

只有 transcript_api.py 和两个责任测试改动。FMP已验证唯一晚call → not_found/candidate_count0/error_code=call_after_as_of；Motley已验证目标期晚published → publication_after_as_of。普通空列表、别期未来候选不冒cutoff。消息/结果版本和10个goldens不变；FMP publication_date=null/as_of_cutoff_verified=false不变。无公司/年份/fixture特判、无额外HTTP、正文/翻译/许可或身份库。

## TDD和证据

正规RED **5FAIL6PASS**，每项失败都是API/CLI真实not_found缺error_code，无import错误。集中 **56PASS40.37s**，10goldens matched，Ruff/diff clean；新worker测试两个provider、真实supervisor/worker/API，只有HTTP seam为fake，来源诊断和实际usage均到wire，无translator，scratch恢复。

中央证据：company-wiki/.planning/w06-cross-period-acquisition/cutoff-implementation/et/；before.json、red.*、green.*、golden.*、lint.*、git-*、product-freeze.json。3产品SHA与12个旧golden目录文件保存。资源：合成HOME/TEMP/fake key/socket deny，0真实网络/LLM/费用，0真实keys/config/raw读取。

## ROOT Next Step

使用父目录统一 product-freeze.json 的ET+FF树与ROOT已验CWP树，原41矩阵/CaseSpec/期待不变在180s内只回放一次；之后独立验收、正常并线发布。工具205冻结、CWP12、其它工作树零写。本作者冻结STOP；不把56unit/integration PASS说成41完整产品认证或真实FMP权益。
