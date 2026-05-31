# Test Plan v2 — Gatekeeper Review

> **审查者**：gatekeeper-test（team eval-impl）
> **日期**：2026-05-15
> **被审件**：`.multi-agent/eval-impl/test/v2.md`（v2 + 内部 v2.1 增量段 §A.1）
> **基线**：v1-review.md（P0×4 / P1×5 / P2×4）+ 上轮 v1+增量补丁的审查（已重命名为本审查，原 P0×5 / P1×7 / P2×3）+ plan/FINAL.md v2 + locks/approved-facts.md
> **结论**：**NO-GO（要求修改）**
> **本轮统计**：P0 × 2（关键失败）, P1 × 2, P2 × 1
> ⚠️ **触发 3 轮告警协议**：P0-2（用例数失实）已连续 3 轮未根治；本轮内部又新增 §A 92 / §0 97 / §A.1 100 三套数字。同步 SendMessage team-lead 报告。

---

## Step 0 — 上轮缺陷修复审计

### 上轮 v1-review 清单（P0×4 / P1×5 / P2×4）

| 缺陷 | 修复状态 | 证据 |
|---|---|---|
| P0-1 SRSR | ✅ 实质修复 | T4-D10 + T0-S2 5 字段 + T0-S4 含 SRSR id + T5-S1 anchor + T6-G1 + TR7 follow-up；并 ping team-lead 触发 plan v3 |
| P0-2 用例数 35 vs 88 | ⚠️ **回归扩大**：内部三套数字打架（详见本轮 P0-1） | §A 92 / §0+§0a+§15 97 / §A.1 100 |
| P0-3 memory.db 隐含 | ✅ 实质修复 | T3-R8 显式正反两路 + T3-R3 强化锚定 |
| P0-4 baseline 语义歧义 | ⚠️ **宣告未落地**（详见本轮 P0-2） | §A.1 表宣告 v2.1 + 方案 C；正文 T6-G6/G8 未重写、T6-G9/G10/G11 缺失、§16.2 未升级 |
| P1-1 `--update-acceptance-sync` | ✅ T1-L7 + T1-L8 |
| P1-2 eval-tier1 manual job | ✅ T9-W5 |
| P1-3 T3-R3 锚 memory.db | ✅ |
| P1-4 6 soft gate schema | ✅ T0-S4 + T6-G2 (CRA) |
| P1-5 T0-C9 收敛 | ✅ |
| P2-1 e2e 单薄 | ✅ T8-E3 |
| P2-2 CI 时长回归 | ✅ T9-W6 |
| P2-3 不阻塞 PR | ✅ T9-W7 |
| P2-4 错误消息收敛 | ⚠️ follow-up TR6（可接受）|

### 上轮 v1 增量补丁审查新增清单（v2 通知未引用，需补审）

| 缺陷 | 修复状态 |
|---|---|
| P0-5 RR 锚 `MERGE_PLAN_*.md` 违锁清单 | ✅ 已删除该字符串；§16.1 RR 行写"三件存在"（建议补全 3 个文件名作 P2，但锁清单不再被违反，本项闭环）|
| P1-6 §17.3 fake_merge_bin glob/template 二义 | ❌ **未修复**：§17 line 546 显式写"17.1-17.6 与 v1 增量补丁一致，不动" |
| P1-7 §0a 金字塔分类不诚实（meta 归 e2e）| ⚠️ **部分修复**：§0a 把 meta 单列，但加注脚"归 e2e 边界"仍语义模糊 |
| P2-3 §16.3 缺 evaluated_at 显式断言 | ⚠️ 文档清单含 evaluated_at，但无单独用例断言其为 ISO timestamp |

**总评**：v1-review 的 9 项中 7 项实质修复、2 项关键项（P0-2/P0-4）有严重新混乱；上轮新增 4 项中 2 项实质 / 1 项部分 / 1 项未修。

---

## Step 1 — 锁清单事实核查

- approved-facts.md `[plan]` 段 9 条事实在 v2 中均无新违反 ✓
- T4-D10 假设 `MergeState.snapshot_rollback_events` 字段（plan v3 待落）—— 已标 TR7 follow-up，处理合理 ✓
- §16.1 RR 锚不再写 `MERGE_PLAN_*.md` ✓

---

## Step 2 — 本轮新发现 P0（critical）

### 🔴 P0-1（关键，3 轮告警触发）：v2 内部用例数三套数字相互矛盾

**位置**：
- §A line 18：P0-2 处置写 "**86 unit + 2 integration + 4 meta = 92**"
- §0 line 65 + §0a line 82 + §15 line 506：合计 "**87 unit + 3 integration + 7 meta = 97**"
- §A.1 line 47：v2.1 增量后 "用例数 **87 → 90 unit；97 → 100 总用例**"

**问题**：
1. v2 自身在四处给出三个不同的合计（92 / 97 / 100），且每处都自洽地宣称"已重算"
2. §A.1 宣告 v2.1 增量 +3（T6-G9/G10/G11）→ 总数应为 100，但 §0/§0a/§15 三表全部仍写 97，**未做 v2.1 同步重算**
3. CI owner 后续按哪个数验收无可对账基准

**3 轮告警触发**：
- **第 1 轮**（v1→v1-review P0-2）：§0 摘要 35 vs 正文 88 失实
- **第 2 轮**（v1+增量→上轮 P0-2 持续）：§0/§0a/§15 三处均写 35 仍失实，回归扩大
- **第 3 轮**（v2→本轮 P0-1）：内部 §A 92 / §0 97 / §A.1 100 三套数字，同一文件内部互相矛盾

→ 本轮已 SendMessage team-lead 报告 3 轮 NO-GO 协议

**要求**：以 v2.1 实际正文为准（§0 + §0a + §15）做最终重算 → 单一数字写入所有四处；同时 §A 表 line 18 同步修正

### 🔴 P0-2（关键）：v2.1 §A.1 表宣告 vs 正文严重脱节

**位置**：§A.1（line 35-47）vs §7.1（line 377-385）vs §16.2（line 565-566）

**问题**：§A.1 表宣告了 7 处改动（T0-S4 加 kind/multiplier、T6-G6 重写、T6-G8 重写、T6-G9 新增、T6-G10 新增、T6-G11 新增、§16.2 升级），实际正文中：

| §A.1 宣告 | 正文实际 |
|---|---|
| T0-S4 加 `kind ∈ {absolute,relative}` + `multiplier` 字段断言 | T0-S4（line 149-155）仍是 v2 措辞，未加新字段断言 |
| T6-G6 改写：`OA=0.80, kind=absolute, threshold=0.92`，"baseline 不参与判定" | T6-G6（line 366-373）仍写"按方案 A" + "如方案 B/C 改..." 旧措辞 |
| T6-G8 重写：缺 baseline + relative gate → SKIP（pass==null） | T6-G8（line 377-385）仍是 v2 旧版"全 pass 路径，按方案 A" |
| **T6-G9 新增**：baseline 存在 + cost 超 1.15× → exit 2 | **正文完全不存在 T6-G9** |
| **T6-G10 新增**：baseline + cost 在 1.15× 内 → exit 0 | **正文完全不存在 T6-G10** |
| **T6-G11 新增**：hard fail + soft fail → exit 1 优先 | **正文完全不存在 T6-G11** |
| §16.2 升级：cost_p95 / wall_time_p95 改为 T6-G9/G10 直接计算覆盖 | §16.2 line 565-566 仍写"T0-S4 schema + T3-R4 + T5-S1"（schema only） |

**含义**：team-lead 已回复方案 C 决策（这是好事，TR8 闭环），但 verifier 把决策落到了 §A.1 表格而非真正的用例正文。CI / Executor 后续按正文跑测时，**T6-G9/G10/G11 完全不存在**；T6-G6/G8 仍按旧（v2 占位）方案。

**要求**：
- 正文 §7.1 必须新增 T6-G9 / T6-G10 / T6-G11 三条用例（按 §A.1 描述展开成完整三要素）
- T6-G6 / T6-G8 必须按 §A.1 描述就地重写
- T0-S4 必须加 `kind` / `multiplier` 字段断言
- §16.2 cost_p95 / wall_time_p95 行必须按 §A.1 line 44 改为 T6-G9/G10
- 正文落地后回写 §0 / §0a / §15 用例数（解决 P0-1 部分）

---

## Step 3 — 本轮 P1

### P1-1（持续）：§17.3 fake_merge_bin 文件名 glob/template 二义未修
- **位置**：§17 line 546 显式声明"17.1-17.6 与 v1 增量补丁一致，不动"
- **问题**：上轮 P1-6 提的 "fake_merge_bin 内 `merge_report_${RUN_ID}.json` 文件名是字面量还是模板"二义未触及
- **要求**：明确 fixture 文件名约定：fixture 落盘命名 `merge_report_FIXTURE.json`（固定字面量）；shell 脚本第一行 cp + rename 到 `merge_report_${RUN_ID}.json`；删除 glob fallback

### P1-2（新）：§0a 金字塔分类标签"归 e2e 边界"语义回避
- **位置**：§0a line 81
- **问题**：把 7 条 yml meta 测试单列且加注脚"归 e2e 边界"，但 yml `safe_load` + 字符串断言本质是 unit；标签 ambiguous 会导致后续讨论金字塔比例时混淆
- **要求**：方案 A：把 meta 7 条计入 unit，金字塔重算 unit=94/integration=3=97 → 96.9% / 3.1% / 0%（更诚实地反映"本项目无传统 e2e"）；方案 B：保留 meta 独立分类但删"归 e2e 边界"措辞，明文写"meta 不参与传统金字塔比例"

---

## Step 4 — 本轮 P2

### P2-1（持续）：§16.3 缺 evaluated_at 显式用例断言
- **位置**：§16.3 line 573
- **问题**：依赖 T6-G1（全 pass 路径）隐式覆盖；无单独用例断言 `eval_acceptance.json.meta.evaluated_at` 是合法 ISO timestamp
- **要求**：T6-G1 期望项里加一行 `assert datetime.fromisoformat(meta["evaluated_at"])`；或新增 T6-G12

---

## Step 5 — 通过标准评估

按上轮 review 设定的"P0 全修 + P1 ≥ 5/7"标准核对：

| 标准 | 评估 |
|---|---|
| P0-1 SRSR | ✅ |
| P0-2 用例数 | ❌ 三套数字 |
| P0-3 memory.db | ✅ |
| P0-4 baseline 语义 | ⚠️ team-lead 决策已下，但落地仅在 §A.1 表，正文未同步 |
| P0-5 RR 锚 | ✅ |
| P1-1 / L-2 / L-3 / L-4 / L-5 | ✅ 全 5 |
| P1-6 fake_merge_bin | ❌ |
| P1-7 金字塔分类 | ⚠️ |

P0：实质 3/5，P0-2 + P0-4 阻断；P1：5/7 边界（P1-6 未修 + P1-7 部分）

**结论**：未达通过标准。

---

## 修订建议（给 verifier）

按以下顺序：

1. **优先 P0-2**：把 §A.1 宣告的 v2.1 全部改动落到正文：
   - 正文 §7.1 新增 T6-G9 / T6-G10 / T6-G11 三条完整用例（输入+期望+断言锚点）
   - §7.1 T6-G6 / T6-G8 就地重写按 §A.1 描述
   - §1.2 T0-S4 加 `kind` / `multiplier` 字段断言
   - §16.2 cost_p95 / wall_time_p95 行更新
2. **次优先 P0-1**：上述落地后，统一 §A line 18 / §0 / §0a / §15 用例数为单一真值（按 v2.1 实际正文重数）
3. **P1-1 + P1-2**：本地补
4. **P2-1**：本地补

修订后产出 **v3.md**（不要再就地追加），方便 diff。

---

## 通过标准（gatekeeper-test）

- P0 全修（P0-2 + P0-4 必修）
- P1 至少修 1/2（建议 P1-1 必修以护住 fixture 契约）

---

## 协议提醒

⚠️ **3 轮告警协议触发**：P0-2（用例数失实）已连续 3 轮未根治：
- v1-review P0-2（§0 摘要 35 vs 正文 88）
- 上轮 P0-2（§0 / §0a / §15 三处仍写 35）
- 本轮 P0-1（§A 92 / §0 97 / §A.1 100，同文件内部三套）

按 gatekeeper 协议「同一处问题 3 轮仍 NO-GO → 主动告警 team-lead」，本轮已 SendMessage team-lead 报告该缺陷连续 3 轮未根治，请 team-lead 评估是否需直接介入或换工作流。
