# 进度

- **A 完成**：实测确认根因（深度200/1200/2400 文本片访问 20,303/721,803/2,883,603
  二次增长，节点 409/2,409/4,809 线性；2,403 次逐块 get_text），冻结v1/现v2
  语义基线逐项记录于 findings.md；行为 RED 5 failed / 152 passed
  （receipts/a_first_behavior_red.txt）；oracle 锚点测试（矩阵24例、双fixture、
  120个种子生成样本、双侧覆盖）随 RED 先绿。
- **B 完成**：新增纯模块 `legacy_projection_comparison.py`（共享单遍证据 + 流式
  全局SKIP匹配 + 长度短路逐字符确认），`transcript_extraction.extract` 薄接线
  （冻结 `_legacy_v1_reference` 函数体 sha256 `85d1d45c…` 与基线一致，仅保留为
  独立基准）；`motley-fool-main-text/1` 常量 5 字符段落阈值显式命名为
  `LEGACY_MIN_PARAGRAPH_CHARS`。提交 fe53906（RED）、30cc016（实现）。
- **C 主体完成**：真实公开入口 E2E（`fetch_transcript_candidate` 假HTTP seam：
  单次DOM构建、有界比较、数字不变、/1与/2双侧标签；真实 scraper 入口 TXT 复用 +
  输入/配置 SHA+mtime 不变 + 失败清理零半文件）；算法计数收据
  receipts/c_e2e_cost_evidence.json（15形状，访问≈节点，墙钟仅作参考）。
  提交 36c9b38。
- **独立审查第1轮 FAIL（R1-1 高 / R1-2 中 / R1-3 低）**：判定奇偶性正确但
  丢弃链形状上比较器仍 Θ(深度²)。已根因修复（证据合并遍历，见 findings.md），
  补 12 个丢弃链代价测试；修复后 du9 205、DU7/ROOT 58、集中回归 170、
  golden 10/10 全绿。**同一独立审查agent复核进行中**；PASS 且零未解决后
  才标 READY/交接。
