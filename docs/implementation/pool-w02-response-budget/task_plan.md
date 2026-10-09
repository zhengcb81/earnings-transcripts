# W02 operation response-budget root fix

Status: in_progress. Isolated ET branch codex/pool-et-response-budget-20261009, base282e890. Root W02 card is read-only; no FF, install, main/raw/config/key writes.

1. RED: explicit optional independent response/request quota, actual oversized chunk consumption, padded JSON, exact boundary, multi-request Motley, injected and supervised paths, cleanup/deadline/unknown receipts.
2. Implement operation max_response_bytes without raising provider per-response hardcaps; canonical body remains separate. Reuse existing meter and supervisor; record observed chunk before raising.
3. GREEN: new responsibility suite and four existing focused pytest modules, real normal tool entry under local worker HTTP launcher, no suppliers. Card unittest command is inapplicable to these pytest functions/fixtures; never accept zero-test green.
4. README/protocol and small replay driver; short owned TEMP restored in finally; scoped local commit then handoff. Root integrates FF field only and controls push/merge.

Next Step: write and run RED before runtime changes.

## MAIN接手完成（2026-10-09）

前agent中断，MAIN完成131责任复验、三仓18检查点、静态和交接。当前Next Step：精确commit并线，不重跑每个小节点。
