# `merge forks-profile init` — Auto-draft 起草器实施计划

> **状态**：已实施 + §9 整合三连击（PR-A `9158856` / PR-B `1fd27bd` / PR-C `32798cc`）
> **关联 RFE**：[multi-agent-optimization-from-merge-experience.md §9](multi-agent-optimization-from-merge-experience.md)
> **前置依赖**：`8783475`（P0-1 schema + plan 路由）、`c07b34d`（P0-2/P1/P2 全链路 + JSON Schema CLI）
>
> ## 与本文档原始设计的差异（§9 整合后）
>
> 本文初版按"yaml 单一真源 + 手工维护 + init/diff 仅作旁路工具"设计。
> 落地后发现这与"开箱即用"定位割裂，于是做了三 PR 整合：
>
> | 原文档主张 | 现状（§9 整合后） |
> |---|---|
> | yaml 含 4 类条目（removed_domains / rewritten_modules / fork_only_features / migration_policy） | yaml 仅认 2 类 + `fork`：`removed_domains` / `rewritten_modules`。`fork_only_features` 与 `migration_policy` 每次 run 自动从 git 推算（`compute_auto_overlay`），yaml 写入会被 `ForksProfileError` 拒绝 |
> | "不把 init / diff 接入 `merge <branch>` 主流程"（§8） | 主流程**已接入两类自动化**：(i) 首次向导按阈值（30 fork-deleted 文件）触发 init；(ii) 每次 run 自动跑 diff，drift ≥3 项写入 `state.forks_profile_drift` 并附录到 `MERGE_PLAN_<run_id>.md` |
> | init/diff 渲染输出含全部 4 节 | drafter `render_profile_yaml` 不再输出 `fork_only_features:` / `migration_policy:` 顶层段，改为信息化注释；differ 不再比对这两类（已无可能漂移） |
>
> 用户感知：从"必须知道有这个文件 + 必须手写 + 必须手维护"三层负担，
> 变成"按需向导提示 + 起草自动 + 漂移自动浮出"。
>
> 原文档的设计目标（§2）与字段语义（§3 / §5 / §7）仍为准；以下细节保留作为算法参考，但落地范围以本节及上述 PR 为准。
>
> ---

## 1. 问题陈述

### 1.1 痛点

`forks-profile.yaml` 的 4 类条目要求 fork 维护方手工列出本 fork 的结构性差异：

- `removed_domains` — fork 主动删除的整域
- `rewritten_modules` — fork 重写的模块及其 policy
- `fork_only_features` — fork 独有的子树
- `migration_policy` — fork 占用的 migration 编号空间

实际 fork 历史动辄上百次提交，跨度可达数年。**靠人工记忆罗列必然漏报**，遗漏的条目在合并时表现为：

| 漏报项 | 后果 |
|---|---|
| 漏一个 `removed_domain` | 30+ 个 D_MISSING 文件被 judge 误报 critical（参考 insforge v2.1.0 报告 §4.2.2） |
| 漏一个 `rewritten_module.escalate_human` | executor LLM semantic_merge 静默失败，回退到 fork blob 丢失上游 diff |
| 漏 `migration_policy` | 编号碰撞的上游 migration 直接被 take_target，与 fork 同号 migration 撞库 |

### 1.2 现有手段的不足

- `merge forks-profile validate` 仅校验 yaml 语法 / schema —— 无法发现「漏写」
- IDE / pre-commit 同样只看已写的字段
- 现有 `compute_fork_divergence_map` 已经知道 fork 删了什么、改了什么 —— 但仅用于 judge 降级，从未导出给用户

## 2. 设计目标

| 目标 | 度量 |
|---|---|
| **机械可推断** | 全部 4 类字段都能从 git 客观计算，无需 LLM |
| **保守起草** | 宁可多列让用户删，不可漏列让用户事后补救 |
| **强制审阅** | 输出永远带 `# TODO: review` 标记；`--write` 默认拒绝覆盖已存在 profile |
| **零目标仓库特定字符串** | 启发式不依赖任何业务关键词；输出的 fixture 字段可全部由用户改写 |
| **复用现有工具** | 不引入新的 git 扫描器；最大化复用 `compute_fork_divergence_map`、`config_line_retention_checker` 等 |

## 3. 各字段的数据来源

### 3.1 `fork_only_features` —— 几乎无误差

数据：`compute_fork_divergence_map` 返回 `FORK_ONLY` 的全部文件。

聚类：按公共前缀目录折叠（[3.5](#35-子树聚类启发式) 详述）。

输出：每个独立子树一项，note 留空 `# TODO: describe`。

### 3.2 `removed_domains` —— 高置信，需人工合并

数据：`compute_fork_divergence_map` 返回 `FORK_DELETED` 的全部文件。

附加证据：用 `git log --diff-filter=D --name-only <merge_base>..<fork_ref> -- <path>` 反查删除该路径的最早 commit，作为 `removed_in` 字段填入；commit message 第一行作为 `reason` 草稿（用户必改）。

聚类：同 [3.5](#35-子树聚类启发式)。

### 3.3 `rewritten_modules` —— 启发式，最难调

数据：B 类（`fork_modified` 但 upstream 未变）+ C 类（双方都改）的文件，按以下条件筛选：

| 条件 | 阈值 | 解释 |
|---|---|---|
| `lines_retained / lines_at_base` | < 30% | fork 保留的原始行少于 1/3，视为重写而非修订 |
| AND `lines_changed` | ≥ 50 | 排除小文件因 token 数少误判 |
| OR `git log --follow --oneline` | ≥ 5 个 fork 独占 commit | 仍在持续大改造 |

实现复用 [`config_line_retention_checker`](../src/tools/config_line_retention_checker.py) 的 line-retention 算法。

聚类：同 [3.5](#35-子树聚类启发式)。

policy 字段统一填 **`escalate_human`**（最保守），让用户改 downgrade —— 因为漏检 `escalate_human` 的代价（静默错合）远高于多余 escalate（人工多审一次）。

### 3.4 `migration_policy` —— 纯数字事实

数据：用户在 CLI 提供 `--migration-glob`（默认尝试 `**/migrations/*.sql`、`**/db/migrate/*.{sql,py,rb}`、`**/alembic/versions/*.py`）。

算法：
1. 列出 fork_ref 上匹配 glob 的全部 migration 文件
2. 列出 merge_base 上匹配同 glob 的全部文件
3. 用现有 `extract_migration_number` 提取每个文件的编号
4. `fork_max = max(fork_only migrations 的编号)`；`upstream_max = max(merge_base 上的编号)`
5. 输出：
   ```yaml
   migration_policy:
     path_globs: <用户提供>
     fork_owns_numbers_above: <upstream_max>
     upstream_take_target_max: <upstream_max>
     on_collision:
       action: escalate_human
   ```

仅当 `fork_max > upstream_max` 时才输出此节（否则 fork 没占编号空间，无须策略）。

### 3.5 子树聚类启发式

公用算法（`removed_domains` / `fork_only_features` / `rewritten_modules` 三类共享）：

```
input:  list[file_path]
output: list[(common_glob, file_count)]

算法:
1. 把每个 path 拆成 segment 列表
2. 用 trie 找出"包含 ≥ N 个文件的最深公共前缀目录"，N = max(3, total / 20)
3. 把该子树的所有文件折叠成一条 `<prefix>/**`
4. 不能聚类的孤儿文件作为单独条目（path = 完整文件路径）
```

阈值 N 按规模自适应，避免：
- 小 fork（10 文件）被聚成全仓
- 大 fork（300 文件）每个文件单独成项

## 4. CLI 接口

`forks-profile` group 在原有 `validate` / `schema` 基础上新增两个互补子命令：

- **`init`** — 一次性从 git 历史起草 `forks-profile.yaml`（首次接入用）
- **`diff`** — 对比已有 yaml 与启发式当前结果，按差异分类（增量审阅用）

### 4.1 `init` —— 一次性起草

```bash
merge forks-profile init [OPTIONS]

# 必填
--upstream REF          上游 ref（默认 upstream/main）
--fork REF              fork ref（默认 HEAD）
--merge-base SHA        合并基底（默认 git merge-base）

# 输出
--output / -o PATH      写入文件（默认 stdout）；目标已存在则拒绝并 exit 2

# 启发式调节
--rewrite-retention-threshold FLOAT  默认 0.30
--rewrite-min-lines INT              默认 50
--migration-glob TEXT (multi)        默认 [**/migrations/*.sql, **/db/migrate/*, **/alembic/versions/*.py]
--cluster-min-files INT              默认 auto（max(3, n/20)）
```

**输出策略**：单一 `--output` flag。文件存在时 CLI **拒绝覆盖**并 exit 2，让用户主动 `rm`/`mv` —— 比 `--force` 更醒目。`init` 是稀有事件（fork 一生跑几次），不为它优化双 flag 写法。

退出码：`0`=draft 已生成 / `1`=git 失败 / `2`=`--output` 路径已存在。

### 4.2 `diff` —— 增量审阅（避免重跑 init）

启发式重跑 + yaml 自动 merge 是错的：用户已经手动删除过的误报条目下次会被加回来，用户改过的 `reason` 会被覆盖。正确思路是**让 yaml 始终是单一真源，git 只提供差异建议**。

```bash
merge forks-profile diff [OPTIONS]

--upstream REF                          同 init
--fork REF                              同 init
--merge-base SHA                        同 init
--profile / -p PATH                     yaml 路径（默认 <repo>/.merge/forks-profile.yaml）

# 启发式调节（与 init 共用）
--rewrite-retention-threshold FLOAT
--rewrite-min-lines INT
--migration-glob TEXT (multi)
--cluster-min-files INT

--exit-non-zero-on-diff                 有差异时 exit 1（CI gate 用）
```

输出（人类可读）：

```
📋 已声明但启发式不再检出（可能可删）:
  - removed_domains[smtp]
    rationale: 没有 D_MISSING 文件命中 path_globs；可能是该域已重新引入
                或上游也删了（双方一致 → A 类，无需声明）

➕ 启发式新检出但 yaml 未声明:
  - rewritten_module candidate: backend/services/notifications/**
    retention=18%, lines_changed=320, fork-only commits=8
    建议: 增补到 yaml 或确认这是预期分歧

🔄 yaml 与启发式分类不一致:
  - backend/services/auth/** 在 yaml 是 escalate_human，
    但启发式认为 retention=42%（未达 30% 重写阈值）
    可能可降级为 semantic_merge_with_alert
```

退出码：
- `0` = 完全一致 / 默认（即使有差异也 0，仅展示）
- `1` = `--exit-non-zero-on-diff` 模式下检测到差异
- `2` = profile 文件不存在

### 4.3 子命令何时用

| 阶段 | 子命令 | 频率 |
|---|---|---|
| fork 第一次接入合并系统 | `init -o .merge/forks-profile.yaml` | 一次 |
| 每次大版本合并前 | `diff` | 每次合并 |
| 每月 CI gate | `diff --exit-non-zero-on-diff` | 自动 |
| pre-commit / PR check | `validate` | 持续 |
| IDE schema 提示 | `schema -o forks-profile.schema.json` | 一次 |

## 5. 输出形式

```yaml
# Auto-drafted by `merge forks-profile init` on 2026-05-07
# Inputs: upstream/main..HEAD (merge-base abc1234)
# Stats: D_MISSING=42, D_EXTRA=18, B-rewritten=7, migration-collisions=0
#
# Review every entry below before committing.
# - The `removed_domains` are likely close — verify the `reason` text.
# - The `rewritten_modules` policy is set to escalate_human (safest);
#   downgrade to take_current_with_diff_note or semantic_merge_with_alert
#   only after reading the actual diff.
# - Delete entries that were over-classified (e.g. an unrelated test
#   cleanup misread as a "removed domain").

version: 1

fork:
  name: ""              # TODO: name your fork
  upstream: ""          # TODO: e.g. owner/repo
  positioning: ""       # TODO: one-line description

removed_domains:
  - name: payments      # TODO: confirm domain name
    paths:
      - "backend/services/payments/**"
    reason: "TODO: why was this dropped? (auto-detected from commit abc1234: 'remove billing layer')"
    removed_in: "abc1234"

rewritten_modules:
  - path: "backend/services/auth/**"
    policy: escalate_human   # TODO: consider take_current_with_diff_note / semantic_merge_with_alert
    note: "fork retains 12% of merge-base lines (rewrite threshold 30%)"

fork_only_features:
  - path: "packages/dashboard/src/features/visualizer/**"
    note: ""             # TODO: describe

# migration_policy:      # uncomment if relevant
#   path_globs: ["backend/db/migrations/*.sql"]
#   fork_owns_numbers_above: 25
#   upstream_take_target_max: 25
#   on_collision:
#     action: escalate_human
```

输出用 `ruamel.yaml` 而非 `pyyaml`，以保留注释与字段顺序。

## 6. 实施步骤与改动清单

| # | 文件 | 操作 | 工作量 |
|---|---|---|---|
| 1 | `src/tools/forks_profile_drafter.py` | **新增** — 4 个起草函数（`draft_removed_domains` / `draft_fork_only_features` / `draft_rewritten_modules` / `draft_migration_policy`）+ `cluster_paths` 公用聚类 + 数据模型 `DraftedProfile` | ~3h |
| 2 | `src/tools/forks_profile_differ.py` | **新增** — `diff_profile_vs_heuristic(yaml_profile, drafted) → ProfileDiff`；3 类差异（已声明未检出 / 新检出未声明 / 分类不一致） | ~2h |
| 3 | `src/cli/commands/forks_profile.py` | 改 — 增 `init` + `diff` 子命令；调用 drafter / differ；用 `ruamel.yaml` 保留注释序列化 | ~2h |
| 4 | `pyproject.toml` | 改 — 加 `ruamel.yaml` 依赖（runtime 需） | ~5min |
| 5 | `tests/unit/test_forks_profile_drafter.py` | **新增** — 4 起草函数各正反例 + 聚类边界用例（10/50/300 文件三档） | ~2h |
| 6 | `tests/unit/test_forks_profile_differ.py` | **新增** — 3 类差异分类 + 退出码 + 空 profile 边界 | ~1.5h |
| 7 | `tests/unit/test_cli_forks_profile.py` | 改 — 增 `init` / `diff` 子命令 CliRunner 测（mock GitTool） | ~1.5h |
| 8 | `doc/forks-profile-init.md` | 已生成（本文档） | ✅ |
| 9 | `doc/README.md` | 改 — 索引加一行（已完成） | ✅ |

**合计 ~12 工作时**，建议拆 2 个 PR 落地：

- **PR A**（~7h）：`init` + drafter + 单测 + ruamel.yaml 依赖
- **PR B**（~5h）：`diff` + differ + 单测，依赖 PR A 的 drafter 数据模型

PR B 也可独立合并 —— `diff` 内部仍跑 drafter 算 fresh draft，再与 yaml 对比，所以 differ 模块依赖 drafter 但 yaml 解析路径完全独立。

## 7. 测试策略

### 7.1 起草纯函数（最高覆盖）
- 用 `unittest.mock.MagicMock(GitTool)` 喂入合成的 `compute_fork_divergence_map` 输出
- 断言：聚类后路径列表、TODO 标记、retention 阈值边界

### 7.2 聚类启发式
- 10 / 50 / 300 文件三档，验证自适应 N 行为
- 路径深度悬殊（如 `a.py` 与 `a/b/c/d/e.py` 混合）的孤儿处理

### 7.3 CLI 端到端
- `init -o PATH`：目标已存在拒绝（exit 2），目标不存在写入成功（exit 0）
- `init` 默认 stdout 模式可被 `> profile.yaml` redirect 直接生效
- yaml 输出能被 `merge forks-profile validate` 通过
- `diff -p PATH`：profile 不存在 exit 2；完全一致 exit 0；
  `--exit-non-zero-on-diff` 有差异时 exit 1
- `diff` 输出三类标题（📋 / ➕ / 🔄）至少各覆盖一个用例

### 7.4 真实仓库 smoke（手动）
- 在已有的目标 fork 上跑 `merge forks-profile init`
- 检查能否复现已知的 removed_domain / rewritten_module
- 跑几个月后再跑 `merge forks-profile diff`，检查差异分类是否合理
- 误报率 / 漏报率统计写入 [test-report/](test-report/)

## 8. 不做的事

| 项 | 理由 |
|---|---|
| 不用 LLM 给 `reason` / `note` 起草中文 | 起草器必须 0 网络 0 API key，能在 fork PR 流水线裸跑；reason 由人改最准 |
| **init 不自动 merge 进已有 yaml** | 启发式重跑会撤销用户人工删除的误报 / 覆盖 reason 的人工编辑；增量场景走 `diff` 子命令让人审阅每一项 |
| **init 拒绝覆盖已存在文件，且不提供 `--force`** | 覆盖是不可逆决定，让用户显式 `rm` / `mv` 比命令行 flag 更醒目；`init` 一生跑几次，无须优化 |
| ~~不把 `init` / `diff` 接入 `merge <branch>` 主流程~~ **已撤销 (§9 PR-B/C)** | 原顾虑是"主流程必须可重入幂等"。整合后保留幂等性的方式：init 仅在首次向导（无 yaml + 阈值触发）跑一次；diff 每次 run 跑但**只读**，仅写 `state.forks_profile_drift` 与 plan 报告附录，不修改 yaml |
| 不在 init / diff 时检查 `migration_policy` 的实际碰撞 | 那是 plan-stage routing 的职责（已由 `compute_auto_overlay` + `find_migration_collision` 实现） |
| 不输出 JSON 格式 | yaml 注释承载关键的 TODO 信息；diff 的人类可读输出非结构化 |

## 9. 已知局限

| 局限 | 缓解 |
|---|---|
| **rewritten 启发式漏报**：fork 把模块整体改写但保留 30% imports / types | 用户审阅时通过 git diff 自验；后续可加 `--rewrite-retention-threshold 0.5` 让用户调阈值 |
| **rewritten 启发式误报**：单文件大量 lint / format 改动会被算"重写" | 同上；输出含 retention 数字便于判断 |
| **聚类粒度争议**：`backend/services/payments/v1/**` 还是 `backend/services/**`？ | 默认聚到「包含 ≥ N 文件的最深前缀」，用户可改 `--cluster-min-files` |
| **migration glob 项目相关**：每种框架不一样 | 默认 3 个常见 glob；用户用 `--migration-glob` 覆盖 |

## 10. 关联资料

- §9 RFE 全文：[multi-agent-optimization-from-merge-experience.md](multi-agent-optimization-from-merge-experience.md)
- 已落地 schema / loader：[`src/models/forks_profile.py`](../src/models/forks_profile.py)、[`src/tools/forks_profile_loader.py`](../src/tools/forks_profile_loader.py)
- 现有 fork-divergence 推断：[`src/tools/file_classifier.py::compute_fork_divergence_map`](../src/tools/file_classifier.py)
- 现有 line-retention 算法：[`src/tools/config_line_retention_checker.py`](../src/tools/config_line_retention_checker.py)
- 现有 CLI scaffolding：[`src/cli/commands/forks_profile.py`](../src/cli/commands/forks_profile.py)
