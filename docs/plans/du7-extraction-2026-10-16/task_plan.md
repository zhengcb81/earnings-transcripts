# DU7-ET-EXTRACTION

状态：READY；工区 `C:/Users/郑曾波/Projects/earnings-transcripts-du7-extraction`。源码基线 `d0ac42c60dcca992db78860b8469cfed3dd527e5`，分支 `codex/du7-et-extraction-20261016`。
PLAN_ID：`du7-extraction-2026-10-16`；PWF_PLAN_ROOT：`docs/plans`。

先读本目录 [施工卡](construction_card.md)、[接口](INTERFACES.md)、findings/progress，再开始A。
本卡现在即可独立启动，不等DU6或ROOT下一轮源码。只改施工卡白名单，ROOT负责合流。

## A：调查、冻结接口、行为RED

Status: pending

按卡真实代码与期望，保留旧正确行为，区分合成/真实/未运行。

## B：所属层共因实现和集中测试

Status: pending

先测试后实现，不增加预算/权限/身份库；不改旧断言、跨仓或兄弟范围。

## C：实际入口E2E、清理和交接

Status: pending

只读原件，独立Temp/sentinel，成功/失败均恢复；正常commit后提交handoff.md/json和收据，冻结源码并STOP。

## Next Step

执行A；只在A/B/C大节点集中检查，不增加小节点人工许可或每commit全仓测试。
