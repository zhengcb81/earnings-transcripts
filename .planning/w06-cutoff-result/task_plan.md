# W06 ET cutoff result 共用修复

PLAN_ID=w06-cutoff-result；独占 TREE et-cutoff-root，base c91f5f54。
诊断依据：canonical CWP .planning/w06-cross-period-acquisition/cutoff-diagnosis/report.md。
写集仅 transcript_api.py、tests/test_transcript_api.py、tests/test_retrieval_cli_e2e.py、本文3PWF与自身handoff；FF由并行作者处理。

## 接口与函数决定

result /1、/2、discovery /1和成功goldens不变。合法目标期记录因日期过晚排除仍 not_found/candidate_count=0，optional error_code=call_after_as_of（FMP）或 publication_after_as_of（Motley）；普通空结果不填cutoff。无新增reason同义字段或日期公开字段。call_date不冒充publication，原publication unknown不变。
FMP在现有实际身份/期间/日期验证、唯一性判断后记录cutoff原因，不取消多候选歧义。Motley exact fetch/discover共用listing selection：_candidate_urls验证身份后先筛目标期，再比较published_date；返回eligible与真实excluded标记；batch listing继续复用已有_dict接口，原近期N语义不变。不追加HTTP、正文、许可或第二身份库。supervisor/tool透明传递不修改。

## 三个大节点

- A：先正规业务RED。强化已存在两个cutoff测试，加入empty/边界日/晚一天/错symbol-quarter+晚日期/多候选与Motley fetch-discover别期未来控制；CLI真实worker原因+usage/cleanup。不得缺文件/importerror冒RED。
- B：实现上述共用选择和最小诊断；一次ET+FF责任package、success golden --check、相关lint/diff。仅为具名剩余风险扩查。
- C：冻结所有本包产品SHA、已有golden和其他工作树变动；独立owned短scratch恢复。ROOT原41矩阵回放、独立验收和发布，本作者不跑41、不commit/merge/push/install。

## 资源与恢复

真实HTTP/LLM/费用0；假HTTP、合成HOME/TEMP/key、socket拒绝；不读生产config/key/raw、不复制真实配置。旧40/1及diagnosis/freeze不覆盖。短scratch仅删除自有绝对目标并确认在workspace .tmp内；已有文件/空目录保留。

A业务RED、B共用实现/集中责任节点、C作者冻结均完成；仅ROOT原41矩阵/非作者验收/发布未执行。Next Step：ROOT使用中央统一product-freeze.json的ET/FF树，原CaseSpec/fixture/期待不变做一次41/180s回放，再正常发布；本作者STOP。
