# 0529 · 上下文/记忆优化(OPP 系列)成效评估方案

> 评估对象:2026-05-29 落地的 6 项 SOTA 缺口优化 **OPP-1/4/5/6/8/10**,以及 OPP-10
> 的 analyst/judge 接入补完(`test_opp10_analyst_judge_wiring.py`)。
> 提案出处:`doc/references/enhanced-context-memory-proposal.md`(P0-P3 早已实装,本轮是其上的增量)。
> 评估口径沿用 `doc/evaluation/`(`metrics.md` / `acceptance.md` / `procedure.md`),本文件只补"如何把这几项优化的成效**可量化、可归因、可回归**"。

---

## 0. 一句话结论(方法论)

这几项优化**不直接改最终正确性**,而是改 LLM 的**输入质量**(记忆召回更准、staged 压缩更保真、依赖图权重更聚焦)。
因此:

- **不能**用全量 `OA`(总正确率)做 A/B —— 信号会被淹没在噪声里(只有被触发的文件才受影响,全量分母把效应稀释到统计不可见)。
- **应当**做**两层评估**:
  - **L1 组件层** —— 确定性、廉价、无需 LLM、可单测/脚本化,直接量机制本身(召回率、FULL 保留率、预算合规、去重纯度)。**这是主证据**。
  - **L2 系统层** —— 端到端 A/B,但**只在"机制被触发的文件子集"上**对照 `HMR / AERR / MMR / 成本`。**这是验证二阶效应**。

---

## 1. 评估对象与各自的"待证假设"

| 编号 | 改动 | 待证假设(优化想换来什么) | 主指标层 |
|---|---|---|---|
| **OPP-1** | `SQLiteMemoryStore` 复用共享 `score_path_overlap()`(补回 Jaccard 兄弟目录分支) | 生产(sqlite)路径的记忆召回不再静默弱于 in-memory;兄弟目录条目能被召回 | L1 |
| **OPP-6** | `_update_memory` LLM 抽取分支累积回写 `add_entry` 返回值 | in-memory store 下 LLM 记忆不再全丢,条目留存=100% | L1(回归) |
| **OPP-4** | `build_staged_content` 三处盲截 → `_truncate_text`;修 `available==1` 越界 | staged 输出严格 ≤ 预算;不再出现超预算 ~40× 的潜伏放大 | L1(回归+合规) |
| **OPP-8** | `_consolidate_entries` 分组键加目录桶 | 跨目录同类 pattern 不再被合并成有损 blob;合并纯度↑ | L1 |
| **OPP-10** | 依赖图扇入权重注入 relevance;executor+analyst+judge 全接入 | 高扇入公共接口在 staged 压缩下保 FULL;下游幻觉跨模块引用↓、加性导出保留↑ | L1 + L2 |
| **OPP-5** | 反馈环按 judge 结果奖惩记忆 confidence(**默认关闭**) | 开启后 harmful 条目被持续降权,多 run 召回精度↑ | L1 + L2(需多 run) |

---

## 2. L1 组件层指标(主证据,确定性、廉价)

> 全部可脱离真实 LLM、用 fixture/脚本算出,适合进 CI 做防回归。多数已有单测雏形,缺的标注"需新增脚本/埋点"。

### 2.1 OPP-1 · 记忆召回率 `MemRecall@k`

- **定义**:构造一个带标注的召回集——给定查询文件路径,人工标注"应被召回"的记忆条目(含**兄弟目录**条目)。
  `MemRecall@k = |命中 ∩ top-k| / |应召回|`。
- **关键对照**:同一查询分别走 `MemoryStore`(in-memory)与 `SQLiteMemoryStore`。
  **期望**:OPP-1 后两者 top-k 顺序/命中**一致**(此前 sqlite 缺 Jaccard 分支 → 兄弟目录条目被静默削弱)。
- **数据源**:`src/memory/` 的 `get_relevant_context` / `score_path_overlap`。
- **执行**:需新增 `scripts/eval/mem_recall.py`(纯词法,无 LLM),喂一组 (query_path, 候选条目) fixture,断言两 store 召回集相等且 ≥ 基线。
- **回归红线**:两 store 召回集**必须逐条相等**(这是 OPP-1 的本质——消除实现分叉)。

### 2.2 OPP-6 · 记忆条目留存率 `MemRetention`

- **定义**:一次 `_update_memory` LLM 抽取分支处理 N 条候选,留存 = 实际入库条目 / 应入库条目。
- **期望**:in-memory store 路径下 = 100%(此前不可变 `add_entry` 返回值被丢弃 → 0%)。
- **执行**:单测即可(mock LLM 抽取返回 N 条,断言 store 计数 = N)。属**回归守门**,无需 A/B。

### 2.3 OPP-4 · staged 预算合规 `BudgetCompliance` + 截断模式正确性

- **定义**:对每次 `build_staged_content`,`estimate_tokens(staged) ≤ budget_tokens`。
  违规计数 `BudgetOverrun = |{调用 : rendered_tokens > budget}|`,**目标 = 0**。
- **额外**:小文件走 tail、两个 fallback 走 middle;`available==1` 不再返回整串。
- **数据源**:已有埋点 `prompt_builders.py:165` 的 `Staged processing: ... tokens=%d/%d` 日志——
  直接 grep 真实 run 日志,断言左值 ≤ 右值。
- **执行**:(a) 单测覆盖边界(`available==1`、空 chunk、刚好等于预算);
  (b) 真实 run 后跑 `scripts/eval/`(需新增)`assert_budget.py` 扫日志。
- **回归红线**:`BudgetOverrun = 0`。这是 OPP-4 修掉的"超预算 40×"潜伏 bug 的直接守门。

### 2.4 OPP-8 · 合并纯度 `ConsolidationPurity`

- **定义**:跑 `_consolidate_entries` 后,被合并进同一条的子条目其 `file_paths[0]` 必须落在**同一目录桶**。
  `Purity = |纯目录组| / |总合并组|`,**目标 = 100%**。
- **期望**:OPP-8 后不再出现"跨目录同类 pattern 合并成有损 blob"。
- **执行**:单测——构造跨目录同 tag 的条目,断言它们不被合并到一起。

### 2.5 OPP-10 · 高扇入符号 FULL 保留率 `FullRetention@fanin`

- **定义**:对一个超预算需 staged 的文件,取依赖图中**高扇入**(fan-in ≥ τ,如 ≥4)的公共符号,
  `FullRetention = |这些符号被判 FULL| / |这些符号总数|`。
- **对照**:同文件 `symbol_weights={}`(空图退化)vs `weights_from_fanin(symbol_fanin)`。
  **期望**:加权后高扇入符号 FULL 保留率显著↑,叶子 helper 不变;空图两者相等(已由
  `test_relevance_degree_weight.py::test_empty_weights_reproduce_flat_reference_behaviour` 守门)。
- **覆盖三消费方**:executor / analyst(`analyze_file`)/ judge(`review_file`)均把权重透传到 staging
  (已由 `test_opp10_analyst_judge_wiring.py` 守门)。
- **执行**:单测 + 可选脚本统计真实 run 的 FULL 集合。

---

## 3. L2 系统层指标(二阶效应,端到端 A/B,贵)

> **只在"机制被触发的文件子集"上对照**,否则信号被全量稀释。复用 `doc/evaluation/metrics.md` 既有指标,**无需新指标**。

| OPP | 二阶指标(metrics.md 既有) | 触发子集(分母收窄到这里) | 期望方向 |
|---|---|---|---|
| OPP-10 | `HMR` 幻觉跨模块引用数(§8.3) | 依赖图 fan-in 非空 且 需 staged 的文件 | ↓(更可能为 0) |
| OPP-10 | `AERR` 加性导出保留率(§8.2) | 含高扇入 fork-新增导出的文件 | ↑(更接近 100%) |
| OPP-4/10 | `MMR` 漏合率(行级) | 超预算被 staged 的大文件 | ↓ |
| OPP-1/5 | `OA`/`CRA` | 命中相关记忆条目的文件 | 持平或↑(不得退化) |
| 全部 | `cost_usd_per_run` / `wall_time` P95 | 全量 | ≤ 基线 ×1.15 / ×1.20(acceptance soft gate) |

**判定原则**:L2 主看"**不退化**"(WMR/MMR/成本不破 acceptance gate)+ "触发子集上 HMR/AERR 改善";
**不**强求全量 OA 上升——那不是这批优化的设计目标。

---

## 4. A/B 实施:开关从哪来

清醒地面对一个事实——**这批优化大多没有运行时开关**(OPP-1/4/6/8/10 是"修复实现分叉/bug"型,默认即生效),只有 OPP-5 有配置旋钮。所以 A/B 分两种做法:

### 4.1 有 toggle 的:OPP-5

- 开关:`.merge/config.yaml` 的 `memory.outcome_confidence_writeback`(`src/models/config.py:876`,默认 `false`)。
- A/B:`writeback: false` vs `true`,**跑多 run**(单 run 无意义——反馈环要积累)。
  对照 harmful 条目召回随 run 衰减曲线 + 触发子集 OA。
- 注意排除项已内建:`decision_source∈{HUMAN,BATCH_HUMAN}` 文件与 bootstrap 人工条目不被奖惩。

### 4.2 无 toggle 的:OPP-10 用"强制空图"做 A/B,其余用 commit-pair

- **OPP-10**:最干净的 off 态 = **空依赖图**(`weights_from_fanin({})→{}` → flat 行为,代码已保证退化)。
  评估时加一个临时 env/config 让 `initialize` 跳过依赖图构建(或注入空 `FileDependencyGraph`)即得 baseline,
  无需改业务代码。**建议补一个 `--no-dep-graph` 评估旁路**(仅评估用)。
- **OPP-1/4/8**:无退化旁路 → 用 **commit-pair A/B**:
  `git checkout <OPP 前一提交>` 跑 baseline,再 `feat/web` HEAD 跑 treatment,
  其余配置/seed/temperature/数据集 lock 全部锁死(见 `procedure.md §6` 陷阱)。
  涉及提交序列:`a5c54b7 c3a987b 08e1854 cf9e644 2cdac27 04a0523`(+OPP-10 analyst/judge 补完提交)。

> 优先级:**L1 组件层证据 > L2 系统 A/B**。L1 廉价且能精确归因到单个 OPP;
> L2 贵且嘈杂,只用来证明"没有引入二阶退化 + 触发子集有改善"。

---

## 5. 具体执行步骤(基于现有工具链 + forgejo 评估仓库)

> 评估仓库:`/Users/angel/AI/merge-test/forgejo`(fork=`test/fork`,upstream=`origin/forgejo`,base=`160377405c`);
> Tier-1 样本 `t1-0031..0033` 的 golden 在分支 `eval/golden-forgejo-auth`(见记忆 `reference_forgejo_eval`)。

### 5.1 L1(先做,CI 化)

```bash
# 现有单测(已绿)——机制守门
pytest tests/unit/test_relevance_degree_weight.py \
       tests/unit/test_opp10_analyst_judge_wiring.py -q

# 需新增的组件脚本(纯确定性,无 LLM):
#   scripts/eval/mem_recall.py     -> OPP-1 双 store 召回一致性
#   scripts/eval/assert_budget.py  -> OPP-4 扫 staged 日志断言 tokens<=budget
#   (OPP-6/8 用单测即可,无需脚本)
```

### 5.2 L2(端到端 A/B,沿用 scripts/eval/run.py)

```bash
# baseline 与 treatment 各跑一次(同 seed / 同数据集 lock / temperature=0)
# A/B 维度通过 --merge-args 透传不同 config(OPP-5 toggle / OPP-10 空图旁路)
python scripts/eval/run.py --tier 1 --workdir /tmp/eval-ab/baseline \
  --merge-bin "$(command -v merge)" --use-real-keys --seed 1 \
  --merge-args "--config .merge/config.baseline.yaml"
python scripts/eval/run.py --tier 1 --workdir /tmp/eval-ab/treatment \
  --merge-bin "$(command -v merge)" --use-real-keys --seed 1 \
  --merge-args "--config .merge/config.treatment.yaml"

# 逐文件对照 golden -> 指标 -> 报告 -> gate
python scripts/eval/diff_against_golden.py --runs /tmp/eval-ab/treatment ...
python scripts/eval/summarize.py --diff .../diff.json --output report_treatment.md ...
python scripts/eval/gate.py --report report_treatment.md ...
```

> ⚠️ 缓存陷阱(`procedure.md §6.2`):Anthropic 缓存命中会让 treatment "白嫖"baseline 的上下文,
> A/B 前对其中一侧 `--no-cache` 至少一次,否则成本/时延对照失真。

### 5.3 结果归档

按 `acceptance.md §3` 写 `eval_acceptance_<sha>.json`,并在本目录追加
`0529-...-results.md` 记录 baseline/treatment 双列数据(L1 表 + L2 触发子集表)。

---

## 6. 判定标准("算不算有效")

**有效(可宣称落地见效)需同时满足:**

- L1 全部回归红线达标:
  `MemRecall` 双 store 逐条相等;`MemRetention=100%`;`BudgetOverrun=0`;
  `ConsolidationPurity=100%`;`FullRetention@fanin(加权) > 空图基线` 且空图两者相等。
- L2 不退化:`WMR=0`、`MMR` 不破 acceptance hard gate、成本/时延不破 soft gate(×1.15/×1.20)。
- L2 触发子集有方向性改善:`HMR↓`(更接近 0)或 `AERR↑`(更接近 100%)二者至少其一,且无反向恶化。

**无效/需回退信号:**

- 任一 L1 红线破(说明优化引入了新分叉/bug);
- L2 触发子集 OA 退化 ≥3%(`acceptance.md §2` 回归排查阈值);
- 成本 P95 > 基线 ×1.15(staged 压缩本应**省** token,若反而涨说明 FULL 集合膨胀)。

---

## 7. 陷阱与边界(必须在报告头声明)

1. **信号稀释**:全量 OA 对这批优化几乎无感——务必在触发子集上看,并在报告里写明子集定义与分母。
2. **OPP-5 默认关**:不开 toggle 跑出来的 run **不含** OPP-5 效应,别把它算进"已生效"。
3. **OPP-10 退化即基线**:空图 = flat 行为,这是设计;A/B 的 off 态就用空图,不要去删代码。
4. **commit-pair A/B 的纯净性**(`procedure.md §6.4`):跑完发现 bug 改源码再跑,旧 run 必须丢弃,不能挑有利的那次。
5. **依赖图覆盖度**:OPP-10 的效应上限受依赖图提取覆盖度约束(Python ast 全、TS 降级、tree-sitter 实际未装——见记忆 `dependency_graph_unwired`)。报告需附 fan-in 非空文件占比,否则"无改善"可能只是图为空而非优化无效。
6. **Judge 自评 ≠ Ground Truth**(`procedure.md §6.5`):L2 一律对 golden,不用 judge verdict 当正确性来源。

---

## 8. 落地清单(TODO)

- [ ] `scripts/eval/mem_recall.py` —— OPP-1 双 store 召回一致性(确定性,无 LLM)
- [ ] `scripts/eval/assert_budget.py` —— OPP-4 扫 `Staged processing` 日志断言预算合规
- [ ] OPP-10 评估旁路 `--no-dep-graph`(或注入空图的 config 项,仅评估用)
- [ ] OPP-8 跨目录非合并单测(若 `tests/unit/` 尚无)
- [ ] 一次 forgejo Tier-1 commit-pair A/B,产出 `0529-...-results.md` 双列数据
- [ ] OPP-5 多 run(toggle on/off)harmful 条目衰减曲线
