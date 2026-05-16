# 评估数据集（Dataset）

> 评估集**质量决定结论可信度**。本章定义三层数据集的构造原则、维护机制、防过拟合规则。
> 数据集所在路径：`tests/eval/datasets/`（待建）。

---

## 1. 设计原则

1. **真实优先**：Tier-2 取自历史真实合并，是结论是否能外推到生产的关键。
2. **覆盖优先**：Tier-1 必须覆盖 ABCDE × {AUTO_SAFE, AUTO_RISKY, HUMAN_REQUIRED} × M1-M6 的关键交叉。
3. **对抗优先**：Tier-3 用注入式陷阱专门攻击系统的"语义丢失"盲点，避免黄金合并里偶然不出现某类问题。
4. **隔离原则**：评估集不进入训练 / few-shot / Memory；任何调优只能在 dev split 上进行。
5. **基线锁定**：评估集 commit 哈希 + checksum 写入 `acceptance.md`，禁止"为了通过测试改样本"。

---

## 2. Tier-1 微基准

### 2.1 规模

- 30–60 个 PR，约 200–500 个文件。
- 跑完一轮控制在 **30 分钟** 内（CI 可接受），单次成本 < $5。

### 2.2 抽样矩阵

每格至少 5 个样本：

|        | A | B | C | D_MISSING | D_EXTRA | E |
|--------|---|---|---|-----------|---------|---|
| AUTO_SAFE | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| AUTO_RISKY | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| HUMAN_REQUIRED | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

并叠加 M1-M6 注入维度：每类丢失模式至少 5 个样本（与上面交叉，不要求满铺）。

### 2.3 标注流程

每个样本由人工产出三份产物：

```
samples/<sample_id>/
  base.tar           # merge base 的工作树
  upstream.patch     # 上游侧改动
  fork.patch         # fork 侧改动
  golden.tar         # 人工黄金合并的最终工作树
  meta.yaml          # category / risk / loss_class / notes
```

`meta.yaml` 必填字段：

```yaml
sample_id: t1-0001
category: C            # ABCDE
expected_risk: AUTO_RISKY
loss_class: M3         # 若注入了语义丢失，否则 null
expect_human: false    # 是否应升级到人工
golden_strategy: SEMANTIC_MERGE
notes: "tongyi headers conflict; both add new params"
```

### 2.4 维护规则

- 评估集变更必须走 PR + 至少 2 名工程师评审；
- 任何样本都需有 reproducer 命令；
- 每季度审计 1 次："黄金"是否随上游变更而过期。

---

## 3. Tier-2 历史回放

### 3.1 来源

挑选**已被人工合并并跑过 1 个版本以上无重大回滚**的真实合并：

- 候选库：`dify-official-plugins`、`dify-plugin-daemon`、`insforge` 等已在 `doc/test-report/` 跑过的目标。
- 推荐选取 5–10 个跨越 ≥ 25 commits 的合并窗口，覆盖小 / 中 / 大三档。

### 3.2 构造方式

```bash
# 例：复现 upstream/main~50 → feat_merge 的合并
git checkout -b eval/replay-<id> <merge_base_sha>
# Ground Truth = 当时人工合出的 merge commit ^2..merge
git log --first-parent <merge_sha> -1 --format=%H > golden.sha
```

每个回放条目包含：

```
replays/<replay_id>/
  config.yaml      # .merge/config.yaml 完全可复现
  golden.sha       # 真实人工合出的 commit
  golden_diff.patch # base...golden 的 patch（diff oracle）
  notes.md         # 当时的争议点、回滚记录
```

### 3.3 评估口径

- 系统在 `eval/replay-<id>` 上跑完后，比对 `merged tree` 与 `golden.sha` 的 tree：
  - 文件存在性差异 → MISS_UPSTREAM / MISS_FORK
  - 内容差异 → 按 metrics.md §1.2 走 EXACT / SEMANTIC / MISMATCH
- 报告必须列出"系统选择人工 + 用户实际未决策"的样本（H[f]=true 但人工没动）作为单独类别，不计入 WMR 但计入升级负担。

### 3.4 防过拟合

- **不能**对评估集中出现的 prompt / few-shot / Memory 做特殊调优；
- 如果某个 replay 用于调优，需从评估集中移除，标注到 `dev_split.txt`。

---

## 4. Tier-3 对抗集

### 4.1 目的

回答"系统真的能识别 M1-M6 吗"，不被 Tier-1/2 中样本覆盖不足误导。

### 4.2 注入策略

| 类别 | 注入手段 |
|---|---|
| **M1 调用点丢失** | 删掉 fork 侧某函数的所有调用，但保留定义；上游引入了新调用方 |
| **M2 配置丢失** | fork 在 yaml/env 加了字段，注入时只在某一个 yaml 文件保留，其他被覆盖 |
| **M3 接口契约丢失** | 上游改了函数签名，fork 有兼容封装；注入"上游签名 + fork 旧封装"冲突 |
| **M4 资源依赖丢失** | fork 引入新的 vendor lib，注入上游升级时未携带依赖 |
| **M5 默认值丢失** | fork 改了一个 default，注入上游回退到旧 default |
| **M6 路径 / 命名空间丢失** | fork 重命名 module，注入上游引用旧名 |

### 4.3 标注

每个样本 `meta.yaml` 必含：

```yaml
loss_class: M3
expected_detection: true
expected_strategy: ESCALATE_HUMAN | SEMANTIC_MERGE
golden_diff_summary: "system must keep fork's compat wrapper"
```

### 4.4 通过门槛

- 每类 Mi 召回率 ≥ 95%（详见 acceptance.md）；
- WMR 必须为 0：宁可漏合给人工，也不允许错合。

---

## 5. 数据集仓库结构

```
tests/eval/
  datasets/
    tier1/samples/<id>/...
    tier2/replays/<id>/...
    tier3/adversarial/<id>/...
  manifests/
    tier1.lock.json    # sample_id → checksum
    tier2.lock.json
    tier3.lock.json
  scripts/             # prepare / diff / summarize / gate （见 procedure.md）
```

`*.lock.json` 由 `scripts/eval/lock.py` 生成，CI 校验：

- 任何 sample 内容变更但未更新 lock → CI 失败；
- 任何 lock 变更但未在 PR 描述中说明 → 评审拒绝。

---

## 6. 数据安全

- 评估集禁止包含真实凭据（CI 用专用 dummy key）。
- 包含真实 fork 源码的 Tier-2 必须在仓库内有显式授权，否则放在私有 submodule。
- 评估输出（diff / 报告）按 release 分区清理，不与生产 `.merge/runs/` 混放。
