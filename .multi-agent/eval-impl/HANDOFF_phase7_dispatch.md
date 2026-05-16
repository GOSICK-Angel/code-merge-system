# Phase 7 派单全文（备用，下次会话直接 SendMessage 给 executor）

> 本文件保存上次会话发给 executor 的 Phase 7 派单全文，供下次会话续接时复用。

---

```
Phase 6 GO（commit 000d6464，cov 94.52%，3 项 carry-forward 闭环）。

继续 **Phase 7 — consistency.py**（估时 0.5d，详见 plan/FINAL.md §Phase 7 ll.278-288）。

## first action

1. Read .multi-agent/eval-impl/locks/approved-facts.md（[code-phase-6] 8 新事实 + Carry-forward 4 项）
2. Read .multi-agent/eval-impl/test/FINAL.md §8 Phase 7 用例 T7-C1..C5
3. Read scripts/eval/{_ground_truth,diff_against_golden,_schemas,run}.py 复用接口
4. Read .multi-agent/eval-impl/code/phase-6/FINAL.md 续接锚点

## gatekeeper-code 强制提示（必须做）

1. **顺手修本审 P2-1..P2-4**：
   - P2-1：assert ... is not None type narrow → 改成显式 if/raise（防 -O 剥离）
   - P2-2：缺指标 → SKIP 路径补显式 test
   - P2-3：_compute_rr glob 多匹配 → 显式选最新或报错
   - P2-4：yaml SRSR hard gate 隐式 SKIP → 显式 marker
2. **consistency.py 比对 N 次 runs 的 DET / CPC**（test FINAL §8 T7-C1..C5）
3. **比对维度：MergeState.file_decision_records[f].(strategy, target_risk_level)**（plan 决策 3）
4. **独立 CLI**（不挤压 lock argparse 互斥 [code-phase-1]）
5. **不触发 N 次 runs**（plan 决策 3：调用方 shell 循环）
6. **复用** [code-phase-2] _ground_truth / [code-phase-4] DiffReport schema / [code-phase-3] runs/ 目录约定

## 本 Phase 范围

- `scripts/eval/consistency.py`（≤ 250 行）：CLI `--runs <runs/>* --metric {DET|CPC}`
- `tests/eval/unit/test_consistency.py`：覆盖 ≥ 80%

## GO 条件

1. 输入多个 runs/，输出 DET / CPC 数值 + 不一致样本清单
2. 比对维度：MergeState.file_decision_records[f] 的 (strategy, target_risk_level)
3. mypy strict / ruff / fork-name-check / cov ≥ 80% 全绿

## commit message 建议

feat(eval): Phase 7 — consistency.py (DET/CPC)

复用 _ground_truth / DiffReport / runs/ 目录约定
N 次跑由调用方 shell 循环触发（不在本脚本范围）
carry-forward CLOSE: Phase 6 P2-1..P2-4

测试：pytest X/X PASSED, cov Y%

## 实施纪律

- 不修改 src/ / doc/evaluation/ / pyproject.toml
- 不引入新依赖
- 写报告到 .multi-agent/eval-impl/code/phase-7/v1.md
- 完成后 SendMessage 给 gatekeeper-code 含 commit hash
```
