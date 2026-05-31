# Web UI 重构（TUI → 浏览器）交接文档

> 多 agent 协作（team-lead / planner / verifier / executor / gatekeeper）将 React Ink TUI 替换为浏览器 Web UI；本文档为本会话**未完结**的工作的交接，供下次会话恢复使用。
>
> 写入时间：2026-05-15（feat/web 分支）。

---

## 1. 总目标与不可重新讨论的关键决策

**目标**：把 `merge <branch>` 默认 UI 从 React Ink TUI 换成浏览器 Web 页面。启动方式不变；启动后自动打开浏览器；尽可能展示每个 agent 的关键节点与产物，重点优化人工决策（plan review、conflict resolution、judge verdict）。

**已与用户对齐、不要重新讨论的决策**：

| 决策 | 取值 |
|---|---|
| 前端栈 | **React 18 + Vite + TypeScript**（strict） |
| TUI 处理 | **完全替换**（删除 `tui/`），`--no-tui` / `--tui` 退化为 hidden deprecated alias，CLI `--ci` 保留无 UI 路径 |
| 后端通信 | **复用 `src/web/ws_bridge.py`**，按需扩展；不重写 FastAPI/aiohttp |
| 范围 | **MVP 可用**（L1 实时进度 / L2 PlanReview / L3 ConflictResolution / L4 Judge / L5 Report 全套核心交互页） |
| 工作模式 | 无人值守，所有内部决策由 team-lead（main agent）自行裁决 |

**额外的多 agent 协作裁决**（本会话已被使用，下次会话沿用）：

- 路径 A cancel：`MergeWSBridge._cancel_event` 内自管，**不动 Orchestrator**（即 plan v1.1 §P1-2）
- localhost-only：**不引入 ws token 鉴权**；URL query 只 `?ws=<port>`
- 两步提交协议（plan v1.1 §P1-3）：L2 PlanReview 在前端用本地 `pendingUserDecisionDraft`，仅在 `Approve all` 触发 `submit_user_plan_decisions` + `submit_plan_review approve` 两帧；`Reject` 路径**绝不**发 `submit_user_plan_decisions`（绕过 `_apply_user_plan_decisions` 自动 APPROVE 副作用）
- M6（线程安全）已修：`notify_agent_activity` 用 `call_soon_threadsafe` marshal 到 event loop 线程
- M13 修复（合并 Phase 4）：`serverDecided` 改为 `snapshot?.planHumanReview != null`，serializer 透出 `plan_human_review`
- L4 用**独立** `_judge_resolution_received` event（不复用 `_plan_review_received`，避免语义被 abuse）
- L5 markdown 渲染用 **marked**（轻量），加 DOMPurify 防御层
- 端到端手测路径：用 `web/dev/mock-bridge.py` 推 mock state_snapshot 验证浏览器渲染（避免依赖真实 fork repo + LLM API key）

---

## 2. Team 拓扑与协作约定

Team 名：`web-ui-redesign`（位于 `~/.claude/teams/web-ui-redesign/config.json`，共享 task list 在 `~/.claude/tasks/web-ui-redesign/`）

| 角色 | name | subagent_type | 权限 | 状态 |
|---|---|---|---|---|
| Team-lead | (main agent) | n/a | n/a | 不在 team 内，外部调度 |
| Planner | `planner` | Plan | 只读 | 已完成 v1 + v1.1 |
| Gatekeeper | `gatekeeper` | general-purpose | 只读 | 审 7 次（Phase 0/H1/Phase 1/Phase 2/H3/Phase 3，本会话末等待 Phase 4 审） |
| Verifier | `verifier` | general-purpose | 只读 | 测试矩阵 v1.1 已交，gatekeeper 曾给 3 P1 must-fix；v1.1 修订后未再被审查（plan §8 测试矩阵已在 Phase 0-3 实施中实际达成，无关键缺口） |
| Executor | `executor` | general-purpose | 读写 | 已提交 7 个 commit；Phase 4 本会话末进行中 |

**协作约定**（已沉淀，新会话直接复用）：

- Peer DM：teammate 完成产出 → 自己 SendMessage gatekeeper；审完直接回 sender；main agent 仅在节点出现
- Gatekeeper 反馈格式：开头必须列「## 已通过事实（禁止改动）」防止后续 round 改回已 OK 的部分
- 每个 Phase commit 前必须跑：`pytest tests/unit/ -q --cov=src --cov-fail-under=80` + `mypy src` + `ruff check src/` + `cd web && npm run build` + `npm test`（vitest）+ 浏览器手测（mock-bridge）
- commit message 用中文 conventional commits（`feat(web)` / `fix(web)`），不带 Co-Authored-By
- Executor 启步每个 Step 前发简短 `SendMessage team-lead`（"Step X 起步，预计 Y 分钟"），避免长时间静默被误判卡死
- Executor 遇 plan 未覆盖边界时停手，列 A/B/C 三选项给 team-lead，30 分钟超时默认按推荐选项推进

**已发现的失败模式（下次会话注意防止）**：

1. Executor 长时间静默：本会话出现过 ~6h 不发消息但实际在工作。team-lead 在 git status 看到改动但 Executor 不报 → main agent 接手 verify 浪费成本。约束已加固："每 Step 前发起步消息"。
2. 消息延迟：本会话出现 Executor 报文延迟 ~7h 到达（Step 1-5 报告与询问 A/B/C 三连消息同一时刻到达）；main agent 已开始接手时收到。处理方法：在仓库直接 `git log` 看 commit 进度作为权威信号，不全靠消息。
3. 旧测试断言路由：Phase 0 改 CLI 路由后，`test_cli_flow.py::test_merge_routes_to_tui_by_default` 与 `test_resume_tui_dispatch.py` 必然红，与"不动旧测试"指令冲突；最终用 A 路径（最小化更新 mock target + kwargs，文件改名留 Phase 5）。

---

## 3. 事实锚点清单（main agent 核实过，下次会话直接信任）

> 形式：`file:line — 含义`。grep 验证一次即可。

### CLI 与启动
- `src/cli/main.py:75-151` — `merge_command`，已改造为默认走 Web。`--no-tui` / `--tui` 为 hidden alias（DeprecationWarning + click.echo 双层提示）。新增 `--no-web` / `--no-browser` / `--web-port`（默认 5173） / `--ws-port`（默认 8765）。
- `src/cli/commands/resume.py:87-94, 211-215` — 已切到 `web_resume_impl`；`--tui` hidden alias，新增 `--web` / `--web-port` / `--no-browser`。
- `src/cli/commands/web.py:1-167`（Phase 0 新建）— `web_command_impl` / `web_resume_impl` / `_run_web` / `_resolve_web_dist` / `_bridge_cancelled`（Phase 0 stub，Phase 1 经 `is_cancelled()` 自动激活）。
- `src/cli/commands/tui.py` — **未删**，仍在仓库，等 Phase 5 整目录删除。

### 后端通信
- `src/web/ws_bridge.py`（Phase 1 起从 682 行减至 ~390 行）：
  - `MergeWSBridge.start(host, port=8765)`
  - `_handle_command` 入站类型：`submit_decision` / `submit_conflict_decisions_batch` / `submit_plan_review` / `submit_user_plan_decisions` / `pause` / `resume` / `cancel_run` / `submit_judge_resolution`（Phase 4 新增）
  - `_cancel_event` + `is_cancelled()`（Phase 1）；`_judge_resolution_received`（Phase 4 计划新增）
  - ring buffer：`ACTIVITY_BUFFER_MAX = 200`
  - 出站 type：`state_snapshot` / `agent_activity` / `agent_activity_replay` / `cancel_error`
- `src/web/serializers.py`（Phase 1 拆分）— `serialize_state` 已透出：runId / status / currentPhase / phaseResults / mergePlan / fileClassifications / fileDiffs / fileDecisionRecords / humanDecisionRequests / humanDecisions / judgeVerdict / judgeRepairRounds / planReviewLog / reviewConclusion / pendingUserDecisions / gateHistory / errors / messages / memory / costSummary / phaseElapsed / decisionRecordCounts。Phase 4 还应加：`planHumanReview` / `judgeResolution` / `maxJudgeRepairRounds`（或 frontend 写死 3）。
- `src/web/static_server.py` — stdlib ThreadingHTTPServer + SPA fallback + 路径穿越防护（`resolve().relative_to(root_resolved)`）。Phase 4 需加 `/runs/<id>/*` 路由。

### Orchestrator 与 phases
- `src/core/orchestrator.py:193-194` — `set_activity_callback(cb)` 注册 `ActivityEvent` 回调。
- `src/core/state_machine.py:12-71` — 13 个 SystemStatus 状态机；`AWAITING_HUMAN` 是唯一人工决策门。
- `src/core/phases/base.py:29-39` — `ActivityEvent(agent, action, phase, event_type, elapsed, extra)`；`ctx.notify(agent, action)`。
- `src/core/phases/human_review.py:148-161` — Judge 阻塞：`judge_verdict != None && current_phase == JUDGE_REVIEW && judge_resolution is None` → AWAITING_HUMAN。

### 数据模型
- `src/models/state.py:38-51` — `SystemStatus` 枚举；`:152-159` — `judge_resolution: Literal["accept","abort","rerun"] | None`。
- `src/models/decision.py:8-14` — `MergeDecision`：TAKE_CURRENT / TAKE_TARGET / SEMANTIC_MERGE / MANUAL_PATCH / ESCALATE_HUMAN / SKIP
- `src/models/human.py:16-36` — `HumanDecisionRequest`
- `src/models/conflict.py:25-37` — `ConflictPoint` 含 upstream_intent / fork_intent / can_coexist / suggested_decision / confidence / rationale / risk_factors / hunk_id
- `src/models/plan_review.py:40-55` — `UserDecisionItem`（**plan-level**，注意：`DecisionOption.key/label/description` 不含 MergeDecision 枚举；与 L3 的 `HumanDecisionRequest.options` 字段语义不同）
- `src/models/plan_review.py:7-10` — `PlanHumanDecision`：APPROVE / REJECT / MODIFY

### 前端
- `web/package.json` — Vite 6 + React 18 + TS strict + Zustand + Tailwind + Radix Dialog/Tooltip + lucide-react + react-diff-viewer-continued
- `web/src/`：
  - `App.tsx` — 用 `useWsClient` hook + `classifyView` derived selector 切屏
  - `lib/classifyView.ts` — 路由优先级：**L2 > L3 > L4 > L1**（L5 终态最高），实现状态：L2/L3 已实施，L4/L5 Phase 4 进行中
  - `ws/client.ts` — `resolveWsUrl()` 从 `?ws=` URL 参数解析；指数退避重连 500ms→30s + manuallyClosed flag
  - `store/` — zustand：snapshotStore（state_snapshot 镜像）/ activityStore / conflictDraftStore（L3）/ planReviewDraftStore（L2，含 commitApprove/Reject/Modify 两步协议）
  - `components/` — StatusBanner / PhaseTimeline / AgentActivityStream / CostCard / DecisionCountsCard / RiskBadge（L1）+ FileTree / DiffViewer / DecisionPanel / ConflictPointMarker / BatchActionBar（L3）+ PlanTree / NegotiationTimeline / PlanDecisionDrawer / PlanReviewBatchBar / PendingDecisionsList（L2）
  - `views/` — RunDashboard / ConflictResolution / PlanReview

### 测试与 dev tools
- `tests/unit/test_web_command.py`（Phase 0 新建，3 cases）
- `tests/unit/test_serializers.py`（Phase 1）
- `tests/unit/test_ws_bridge_extensions.py`（Phase 1，cancel + replay）
- `tests/unit/test_ws_bridge_threading.py`（M6 修复，跨线程并发）
- `tests/unit/test_ws_bridge_h3_reviewer_fields.py`（H3，reviewer/custom_content 透传）
- `web/dev/mock-bridge.py` + `web/dev/README.md` — MOCK_VIEW=dashboard/conflict/plan_review 三场景；Phase 4 加 judge/report 两场景
- 覆盖率门槛：plan v1.1 §8 要求 `src/web/` ≥85%（serializers 92% 已超；ws_bridge Phase 1 起 42%→63% 持续上升；static_server 37% 待补；server.py 0% 待 Phase 5 删除）

---

## 4. Commit 历史（按时间）

| Hash | Phase | 标题 | Verify |
|---|---|---|---|
| `d0c18af` | Phase 0 | `feat(web): Phase 0 — Web UI 脚手架 + CLI 路由切换` | 2173 passed |
| `595270f` | H1 hotfix | `fix(web): 移除 src/web/* 覆盖率排除，纳入 80% 门槛` | TOTAL 80.88% |
| `bbee60d` | Phase 1 | `feat(web): Phase 1 — cancel_run + serializers 拆分 + L1 Dashboard` | 2205 passed / 81.51% |
| `bdd326a` | M6 hotfix | `fix(web): M6 — notify_agent_activity 跨线程安全 marshal 到 event loop` | 2209 passed / +4 threading tests |
| `702484b` | Phase 2 | `feat(web): Phase 2 — L3 Conflict Resolution` | 2213 passed / 81.55% |
| `c306def` | H3 hotfix | `fix(web): H3 — manual_patch reviewer_notes/custom_content 端到端传输` | 2220 passed / 81.83% / ws_bridge cov 45→63% |
| `24509de` | Phase 3 | `feat(web): Phase 3 — L2 Plan Review 决策视图` | 2220 passed / 81.83% / vitest 45 cases |
| `8cac90c` | Phase 4 | `feat(web): Phase 4 — L4 Judge verdict + L5 Report & Memory + M13/M14/M15 修复` | 2237 passed / **82.06%** / static_server 37→94% / vitest 57 cases / 164 modules / chrome-devtools L4+L5 实测 |

> 下次会话首先 `git log --oneline` 确认 Phase 4 是否落地，并核对 Verify 摘要。

---

## 5. 各 Phase 状态

### Phase 0 / 1 / 2 / 3 — **闭环 GO**
Gatekeeper 全部 final ACK。L1（实时 dashboard）/ L2（plan review）/ L3（conflict resolution）已在浏览器手测全过。

### Phase 4 — **本会话末进行中**
**任务**：L4 Judge Verdict 视图 + L5 Report & Memory 视图 + M13 修复（合并）

**已确认实施细节**（团队对齐过，下次会话恢复时核对 git log 是否已 commit）：

1. **M13 修复**（独立或合并 Phase 4 commit 皆可）
   - `serializers.py`：透出 `plan_human_review.model_dump(mode="json") if not None else None`
   - `web/src/types/state.ts`：加 `planHumanReview?` 字段
   - `PlanReview.tsx:54-60`：`serverDecided = snapshot?.planHumanReview != null`
   - 顺便收：M14（直接 import OutboundMessage，去 `Parameters<NonNullable<...>>[0]` 反模式）+ M15（grep planner_agent 验 options[0]==recommended，注释 gate id）

2. **L4 Judge Verdict 视图**
   - classifyView 加 L4 分支：`AWAITING_HUMAN && judgeVerdict != null && judgeResolution == null`，优先级 **低于 L2/L3，高于 L1**
   - serializer 加 `judgeVerdict` 完整字段 + `judgeResolution` + `maxJudgeRepairRounds`（若 config 无字段则前端写死 3）
   - 组件：VetoBanner（veto_triggered 时显示 veto_reason 红色横幅）/ IssuesList（按 file_path 分组，severity 配色）/ Failed files diff（复用 DiffViewer）/ RepairInstructionsList / RepairRoundsProgress / Accept/Abort/Rerun 三按钮
   - 后端：`_handle_command("submit_judge_resolution")` 设置 `state.judge_resolution = <value>` + `_judge_resolution_received.set()`（**独立 event**，不复用 plan_review_received）；新增 `wait_for_judge_resolution()`
   - `web.py:124-148` run loop 扩展为三分（先判 judge → 再 conflict → 再 plan_review）；cancel_run 唤醒分支同步扩 `_judge_resolution_received`

3. **L5 Report & Memory 视图**
   - classifyView 加 L5 分支：`status === COMPLETED || status === FAILED`，**优先级最高（终态）**
   - 组件：Report.tsx（marked + DOMPurify 渲染 `<repo>/.merge/runs/<id>/merge_report.md`）/ Cost 详情 / Memory 浏览器 / checkpoint.json 下载链接
   - `src/web/static_server.py` 加 `/runs/<id>/*` 路由，从 `<repo>/.merge/runs/<run_id>/` 读文件 + 路径穿越防护（同 web/dist 模式）

4. **mock 扩展**
   - 新 MOCK_VIEW=judge：veto_triggered=true / 3 issues / 2 files / 2 repair instructions / repair_rounds=1/3
   - 新 MOCK_VIEW=report：phase_results 全 completed / cost_summary / memory entries / fake `runs/<id>/merge_report.md` 写到 tmp dir
   - 浏览器手测：四场景切换（dashboard / plan_review / judge / report）；L4 三按钮发出正确 ws cmd（chrome-devtools 实测）；L5 markdown 渲染 + checkpoint 下载

5. **Verify 标准**：pytest --cov ≥ 81.8% / mypy / ruff / npm build / npm test 全套
6. **commit message**：`feat(web): Phase 4 — L4 Judge verdict + L5 Report & Memory + M13 修复`

**本会话末 Executor 已收到的指令**：完成 Phase 4 commit + 触发 gatekeeper 审查后**停手**，不进入 Phase 5。Gatekeeper 审完后停手，不启动 hotfix 循环；若 NO-GO 含 hotfix 必修项，仅列在反馈中等下次会话修。

### Phase 5 — **未启动，下次会话首先处理**
预计 0.5d。任务清单见 §6。

---

## 6. Phase 5 完整待办（下次会话直接派给 Executor）

> 任务编号沿用本会话的 task #7（已在共享 task list 中预占）。

### 6.1 强制必做

1. **H2：wheel 打包 web/dist**（plan v1.1 §7 未决 1）
   - `pyproject.toml` 加 hatch build hook（或 force-include），把 `web/dist/*` 映射到 `src/web/dist/*`
   - 验证：`python -m build`、安装产生的 wheel、`merge --help` 能起 Web UI 且 `_resolve_web_dist()` 命中 packaged 路径
   - 配套 CI：`.github/workflows/*` 加 `npm ci && npm run build` 步骤（不然 wheel 出来没 dist）

2. **删除 tui/ 目录与所有引用**
   - `git rm -r tui/`
   - grep 全仓 `tui_command_impl` / `tui_resume_impl` / `tui/src/` / `npx tsx` / `React Ink` / `Ink TUI`，删除 alias 实现（保留 CLI 层 `--no-tui` / `--tui` flag deprecated alias 至下一 major release）
   - 删除 `src/cli/commands/tui.py`（如还存在）
   - 更新 `pyproject.toml` 移除 `tui/` 相关 paths

3. **完整重写 test_cli_flow.py + test_resume_tui_dispatch.py**
   - 本会话因路由变化做了最小补丁；Phase 5 用参数化重写（pytest.mark.parametrize 覆盖 web 默认 / `--no-web` / `--no-tui` alias / `--ci` / resume `--web` / resume `--tui` alias）
   - 文件改名（如适用）：`test_resume_tui_dispatch.py` → `test_resume_web_dispatch.py`

4. **文档更新**
   - `CLAUDE.md` 与 `README.md`：
     - `merge <branch>` 改为说默认启动浏览器 Web UI
     - `--no-tui` / `--tui` 标 deprecated
     - 增加 `cd web && npm install && npm run build` 首次安装步骤（或在 wheel hook 中做掉）
     - 删除 React Ink TUI / `cd tui` 等过时段落
   - `doc/architecture.md`：标记 TUI 已退役、Web UI 接管；后端 ws_bridge + serializers + static_server 架构图更新
   - 加 `doc/web-ui.md`（新建，整理 §3 的事实锚点 + 用户旅程截图）

5. **HEAD pre-existing 残留清理**（顺便修，与 Phase 5 同 scope）
   - `src/core/phases/plan_review.py:802` mypy 错误（`PhaseResult` status arg 类型）
   - `src/core/phases/conflict_analysis.py:11` ruff unused `DecisionSource` import
   - 二者本会话明确「Phase 0-4 不修，Phase 5 顺便修」

### 6.2 Gatekeeper 累计待收的 minor 建议

> 本会话各 Phase 留下、未单独建 hotfix 的 MEDIUM/MINOR 列表。Phase 5 一并 cherry-pick：

| 编号 | 建议 | 位置 |
|---|---|---|
| M2 | except Exception 收紧 | web.py（webbrowser.open / 其他兜底处） |
| M4 | vite proxy `/ws-dev` 摆设未用 | `web/vite.config.ts` |
| M5 | static_server 动态 type() mypy 不友好 | `src/web/static_server.py` |
| M7 | commit body 行数与实测对不上的复盘约定 | 后续 Executor 自律，不需要代码改动 |
| M8 | RunDashboard useEffect 依赖列表隐式假设 zustand setter 稳定 | `web/src/views/RunDashboard.tsx` |
| M9 | serializers `_enum_value if hasattr else str` 双层防御冗余 | `src/web/serializers.py` |
| M11 | ESLint `_omit` 已收 ✓ | — |
| M14 / M15 | 应在 Phase 4 M13 同步收，下次会话检查 | `PlanReview.tsx` |
| M16 | gatekeeper 单独 follow-up，已计入 ✓ | — |

### 6.3 Verify

`pytest tests/unit/ -q --cov=src --cov-fail-under=80`（src/web/ 应升至 ≥85%）/ `mypy src` 全绿（含 plan_review.py:802 修复）/ `ruff check src/` 全绿（含 conflict_analysis.py:11 修复）/ `cd web && npm run build && npm test` / 端到端 wheel install 后 `merge --help` 验证。

### 6.4 commit message

`chore(web): Phase 5 — 清理 TUI + wheel 打包 + 文档更新 + minor 修复`

---

## 7. 下次会话恢复 SOP

1. 读本文件 + `CLAUDE.md` + 跑 `git log --oneline -15` 看本会话留下的 commit 链
2. 跑一次 `pytest tests/unit/ -q && mypy src && ruff check src/ && cd web && npm run build && npm test`，确认 main 分支基线绿（如有红，是 Phase 4 commit 引入或本会话 Phase 4 未完成留下的脏改动）
3. 检查 Phase 4 状态：
   - 若 commit 存在（`feat(web): Phase 4 ...`）：读 gatekeeper 审查结论（在 `~/.claude/teams/web-ui-redesign/` 历史消息或新会话 SendMessage gatekeeper 询问）。如 GO → 直接派 Phase 5；如 NO-GO 含 hotfix → 先派 Executor 修
   - 若 commit 不存在：检查 `git status` 的脏改动，决定回滚或补 commit；本文件 §5 Phase 4 五项实施细节足够 Executor 接续
4. 如果团队 teammate 已被打盹（process 退出），按需重新 `Agent({team_name: "web-ui-redesign", name: "executor"/"gatekeeper"/...})` 起步，把本文件 §1-§3 作为 prompt 上下文塞进去
5. 派 Phase 5 任务（task #7 已建，描述见 §6）
6. Phase 5 GO 后整体回归测试 + 端到端 wheel install 验证 + 全部 commit 推到 main（或保留 `feat/web` PR）
7. `TeamDelete()` 清理（如新会话内不再用 team）

---

## 8. 已知风险与未决

- **`max_judge_repair_rounds`**：本会话 grep 后 `MergeConfig` 无该字段。Phase 4 倾向前端写死 3；Phase 5 议是否加入 config（决定后回头改 serializer 透出）
- **wheel install 后 web/dist 路径**：未端到端验证（本会话只验证 source checkout 路径 `<repo>/web/dist`）；Phase 5 H2 必须做端到端 install 测试
- **`max_judge_repair_rounds` config 字段缺失**：见上
- **L5 markdown 渲染 XSS**：marked + DOMPurify 已计划；新增依赖 `dompurify` 需在 npm 报上
- **cancel_run 非门期 vs 终态**：当前 `cancel_error` 仅在 AWAITING_HUMAN 门外回错；如果 COMPLETED/FAILED 终态收到 cancel 行为未定义，Phase 5 可补一行 fast-path
- **`server.py` / `app.py` 仍存在**：`src/web/server.py` 是旧的最简 HTTP server（轮询、内联 HTML），未对接 ws_bridge；`src/web/app.py` 是它的 REST shim。本会话决策"复用 ws_bridge"导致两者未被使用。Phase 5 可清理（或保留作为 CI 检测脚本 fallback；需要决策）

---

## 9. 任务清单（共享 task list 当前状态）

| ID | 状态 | 主题 |
|---|---|---|
| #1 | completed | Plan: Planner 产出 v1 设计方案 |
| #2 | pending | 汇总最终报告到 doc/web-ui-redesign-report.md（**即本文件，可改为 completed**） |
| #3 | completed | Test: Verifier 设计测试矩阵 |
| #4 | completed | Code: Phase 0 前端脚手架 + 后端启动方式改造 |
| #5 | completed | Plan: Phase 0 commit 审查 |
| #6 | completed | Code: Phase 1 — cancel_run + serializers 拆分 + L1 Dashboard 实时进度 |
| #7 | pending | **Code: Phase 5 — 清理 + wheel 打包 + 删除 tui/**（下次会话首要任务） |
| #8 | completed | Plan: Phase 1 commit bbee60d 审查 |
| #9 | completed | Code: Phase 2 — L3 Conflict Resolution + M6 修复 |
| #10 | completed | Plan: Phase 2 commit 702484b 审查 |
| #11 | completed | Code: H3 hotfix — manual_patch custom_content/reviewer_notes 端到端传输 |
| #12 | completed | Code: Phase 3 — L2 Plan Review 决策视图 |
| #13 | completed | Plan: Phase 3 commit 24509de 审查 |
| #14 | in_progress | **Code: Phase 4 — L4 Judge + L5 Report + M13 修复**（本会话末进行中） |

下次会话首先把 #14 推到 completed（或追加 hotfix task），然后启动 #7。

---

> 本会话由 Anthropic Claude Code (Claude Opus 4.7 1M context) 多 agent 工作流（`multi-agent-team` skill）驱动产出。
