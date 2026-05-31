# Test Plan v1 — Gatekeeper Review

> **审查者**：gatekeeper-test（team eval-impl）
> **日期**：2026-05-15
> **被审件**：`.multi-agent/eval-impl/test/v1.md`
> **基线**：`plan/FINAL.md` v2 + `locks/approved-facts.md` + `decisions/scope.md`（Phase 0-9 全量）+ `doc/evaluation/{procedure,acceptance,metrics}.md`
> **结论**：**NO-GO（要求修改）**
> **本轮统计**：P0 × 4，P1 × 5，P2 × 4

审查要点按 5 项展开 → 缺陷列表 → 修订建议。

---

## Step 1 — 锁清单事实核查

锁清单 `[plan]` 段全部 9 条事实在测试方案中均被引用且无冲突：

- 产物名 `merge_report_<run_id>.{json,md}` / `plan_review_<run_id>.md` / `checkpoint.json` —— 在 T3-R1 / T4-D6 / T4-D7 / 表 §11 全部正确使用，无 `merge_state.json` 残留 ✓
- `MERGE_DEV` 而非 `MERGE_HOME` —— T0-C1 / T0-X1 / T3-R2 全部对齐 ✓
- per-file 来源 `merge_report_<run_id>.json`，stdout `ci_summary.json` 仅 sanity-check —— T4-D6 显式守护此契约 ✓（**唯一一处明文契约守护，质量好**）
- Memory 是 SQLite 单文件 `<cwd>/.merge/memory.db` —— 表 §11 提到，但 T3 用例并未显式断言此路径，见 P1-3
- `merge` CLI 无 `--no-cache` —— 测试方案未尝试调用，无回退风险 ✓
- CI `tests/eval/` 必须显式 step，独立 `--cov=scripts/eval` —— T9-W1..W3 静态校验 yml ✓

**无 regression**。

---

## Step 2 — 覆盖率审查（按 plan GO 条件 + acceptance.md hard gates 双向核对）

### 2.1 Plan GO 条件覆盖

逐 Phase 比对 plan FINAL.md 的 GO 条件清单与 test §11 trace 表：

| Phase | GO 条件 | 测试覆盖状态 |
|---|---|---|
| 0 | §1 三个测试文件通过 | ✓ |
| 0 | §2 mypy 通过 | ✗ 未单独测；属 CI 静态校验 |
| 0 | §3 ruff 通过 | ✗ 同上 |
| 0 | §4 fork-name-check 退出 0 | ✓ T0-F1 |
| 0 | §5 eval_subprocess_env 单测 | ✓ T0-C1 |
| 1 | §1 `--verify` 退出 0 / 篡改非 0 | ✓ T1-L2 / T1-L4 |
| 1 | §2 `--update` 写三份 lock | ✓ T1-L1 |
| 1 | §3 acceptance_thresholds.yaml 缺失 warning（不 fail）| ✓ T1-L5 |
| 2 | §1-2 prepare 展开 4 件 | ✓ T2-P1 |
| 2 | §3 三类异常 | △ T2-P4（损坏 patch）+ T2-P5（缺 golden）+ T2-P6（lock 不一致）—— 三类都覆盖 ✓ |
| 3 | §1 7 件产物 | ✓ T3-R1 |
| 3 | §2 三层隔离 | △ env ✓（T3-R2）；cwd ✓（T3-R3）；HOME ✗ **未直接测子进程 env["HOME"] 是否=workdir/home** |
| 3 | §3 `<cwd>/.merge/memory.db` 不存在断言 | ✗ "隐含在 T3-R3" 不可接受，见 P1-3 |
| 3 | §4 concurrency + summarize 头部标注 | ✓ T3-R4 + T5-S3 |
| 3 | mypy strict | ✗ 静态 |
| 4 | §1 schema 严格 + 扩展字段 | ✓ T4-D5 + T0-S2 |
| 4 | §2 per-file 数据来源 | ✓ T4-D6（守护契约，质量好）|
| 4 | §3 fallback engine 标注 | ✓ T4-A1 + T4-D9 |
| 4 | §4 4 label 各 1 用例 | ✓ T4-D1..D4 |
| 5 | §1 6 章节齐 | ✓ T5-R1 |
| 5 | §2 9 指标 | △ 列举的 9 个指标**漏 SRSR**，见 P0-1 |
| 5 | §3 concurrency>1 头部标注 | ✓ T5-S2 / T5-S3 |
| 5 | §4 失败案例按 sample_id 排序 | ✓ T5-S5 |
| 6 | §1 输出符合 procedure §3.3 | ✓ T6-G3 + T6-G4 |
| 6 | §2 退出码三态 | ✓ T6-G1 / T6-G5 / T6-G6 |
| 6 | §3 `lock.py --verify` sha 同步检查（warning vs CI error）| ✓ T1-L5 / T1-L6 |
| 6 | §3 `--update-acceptance-sync` 子命令"仅更新 sha 不改 thresholds" | ✗ 缺，见 P1-1 |
| 6 | §4 hard / soft / 全绿三路径 | ✓ T6-G1 / T6-G5 / T6-G6 |
| 7 | §1-3 DET / CPC + 不一致清单 | ✓ T7-C1..C5 |
| 8 | §1 e2e PASS | ✓ T8-E1 |
| 9 | §1 PR test job 时长不增加 > 30s | ✗ §15 仅自评 ≈18s，无回归测试，见 P2-2 |
| 9 | §2 unit 全过 + 覆盖 ≥ 80% 独立 cov | ✓ T9-W2 |
| 9 | §3 `eval-tier1` manual job 可触发 | ✗ 缺，见 P1-2 |
| 9 | §4 不阻塞 PR | ✗ 缺，见 P2-3 |
| 9 | 5 个 step 必须新增 | ✓ T9-W1 |

### 2.2 acceptance.md Hard Gates 反向核对

acceptance.md §1 共 9 个 hard gate：WMR / SSER / DCRR / **SRSR** / MMR / WDR / Recall_Mi / RR / RCR

测试方案 T5-S1 列出的 9 项是 `OA / WMR / MMR / WDR / SSER / DCRR / RR / RCR / Recall_M1..M6`：

- ❌ **缺 SRSR**（hard gate「快照回滚成功率」），plan 阶段也没提
- 其余 8 个 hard gate 都覆盖到 summarize 输出
- 9 个 soft gate（OA / CRA / Over-escalation Rate / JA / DET / CPC / cost_p95 / wall_time_p95 / plan_revision_rounds_p95）—— 仅 OA / DET / CPC 在 summarize / consistency 测试中出现；其余 6 项缺失

→ 见 **P0-1**（SRSR）+ **P1-4**（剩余 soft gates 至少需 schema 测试）

### 2.3 用例数账目核对

测试方案摘要 §0 声称 "33 unit + 2 integration = 35"，但分节实际列出的用例累计：

```
P0:  T0-C1..C10 (10) + T0-S1..S10 (10) + T0-F1..F4 (4) + T0-X1 (1)  = 25
P1:  T1-L1..L6                                                       =  6
P2:  T2-G1..G3 (3) + T2-P1..P6 (6)                                   =  9
P3:  T3-R1..R7                                                       =  7
P4:  T4-A1..A7 (7) + T4-D1..D9 (9)                                   = 16
P5:  T5-R1..R2 (2) + T5-S1..S5 (5)                                   =  7
P6:  T6-G1..G7                                                       =  7
P7:  T7-C1..C5                                                       =  5
P9:  T9-W1..W4                                                       =  4
unit 合计                                                             = 86
integration                                                            =  2
TOTAL                                                                  = 88
```

→ 见 **P0-2**（摘要表与正文相差 53 条，文档严重失实，影响审计）

---

## Step 3 — 测试金字塔

正文：unit 86 / integration 2 / e2e 0 → 比例 **97.7% / 2.3% / 0%**

参考推荐 **60/25/15**。本场景脚本侧偏纯函数 + CLI，可适当下移 e2e 比例，但当前 integration 仅 2 条覆盖整链 + 整链失败传播两条，金字塔头部过尖。

→ 见 **P2-1**（e2e 套件单薄）

---

## Step 4 — 可执行性 / mock 边界

- 多数用例三要素齐全 ✓
- T0-C9 期望「`FileNotFoundError` 或 `OSError`」—— 测试断言不应给"或"，需收敛为单一确定异常类型，否则不同实现策略下都通过 → **P1-5**
- T1-L5 / T1-L6 / T6-G7 / T7-C4 等 stderr 字符串断言用了不带边界的子串匹配（如 "synced_with_sha mismatch"），易被错误信息措辞改动而误绿/误红，但本轮不强求改 → **P2-4**
- T6-G1 期望"output `eval_acceptance.json.verdict == "PASS"`"——文件名缺时间戳模板（procedure §2.5 是 `eval_acceptance_*.json`），需补 glob 或 `--output` 显式注入路径 → **P2-4**（同条）
- fixtures § 12.1 设计清晰；mock 优先级 §12.2 / mypy 兼容 §12.3 描述充分 ✓

---

## Step 5 — 失败场景 / 冗余

失败：12 条覆盖参数校验、文件缺失、sha 不一致、损坏 patch、权限失败 ✓
冗余：T7-C1 与 T7-C2 都测 DET，建议合并或明确各自独立验证目标（T7-C1 测全等 1.0；T7-C2 测部分不等 + 清单），目前可接受。

---

## 缺陷清单（P0/P1/P2 分级）

### P0（必须修复才放行）

#### P0-1：T5-S1 9 指标列表漏 SRSR（plan 阶段就漏，本测试未补救）
- **位置**：v1.md §6.2 T5-S1，line 459；§11 trace 表对 acceptance.md §1 hard gate 反向覆盖
- **问题**：acceptance.md §1 共 9 个 hard gate，包含 `SRSR`（快照回滚成功率，metrics.md §3.3 定义）；plan FINAL.md §0/§1/§2 也未提 SRSR；测试方案承袭此缺口
- **要求**：在 T5-S1 期望中补 SRSR；并新增一条用例（建议 T4-Dx 或 T5-Sx）覆盖 `merge_report_<run_id>.json` 中 snapshot rollback 字段→ summary 的提取链；如 plan 未定义来源字段，必须先 SendMessage team-lead 触发 plan v3 补 SRSR data flow
- **绑定 fact**：acceptance.md §1 hard gate 表

#### P0-2：摘要 §0 与正文用例数 53 条不符
- **位置**：v1.md §0（line 19-34）vs §1-§10
- **问题**：摘要表声称 "33 unit + 2 integration = 35"，正文实际 86 unit + 2 integration = 88；差 53 条 unit
- **要求**：以正文为准重写 §0 矩阵表；同步修正 §15 执行预算（按 86 unit 重算 wall time 预算）；同步 trace 表 §11 完整度核对
- **理由**：该数据是 gatekeeper / executor / CI owner 后续验收的 baseline，失实直接动摇审计

#### P0-3：Phase 3 GO §3 关键隔离断言"隐含"不可接受
- **位置**：v1.md §11 trace 表 line 632："Phase 3 GO §3（memory.db 不存在）| 隐含在 T3-R3"
- **问题**：plan FINAL.md Phase 3 GO §3 明文要求"每 sample 跑前 `<cwd>/.merge/memory.db` 不存在"，其立项动机是防止评估 run 串污染 memory（锁清单 [plan] Memory 形态事实就是为防此类回退）；"独立 cwd 自然不存在"是推理而非测试断言，如未来 run.py 内部不慎复制 memory.db / 隔离逻辑回退，**本测试套件无法捕获**
- **要求**：在 T3-R5 之前新增 T3-R8（正常）：
  - 输入：fake merge 在 cwd 下创建 `.merge/memory.db` 后退出 0；run.py 跑 2 个 sample
  - 期望：第 2 sample 启动前 run.py 显式调一次 `_assert_clean_memory(<cwd>/.merge/memory.db)`，断言文件不存在；如存在则抛 `MemoryLeakDetected`
  - 断言锚点：第 2 sample run_meta.json 含 `memory_clean_check: "passed"`
- **绑定 plan**：Phase 3 GO §3 + 决策 2 §3
- **绑定 fact**：approved-facts.md `[plan]` Memory 形态

#### P0-4：T6-G6 soft gate 退出码用例与 acceptance.md 不一致
- **位置**：v1.md §7.1 T6-G6 line 502
- **问题**：T6-G6 用 `OA=0.80`（soft 阈值 0.92）期望退出码 2 + verdict==NEEDS_REVIEW。但 procedure.md §2.5 定义"`2` —— soft gate 退化但未到红线"——"未到红线"暗示需要"基线对比"才能算"退化"；当前 T6-G6 没有 `--baseline` 参数（gate.py 签名含 `--baseline`，见 plan §5），单跑当前 report 不构成"退化"
- **要求**：明确 T6-G6 输入是否含 baseline；若 acceptance gate 设计为「绝对阈值」+「相对退化」二维判定，需补一条用例区分；若仅看绝对阈值，需修改预期描述为"soft 阈值不达标即 NEEDS_REVIEW"并在 plan 阶段确认 gate.py 语义；**该歧义未澄清前 Phase 6 实现会无所适从**
- **建议**：先 SendMessage team-lead 同步 plan-side 决策，避免 Executor 走偏

### P1（强烈建议修复）

#### P1-1：缺 `lock.py --update-acceptance-sync` 子命令测试
- **位置**：v1.md §2.1 / §11
- **问题**：plan §5 命令清单含 `python scripts/eval/lock.py --update-acceptance-sync`，决策 7 详述其语义"仅更新 yaml 中的 `synced_with_sha` 与 `synced_at`，**不**改 thresholds 字段"。测试方案完全未覆盖。这是防止脚本误改阈值的关键护栏，**必须有用例守护**
- **要求**：补 T1-L7（正常）+ T1-L8（失败）：
  - T1-L7：先手工改 yaml.thresholds，再跑 `--update-acceptance-sync`，期望 yaml.thresholds 字段保持不变 + `synced_with_sha` 更新到 acceptance.md 当前 sha
  - T1-L8：测试 `--update-acceptance-sync` 与 `--update` 互斥（不能同时使用），或确认 plan 是否允许联合使用

#### P1-2：缺 CI `eval-tier1` manual job 静态校验
- **位置**：v1.md §10.1
- **问题**：plan Phase 9 GO §3 要求"`eval-tier1` job 可手动触发并跑完"；plan §0 明确该 job 含 `workflow_dispatch` + `schedule: cron: '0 18 * * *'` placeholder。T9-W1..W4 均覆盖 `test` job 内的 5 step，未校验该 job 存在
- **要求**：补 T9-W5（正常）：`yaml.safe_load` 后断言 `jobs.eval-tier1` 存在；含 `workflow_dispatch` trigger；含注释"nightly placeholder, not blocking"

#### P1-3：T3-R3 cwd 隔离测试不验证 `<cwd>/.merge/memory.db` 真实路径
- **位置**：v1.md §4.1 T3-R3
- **问题**：当前断言 `cwd 路径 parent.parent == workdir/runs/<id>`，未触及 `<cwd>/.merge/memory.db` 这个具体路径锚点；锁清单 [plan] 第二段"Memory 形态"明确该路径；测试需 explicitly anchor on this path 以防 cwd 隔离层次未来改动后没人注意 memory 仍按真实路径写
- **要求**：T3-R3 补一条断言：fake merge 跑完后 `<workdir>/runs/<id>/_cwd/.merge/memory.db` 不存在（或仅在 fake merge 显式创建时存在）

#### P1-4：剩余 6 个 soft gate 完全未在 schema 层覆盖
- **位置**：v1.md §6 / §7
- **问题**：acceptance.md §2 共 9 个 soft gate；测试仅覆盖 OA / DET / CPC（且 OA 仅在 T6-G6 通过 yaml 阈值修改间接出现）；CRA / Over-escalation Rate / JA / cost_usd_per_run / wall_time_seconds / plan_revision_rounds_p95 缺失
- **要求**：在 `_schemas.py.AcceptanceThresholds` 测试中（T0-S4）追加断言 yaml schema 必须支持上述 6 个 id 的 entry；T6-G* 至少补 1-2 条覆盖 soft gate 路径中除 OA 外另一个指标的执行路径
- **理由**：避免 Executor 实现 schema 时漏字段，造成后续 acceptance 阶段报错

#### P1-5：T0-C9 期望「`FileNotFoundError` 或 `OSError`」收敛
- **位置**：v1.md §1.1 T0-C9 line 92
- **问题**：测试期望"或"逻辑使断言对实现选择不敏感
- **要求**：根据 `_common.write_json` 的设计明确选定 `FileNotFoundError`（路径不存在场景符合 stdlib 习惯）或在 plan 决策 1 补一条"_common.write_json 在 parent 不存在时抛 FileNotFoundError"明文契约后再固化测试

### P2（建议修复，可后置）

#### P2-1：Integration / e2e 比例偏低
- **位置**：v1.md §0
- **问题**：86/2 ≈ 97.7% / 2.3%，远离推荐金字塔；仅 T8-E1 覆盖整链 happy path，T8-E2 仅测损坏 patch 拦截
- **建议**：补 1-2 条 e2e：DET / CPC 完整流（procedure.md §4 命令链）；或在 T8 加"Tier-3 M3 sample 端到端 verdict==FAIL"覆盖失败链路

#### P2-2：CI 时长回归测试未实现
- **位置**：v1.md §15
- **问题**：plan Phase 9 GO §1"PR 时长不增加 > 30s"仅靠 §15 自评 ≈18s 满足；无自动断言；如未来添加慢测试触发越界，CI owner 才会发现
- **建议**：补 T9-W6（meta），用 `time` 包装 unit 套件并断言 ≤ 25s（留 5s 冗余）

#### P2-3：Phase 9 GO §4「不阻塞 PR」未直接测试
- **位置**：v1.md §10
- **建议**：T9-W7（meta）断言 `eval-tier1` job 不在 `branch protection` 必填 checks 列表（如能从 `.github/CODEOWNERS` 或 settings 反推）；或退而求其次在 `eval-tier1` job 加 `continue-on-error: true` 并测此字段存在

#### P2-4：错误消息断言收敛
- **位置**：T1-L5 / T1-L6 / T6-G7 / T7-C4 等
- **建议**：所有 stderr 断言改为：(a) 退出码精确匹配；(b) stderr 含"错误代号"（建议 plan 引入 error_code，例如 `EVAL_E001`-`EVAL_Exxx`）而非自然语言子串；本轮可暂以 P2 处理，下版收敛

---

## 修订建议（针对 verifier）

按以下顺序修订：

1. **P0-2** 先修：摘要 §0 与正文一致，否则后续每条 P0/P1 修订都会再扩大差异
2. **P0-4** 提前与 team-lead 同步：gate.py soft gate 是否依赖 baseline 是 plan-level 决策，verifier 单独修无效
3. **P0-1 + P0-3** 是契约守护类：直接补用例
4. **P1 系列**：按编号补用例 + 表 §11 同步 trace
5. **P2 系列**：可在 v3 处理

修订完成后，统一在 v2.md 顶部加变更摘要表。

---

## 通过标准（gatekeeper-test）

P0 全部修复 + P1 至少修复 4/5 即可放行（其中 **P1-1 必修**：`--update-acceptance-sync` 是关键护栏）。
