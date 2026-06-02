# LLM-判断密集 Golden 集

> 这是 `merge optimize-prompts --golden` 的决策验证集，**与 Tier-1/2/3 评估数据集
> 的 `golden.tar`（人工黄金合并树）是两回事**：这里的 golden 是**逐 gate 的决策
> oracle**，形如 `[{case_id, expected_decision}]`，用来给候选提示按决策准确率排名。

---

## 1. 为什么要"判断密集"

`optimize-prompts` 的打分信号是：候选提示的 rollout 是否复现 `expected_decision`
（`src/tools/prompt_optimizer.py` `score_candidates`）。这个信号**只有在决策真正由
LLM 推理决定时才有区分度**。如果一个 case 的最终决策被确定性规则短路，那么所有候选
提示都会产出同一个决策 → 准确率全相同 → 优化器无法分辨提示好坏，这个 case 是噪声。

因此 golden 集必须**集中在 LLM 判断区**，排除被确定性路径决定的 case。

### 纳入（judgment-intensive）

- 风险分落在 `risk_score_low`/`risk_score_high` 之间的边界样本（`auto_risky`，
  是否升级取决于 LLM 复核）。
- C-class 真实文本冲突：双侧对同一区域做**独立**改动，ConflictAnalyst 需判断
  能否安全语义合并、还是升级。
- 接口契约 / 调用点漂移（M1/M3 形态），auto-merge 与升级之间的灰带。
- 命中 `risk_hint_patterns`（弱信号，如 `**/auth/**`）但**未**命中严格
  `security_sensitive.patterns` 的文件——`config.py` 明确"LLM 的混合分有最终发言权"。

### 排除（deterministic short-circuit）

- 命中 `security_sensitive.patterns`（`**/.env`、`**/credentials.go`、
  `**/auth/credentials/**` …）→ 强制 `human_required`，与提示无关。
- 确定性 veto / `deterministic_issues` 强制 judge `fail`。
- `--no-llm` heuristic 封顶（如封顶 `high` 却判 `critical` 之类的不可达门）。
- 二进制 / `deleted_only` / `excluded` 等 sentinel 风险级。

判据口诀：**换一版提示词，这个 case 的决策会变吗？** 会 → 纳入；不会 → 排除。

---

## 2. 各 gate 的决策词表

`expected_decision` 必须取自该 gate 真实的决策枚举（`_golden.py` 的
`GATE_DECISION_VOCAB` 从生产枚举派生，写错值会让 `build_golden` 直接报错）：

| gate ID | 来源枚举 | 取值 |
|---|---|---|
| `J-SYSTEM` | `VerdictType` (`src/models/judge.py`) | `pass` / `conditional` / `fail` |
| `P-RISK-SCORE-SYSTEM` | `RiskLevel` (`src/models/diff.py`) | `auto_safe` / `auto_risky` / `human_required`（+ sentinel `deleted_only`/`binary`/`excluded`） |
| `CA-SYSTEM` | `MergeDecision` (`src/models/decision.py`) | `take_current` / `take_target` / `semantic_merge` / `manual_patch` / `escalate_human` / `skip` |

一个样本可同时为多个 gate 贡献 golden case（同一个 C-class 冲突既是
`human_required` 的风险 case，也是 `escalate_human` 的 analyst case）。

---

## 3. 单一真相源：meta.yaml → 生成器

golden 集**不手写**，而是从样本 `meta.yaml` 派生，避免与数据集标注双源漂移。

在样本 `meta.yaml` 里声明：

```yaml
judgment_intensive: true     # 决策由 LLM 驱动，非确定性短路
golden_decisions:
  - gate_id: P-RISK-SCORE-SYSTEM
    expected_decision: human_required
  - gate_id: CA-SYSTEM
    expected_decision: escalate_human
```

两个字段都是可选的（`SampleMeta` 默认 `judgment_intensive=false`、
`golden_decisions=()`），绝大多数样本不带。`judgment_intensive: true` 但不写
`golden_decisions` 是合法的 no-op（先入选、后标注）。

生成（确定性、按 case_id 排序，输出到 `tests/eval/golden/<gate_id>.golden.json`）：

```bash
python -m scripts.eval.build_golden                 # 扫 tier 1/2/3
python -m scripts.eval.build_golden --tier 1        # 只扫 tier-1
```

编辑样本 `meta.yaml` 后会改变其内容哈希，需重建 lock：

```bash
python -m scripts.eval.lock --update
python -m scripts.eval.lock --verify
```

消费（rollout 是你自担成本的离线步骤，见 `self-learning-system.md` Phase 3）：

```bash
merge optimize-prompts --gate CA-SYSTEM \
  --golden tests/eval/golden/CA-SYSTEM.golden.json \
  --rollouts <你跑各候选产出的 {candidate_id:{case_id:decision}}.json>
```

---

## 4. 当前 seed 状态（2026-05-31）

数据集现有 **8 个判断密集真实样本**，覆盖完整决策面（其余 t1-0001..0030 为 TBD
占位 / 单边平凡改动，不入选）：

- **5 个 C-class 升级样本**（t1-0005/0006/0031/0032/0033）：双侧改**同一区域** →
  应升级。
- **3 个 B-class 干净自动合并样本**（t1-0034/0035/0036）：双侧对
  `models/auth/auth_token.go` 的**不相交区域**做纯增量改动，git 3-way 干净合并、
  `go build ./models/auth/` 通过，golden 保留双方（确定性 `git merge`，非系统自身
  产物，dataset.md §1.4）。提供 auto-merge 正例。
- **1 个 J-SYSTEM=fail fixture 样本**（t1-0037）：同形态干净合并，但 upstream 侧加的是
  一个**安全相关**的 `BelongsTo` 所有权校验；golden.tar 仍保留双方（正确），fail 负例
  喂的是丢掉该校验的 fork-only 树（§5）。

逐 gate 现状：

| gate | case 数 | 分布 |
|---|---|---|
| `CA-SYSTEM` | 8 | 5 `escalate_human` + 3 `semantic_merge`（t1-0034/0035/0036） |
| `P-RISK-SCORE-SYSTEM` | 8 | 5 `human_required` + 2 `auto_safe`（t1-0034/0036） + 1 `auto_risky`（t1-0035） |
| `J-SYSTEM` | 4 | 3 `pass`（t1-0034/0035/0036，喂 golden 树） + 1 `fail`（t1-0037，喂 fork-only 树） — 见 §5 契约 |

> 决策面现已**全向**：优化器既能惩罚"把升级误判成自动合并"的提示（升级类负例），
> 也能奖励"敢对干净合并自动放行 / 保留双方而非 take 一侧"的提示（auto-merge 正例；
> cf. IMPLEMENTATION_REPORT t1-0003 executor take_target 丢一侧的失败模式），
> 还能惩罚"对丢了一侧的合并仍判 pass"的提示（`J-SYSTEM=fail` 负例 t1-0037）。

---

## 5. J-SYSTEM rollout 输入契约（已固定）

`J-SYSTEM` 的 golden 语义依赖"judge 在 rollout 时看到的是哪棵树"。`optimize-prompts`
消费的 rollout JSON（`{candidate_id: {case_id: decision}}`）是你自担成本离线跑出来的，
因此**这棵树由你在跑 rollout 时按本契约喂**，否则 `expected_decision` 无法对齐产出：

输入树**全部是 `scripts.eval.prepare` 的现成产物**，rollout 无需临时构造（`prepare`
为每个样本写出 `golden_tree/`（= `golden.tar`）和 `working_tree/`（= base + `fork.patch`，
即只取 fork 侧、完全丢掉 upstream 增量的方向性 take_current 结果））：

| `expected_decision` | rollout 必须喂给 judge 的合并树 | 何以判得动 |
|---|---|---|
| `pass` | `prepare` 的 **`golden_tree/`**（= `golden.tar`，双侧都在） | 正确合并，calibrated 提示应认可 |
| `fail` | `prepare` 的 **`working_tree/`**（= base + `fork.patch`，丢掉 upstream 侧） | 与 `upstream.patch` 对照可见 upstream 的增量整段缺失 → 语义丢失 |

固定项（rollout 脚本必须遵守，否则该 case 的标签无意义）：

1. **输入树**严格取自上表，不得用系统自己跑出来的合并结果（那会把 judge 的输入与
   被测提示耦合，丧失 oracle 地位）。
2. judge 的其余上下文（base / upstream.patch / fork.patch、diff 摘要）按生产口径
   从样本五件套派生，跨 candidate 保持一致——唯一变量是 judge **提示词本身**。
   `fail` 案尤其依赖 `upstream.patch` 在场：judge 正是靠它发现 `working_tree` 缺了
   upstream 的增量。
3. 当前 3 个 `pass` case（t1-0034/0035/0036）均喂各自 `golden_tree/`。这三棵树都是
   纯增量双侧合并，正确提示应稳定 `pass`；过度保守的提示会误报 `conditional` / `fail`，
   这正是优化器要惩罚的——所以即便是 `pass`，case 仍有区分度（§1 判据：换提示会变吗？会）。
4. `fail` case（t1-0037）喂其 `working_tree/`：fork 侧加了 `const DefaultTokenTTLHours`，
   但 upstream 侧加的 `BelongsTo` 所有权校验在该树中完全缺失。calibrated 提示应判
   `fail`（丢了安全校验）；对 fork-only 树仍 `pass` 的提示就是负例要惩罚的对象。
   注意 t1-0037 仅声明 `J-SYSTEM`（其 golden.tar 本身是正确合并，可日后另补
   `P-RISK`/`CA` 正例标签，但不与本 fail fixture 混用）。

---

相关：数据集构造见 [dataset.md](dataset.md)，评估流程见
[procedure.md](procedure.md)，Phase 3 成本模型见
[../plan/self-learning-system.md](../plan/self-learning-system.md)。
