# Per-Hunk Resolution — 设计文档

> Round 3 暂缓项。本文档锁定问题陈述、用户工作流、schema 变更、UI 草图、后端 dispatch、测试矩阵、风险与边界条件。在实施前不写代码。
>
> 最后更新：2026-05-19

---

## 1. 问题陈述

### 1.1 当前选项的局限

经过 Round 2/3 改造后，HUMAN_REQUIRED 文件最多有 6+N 个选项（base 3 + `llm_with_instruction` + `manual_paste` + `skip` + 数据驱动的 `union_additions` + N 个 `analyst_proposed`）。但所有选项的粒度都是 **整文件**：

| 选项 | 粒度 |
|---|---|
| `keep_head` | 整文件取 fork |
| `take_target` | 整文件取 upstream |
| `llm_default` / `llm_with_instruction` / `analyst_proposed` | 整文件由 LLM 重新组装 |
| `union_additions` | 整文件做 `git merge-file --union` |
| `manual_paste` | 整文件粘贴最终内容 |
| `skip` | 整文件保留 fork 内容 |

### 1.2 真实痛点

很多 C-class 三路冲突 *不是整文件级别* 的分歧。典型场景：

- **`models/auth/auth_token.go`** — 头部 import 块需要并集，中部新增 struct 字段需要并集，但底部一个工具函数 fork 改了实现、upstream 改了签名 → 需要逐块决策
- **`routers/web/auth/oauth.go`** — 中段的 session 审计 fork 保留、上游新增的 OIDC silent auth 逻辑也保留，但两侧对 `promptParam` 处理冲突 → 需要选 fork 或 upstream
- 重构合并：fork 重命名了局部变量、upstream 抽出了一个 helper —— 两边都不能整文件 take

整文件选项面对这类需求只能：
1. 选 LLM 自动合并（黑盒、可能错）
2. 选 manual_paste（用户离开 UI 去本地手工合，再粘回来 —— 与"在系统里完成审查"的初衷相悖）

per-hunk 选项是这两者之间的中间地带：**人审者保留对每个 hunk 的最终判断权，但不离开 UI**。

---

## 2. 用户工作流（目标体验）

### 2.1 入口

HUMAN_REQUIRED 文件卡片的选项列表里多出一项：

```
[ Pick hunks side-by-side ]   kind=per_hunk
```

点击后展开一个 **行内 hunk picker**（不另开页面、不弹窗 modal，保持单页心智）。

### 2.2 Hunk Picker 视图（草图）

```
File: models/auth/auth_token.go                                [ Close picker ]
─────────────────────────────────────────────────────────────────
Hunk 1 of 4 — Lines 12-18 (imports block)
  Pick:  ◉ FORK   ○ UPSTREAM   ○ BASE   ○ CUSTOM
  ┌───────────────────────────────┬───────────────────────────────┐
  │ FORK (current)                │ UPSTREAM (target)             │
  │ ────────────                  │ ──────────────                │
  │ import (                      │ import (                      │
  │     "context"                 │     "context"                 │
  │     "time"                    │     "errors"                  │
  │     "audit"        ← fork add │     "optional"     ← upstream │
  │ )                             │ )                             │
  └───────────────────────────────┴───────────────────────────────┘
  Custom (only shown when CUSTOM selected):
  ┌────────────────────────────────────────────────────────────┐
  │ import (                                                    │
  │     "context"                                               │
  │     "time"                                                  │
  │     "audit"                                                 │
  │     "errors"                                                │
  │     "optional"                                              │
  │ )                                                           │
  └────────────────────────────────────────────────────────────┘

Hunk 2 of 4 — Lines 24-37 (struct AuthorizationToken)
  Pick:  ○ FORK   ○ UPSTREAM   ◉ UNION   ○ CUSTOM
  ...

[ Preview merged file ]   [ Apply ]   [ Cancel ]
```

要点：
- 每个 hunk 一个独立的"选择条" + 内容对比
- 选 side 即时着色（fork=绿、upstream=蓝、base=灰、custom=accent）
- 切到 CUSTOM 显示一个 hunk 级的小 textarea，预填当前 side 内容
- 底部 "Preview merged file" 弹一个 read-only 全文预览（拼接所有 hunk 选择 + 未冲突区域）
- "Apply" 把 per-hunk 选择存进 UserDecisionItem 提交

### 2.3 三种简化退路

为防止 hunk picker 过度复杂，提供三个"快速键"：
- **Take all fork** — 把所有 hunk 选 FORK 后等价于 `keep_head`
- **Take all upstream** — 等价于 `take_target`
- **Smart union** — 对所有"双侧只 add"的 hunk 选 UNION，其他用 FORK（一键近似 `union_additions`，但区分了 add-only 和真冲突）

---

## 3. Schema 变更

### 3.1 `UserDecisionItem` 新增字段

```python
class UserDecisionItem(BaseModel):
    ...  # 已有字段
    per_hunk_choices: list["PerHunkChoice"] | None = Field(default=None)


class PerHunkChoice(BaseModel):
    hunk_id: str               # 与 FileDiff.hunks[].hunk_id 对应
    side: Literal["fork", "upstream", "base", "union", "custom"]
    custom_content: str | None = None  # 仅当 side == "custom" 时使用
```

### 3.2 `DecisionOptionKind` 新增

```python
DecisionOptionKind = Literal[
    ...,  # 已有
    "per_hunk",
]
```

### 3.3 `_build_decision_options` 始终 emit `per_hunk`

类似 Round 3 的 `manual_paste` / `skip`，per_hunk 始终对 HUMAN_REQUIRED 文件 emit；UI 决定要不要展开 picker。

---

## 4. 后端实现

### 4.1 Hunk 装配算法

新工具：`src/tools/hunk_assembler.py`

```python
def assemble_per_hunk(
    base_content: str,
    fork_content: str,
    upstream_content: str,
    hunks: list[DiffHunk],
    choices: list[PerHunkChoice],
) -> tuple[str, list[str]]:
    """根据 hunk 级选择装配最终文件内容。

    返回 (assembled_content, warnings)。warnings 收集装配过程中的
    告警：hunk 重叠 / 行号偏移修正 / custom 内容语法疑似异常等。
    """
```

设计要点：
- 用 base 作为"骨架"，按 hunk 列表逐块替换
- fork / upstream 内容从 `git_tool.get_three_way_diff` 已能拿到（complete content per ref）
- hunk 之间不重叠（git merge-file 输出保证）
- side==union 时调一次 `git merge-file --union` 仅对该 hunk 跑（用 `git merge-file --stdout` 加临时三文件）—— 复用 Round 2 `three_way_merge_file_union` 的逻辑封装
- side==custom 时直接用 `custom_content` 替换该 region

边界条件：
- hunk 数量不匹配（用户选的 hunk_id 不在 file 当前 hunks 里）→ 抛 `AssemblyError`，落回 ESCALATE_HUMAN
- side==base 但 base_content 不存在（文件是 D-missing） → 退化为 ""（删除该 region 内容）
- side==union 但 `git merge-file --union` 单 hunk 不可调（git 不直接支持 sub-file union）→ 用工具内 union 实现（基于行的简单 union：保留 fork 所有 add + upstream 所有 add，base 行做 anchor）

### 4.2 Executor L5 dispatch

`auto_merge.py` 的 L5 ladder 加分支：

```python
_l5_per_hunk_keys = {"per_hunk"}

# 与 manual_paste 同形态，但内容来自装配
if choice in _l5_per_hunk_keys:
    choices = item.per_hunk_choices or []
    if not choices:
        # 用户选了 per_hunk 但没有提交 hunk 选择 → ESCALATE_HUMAN
        ...
        continue
    base_c, fork_c, up_c = ctx.git_tool.get_three_way_diff(
        state.merge_base_commit, state.config.fork_ref,
        state.config.upstream_ref, fp,
    )
    fd = next((x for x in state.file_diffs if x.file_path == fp), None)
    if fd is None or not fd.hunks:
        ...  # ESCALATE
        continue
    try:
        assembled, warnings = assemble_per_hunk(
            base_c, fork_c, up_c, fd.hunks, choices,
        )
        record = await apply_with_snapshot(
            fp, assembled, ctx.git_tool, state,
            phase="auto_merge", agent="user_choice_executor",
            decision=MergeDecision.MANUAL_PATCH,
            rationale=f"O-L5 per_hunk: assembled from {len(choices)} hunk choices. {'; '.join(warnings) if warnings else 'no warnings'}",
        )
    except AssemblyError as exc:
        # ESCALATE_HUMAN with detailed reason
        ...
```

### 4.3 ws_bridge 数据通路

`_apply_user_plan_decisions` 已经 `model_copy` 整个字段集合，只需要把 `per_hunk_choices` 也加进 update 字典：

```python
updated = existing.model_copy(
    update={
        ...,
        "per_hunk_choices": item_data.get("per_hunk_choices"),
    }
)
```

---

## 5. 前端 UI 实现

### 5.1 组件层级

```
PendingItemCard
  └─ HunkPickerExpansion   (新增，仅在选 per_hunk 时显示)
      ├─ HunkChoiceRow × N  (一行一个 hunk)
      │   ├─ SideRadioGroup (fork / upstream / base / union / custom)
      │   ├─ SideContentPane × 2 (fork + upstream side-by-side)
      │   └─ CustomTextarea (条件渲染)
      ├─ QuickActionsBar (Take all fork / Take all upstream / Smart union)
      └─ PreviewMergedFileButton (打开 read-only 全文预览)
```

### 5.2 状态管理

新增 store：`web/src/store/perHunkDraftStore.ts`

```typescript
interface PerHunkDraft {
  [itemId: string]: {
    choices: PerHunkChoice[];  // 每 hunk 一项
  };
}
```

写入逻辑：
- 进入 picker 时初始化 `choices` 为每 hunk side=fork（默认值，与"什么都没选" = `keep_head` 一致）
- 切换 side / 编辑 custom 都更新这个 store
- "Apply" 时把 `per_hunk_choices` 塞进 `submit_user_plan_decisions` payload

### 5.3 Hunk 数据来源

`PendingUserDecision` 当前没有 hunk 列表 —— 需要扩展 schema 把 `state.file_diffs[fp].hunks` 序列化进 payload：

```python
# src/web/serializers.py
"hunks": [
    {
        "hunk_id": h.hunk_id,
        "start_line_current": h.start_line_current,
        "end_line_current": h.end_line_current,
        "start_line_target": h.start_line_target,
        "end_line_target": h.end_line_target,
        "content_current": h.content_current,
        "content_target": h.content_target,
        "content_base": h.content_base,
    }
    for h in file_diffs_map.get(item.file_path, FileDiff(...)).hunks
] if item.current_classification == "human_required" else None,
```

注意 payload 大小：HR 文件通常 hunks 数 ≤ 10，每 hunk content_current/target ≤ 1KB，总增量 < 20KB / 文件 —— 可接受。

### 5.4 性能边界

- hunks 数量超过 20 时折叠多余 hunk（"Show all hunks" 按钮展开）
- custom textarea 单 hunk 内容 > 10KB 给警告（推荐用 manual_paste）

---

## 6. 测试矩阵

### 6.1 后端单测（`tests/unit/test_hunk_assembler.py`）

| 用例 | 期望 |
|---|---|
| 所有 hunk 选 fork | 等价于 `keep_head`，输出 == fork_content |
| 所有 hunk 选 upstream | 等价于 `take_target`，输出 == upstream_content |
| 全选 base | 输出 == base_content |
| 部分 fork、部分 upstream | hunk-by-hunk 替换正确 |
| 单 hunk union（add-only） | 该 region fork+upstream 行都保留 |
| 单 hunk custom | 该 region 内容 == custom_content |
| hunk_id 不匹配 | 抛 `AssemblyError` |
| 空 choices 列表 | 抛 `AssemblyError`（应由 dispatcher 提前处理） |
| 重叠 hunk 区间 | warning 输出 + 选最后一个 |
| base 为 None（D-missing） | 选 base 时该 region 为空字符串 |

### 6.2 后端集成（`tests/unit/test_per_hunk_dispatch.py`）

| 用例 | 期望 |
|---|---|
| `user_choice="per_hunk"` + 有效 choices | `apply_with_snapshot` 被调用，FileDecisionRecord decision=MANUAL_PATCH |
| `user_choice="per_hunk"` + 空 choices | 写 ESCALATE_HUMAN |
| AssemblyError | 写 ESCALATE_HUMAN + log warning |

### 6.3 前端单测（`web/src/views/HunkPicker.test.tsx`）

| 用例 | 期望 |
|---|---|
| 渲染 N hunk → N 行 | OK |
| 切换 side → store 更新 | OK |
| Take all fork 按钮 → 所有 choices.side = fork | OK |
| Apply → 调 `submit_user_plan_decisions` 含 per_hunk_choices | OK |
| custom 模式 → textarea 显示，store 接 custom_content | OK |
| Preview merged file → 调后端 dry-assemble API 或前端简单拼接 | OK |

### 6.4 E2E（手工，forgejo 测试分支）

- 对 `models/auth/auth_token.go` 做 4 hunk 混合选择，确认装配后 struct 字段保留全部 + 工具函数只保留 fork 改动
- 对 `routers/web/auth/oauth.go` 做 union+fork 混合，验证 OIDC 逻辑保留 + audit 也保留

---

## 7. 风险与边界

### 7.1 主要风险

| 风险 | 缓解 |
|---|---|
| Hunk 装配算法 bug 写出语法错误的文件 | quality gate 已有（mypy / lint），失败时 patch_applier 回滚；同时算法本身不重排行号，只做"替换 region"，bug 概率较低 |
| 用户漏选某个 hunk（picker 跳过） | 默认所有 hunk 初始化为 `side=fork`，等价于"什么都不改"，安全保守 |
| custom textarea 内容包含 conflict marker（`<<<<<<<`） | 装配后 quality gate 会检测并 fail；建议 UI 端预警 |
| 大文件 100+ hunks | UI 限定折叠 + "Apply" 时聚合提交一次，不会卡 |
| `per_hunk` 与 `analyst_proposed` 共存 | analyst proposal 仍可被选；选 analyst → 走 LLM；选 per_hunk → 走装配算法。互斥（用户只能选一个 user_choice）。 |
| Hunk schema 变化（PostHook 重新解析 file_diffs 后 hunk_id 不稳定） | hunk_id 当前是 uuid4，每次解析变化。需要把 hunk_id 改成稳定 hash（content_current + start_line 内容的 sha1 前 8 位） |

### 7.2 已知边界条件

- **二进制文件**：禁用 per_hunk 选项（与 manual_paste 同理）
- **D-missing 文件**：禁用 per_hunk（没有 fork 侧 hunks 可对比）
- **`file_diff.hunks` 为空但 file 还是 HUMAN_REQUIRED**（极少见，初始化器没产生 hunks）：禁用 per_hunk
- **Resume 场景**：重启 run 后 file_diffs 重新解析 → hunk_id 可能变化 → 老的 `per_hunk_choices` 失效。解决：每次进入 picker 时按 `(start_line, end_line)` 反向匹配 cached choices，匹配不上就重置。

### 7.3 不在范围内的内容

- **inline diff 内的 word-level 选择**（"这一行的前半段用 fork、后半段用 upstream"）—— 超过 per_hunk 粒度，留给 `manual_paste`
- **跨文件 hunk 互动**（"在 A 文件改这里，B 文件就这样跟着改"）—— 系统不应自动推断，由用户分别决策

---

## 8. 实施拆分（推荐 3 个 PR）

### PR 1: 后端 hunk API + 装配算法（无 UI 改动）

| 改动 | 文件 |
|---|---|
| 新工具 `assemble_per_hunk` + `AssemblyError` | `src/tools/hunk_assembler.py` |
| `DiffHunk.hunk_id` 改为内容 hash（向后兼容：旧 uuid 仍是合法 hunk_id） | `src/models/diff.py` + `src/tools/diff_parser.py` |
| schema：`UserDecisionItem.per_hunk_choices` + `PerHunkChoice` 模型 | `src/models/plan_review.py` |
| `DecisionOptionKind` 加 `per_hunk` | `src/models/plan_review.py` |
| `_build_decision_options` 始终 emit `per_hunk`（HR 且 hunks 非空） | `src/core/phases/plan_review.py` |
| L5 dispatch 接管 `user_choice == "per_hunk"` | `src/core/phases/auto_merge.py` |
| ws_bridge 把 `per_hunk_choices` 透传进 update | `src/web/ws_bridge.py` |
| serializer 输出 `per_hunk_choices` + `hunks` 给前端 | `src/web/serializers.py` |
| 单测 `test_hunk_assembler.py` + `test_per_hunk_dispatch.py` | `tests/unit/` |

约 600-800 行（含测试）。落地后 UI 还看不到 per_hunk 选项 —— 测试是后端契约。

### PR 2: 前端 HunkPicker 视图

| 改动 | 文件 |
|---|---|
| schema 镜像 `PerHunkChoice` + `HunkInfo` | `web/src/types/state.ts` |
| 新 store `perHunkDraftStore` | `web/src/store/` |
| 组件 `HunkPicker` + `HunkChoiceRow` + `SideContentPane` + `QuickActionsBar` | `web/src/views/` |
| `PendingItemCard` 集成（kind==per_hunk 时展开） | `web/src/views/PlanReview.tsx` |
| `buildItemsPayload` 把 per_hunk_choices 塞进 payload | `web/src/store/planReviewDraftStore.ts` |
| 单测覆盖 | `web/src/views/HunkPicker.test.tsx` |

约 600-1000 行（前端组件 + tests）。

### PR 3: Preview merged file + Quick actions 完整化

| 改动 | 文件 |
|---|---|
| 后端"dry-assemble" API（不写盘，只返装配结果给 UI 预览） | `ws_bridge` 新消息类型 + `serializers` |
| 前端 "Preview merged file" modal | `web/src/views/MergedFilePreview.tsx` |
| Smart union 快速键算法（区分 add-only hunks） | 前端逻辑 |
| E2E 手工测试报告（forgejo 测试分支） | `doc/test-report/` |

约 300-500 行。可选地与 PR2 合并。

---

## 9. 开放问题

- [ ] hunk_id 改 hash 是否影响其他模块（conflict_analysis / executor）？需要全局 grep `hunk_id` 跟踪
- [ ] Preview merged file 用前端直接拼接还是后端 dry-assemble？前端简单但行号偏移容易错；后端权威但要新 WS 消息
- [ ] `analyst_proposed` 选项是否需要支持 per-hunk 形态（即 analyst 提议"hunk 1 用 fork、hunk 2 用 upstream"）？— 不在初版范围
- [ ] keyboard shortcut（j/k 切 hunk，1/2/3/4 切 side） — UX 加分项，PR 2 之后再加
- [ ] Hunk picker 进入/退出动画 — 纯 UX，可后置
