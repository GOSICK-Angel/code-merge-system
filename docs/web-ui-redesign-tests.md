# Web UI Redesign — Test Matrix v1.1 (Verifier)

> **修订依据**：Planner final plan v1.1（按 Phase 0-5 验收点对齐）+ Gatekeeper 4 个 P1 修订点。
>
> **约束**：mypy strict / ruff / 80% 覆盖率 / 不引入 FastAPI / aiohttp（stdlib HTTP server）/ 文件上限：`ws_bridge.py` ≤600 / `serializers.py` 250-300 / `web.py` 110-140 / `static_server.py` 60-90。
>
> **v1 → v1.1 主要变化**：
> 1. `--no-tui` 决议 = 保留并发 DeprecationWarning + 等价 `--no-web`（不再二选一悬置）
> 2. 命令名 `submit_user_plan_decisions` 现已存在（无需新增），但前端必须**一次性提交模型**（plan 决策本地暂存 → Approve 时顺序发 2 条 cmd）
> 3. `cancel_run` 仅在 AWAITING_HUMAN 允许；非 AWAITING_HUMAN 回 `cancel_not_allowed` 错误帧
> 4. ws URL query 参数语义改为 `?ws=<port>`（不是完整 URL），默认 8765
> 5. 新增 serializer 模块单独成文件 → 新增 `tests/unit/test_serializers.py`
> 6. 新增 stdlib SPA static server → 新增 `tests/unit/test_static_server.py`
> 7. 删除 `tui/` 后需 grep 残留 → 新增 Phase 5 清理测试

---

## 0. 事实锚点（2026-05-14 grep 核实）

| 锚点 | 现状 / 行号 |
|------|------|
| `MergeDecision` 6 枚举 | `src/models/decision.py:8-14`（TAKE_CURRENT / TAKE_TARGET / SEMANTIC_MERGE / MANUAL_PATCH / ESCALATE_HUMAN / SKIP）；前端必须**隐藏 ESCALATE_HUMAN** |
| `DecisionSource` 4 枚举 | `src/models/decision.py:17-21`（AUTO_PLANNER / AUTO_EXECUTOR / HUMAN / BATCH_HUMAN）；`decisionRecordCounts` 按此 key 聚合 |
| `cost_summary` 字段 | `src/models/state.py:244` 已存在，原 `_serialize_state` 未透出 → Phase 1 serializer 需补 |
| `ws_bridge.py` 入站命令 | 5 个 @ line 464-487：`submit_decision` / `submit_conflict_decisions_batch` / `submit_plan_review` / `submit_user_plan_decisions` / `pause` + `resume`（共 6 处分支）|
| `_apply_user_plan_decisions` | `src/web/ws_bridge.py:574`（planner 给的 594-603 为偏移，语义一致）—— **立即触发 APPROVE 副作用**，前端必须一次性提交 |
| `_serialize_state` | `src/web/ws_bridge.py:103` 当前位置；Phase 1 拆出到 `src/web/serializers.py` |
| 默认 ws port | `src/web/ws_bridge.py:43-50` = 8765 |
| `--no-tui` 现状 | `src/cli/main.py:80,107,143` + `src/cli/commands/tui.py` 3 处文案 + `tests/` 3 处。CI workflow `.github/workflows/ci.yml` **无引用** |
| `tui/` 目录 | `tui/` 现存（Ink 实现）；Phase 5 删除 |

---

## 1. 测试运行约定

### 1.1 CI 必跑

```bash
# 后端
pytest tests/unit/ --cov=src --cov-fail-under=80
mypy src
ruff check src/

# 前端
cd web && npm ci && npm run lint && npm run test && npm run build
# build 已含 tsc --noEmit
```

### 1.2 本地手测

- E2E 表（§8）真实 fork merge 全旅程
- `tests/integration/`（真实 API key，按需）

### 1.3 不引入

- Playwright（先用 vitest + 手测脚本）
- FastAPI / aiohttp（stdlib `http.server` 够用）
- 跨浏览器自动化（首版 Chrome/Edge/Firefox 手测）

---

## 2. Phase 0 — 脚手架 + 后端启动

**新增文件**：`tests/unit/test_web_command.py`

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P0-1 | `test_merge_default_routes_to_web_command` | `merge <branch>` 默认走 `web_command_impl`，不再调 Node Ink | AC1 |
| P0-2 | `test_merge_no_browser_skips_webbrowser_open` | `--no-browser` 启 ws server 但不调 `webbrowser.open`（patch + assert_not_called） | AC1 反向 |
| P0-3 | `test_merge_no_web_uses_run_command_impl` | `--no-web` 走 plain text `run_command_impl` | AC5 |
| P0-4 | `test_merge_no_tui_alias_to_no_web_with_warning` | `--no-tui` 等价 `--no-web` 且 `warnings.warn(DeprecationWarning)` | AC6 |
| P0-5 | `test_merge_ci_skips_web` | `--ci` 不起 ws server | AC7 |
| P0-6 | `test_web_dist_missing_exits_with_clear_error` | `web/dist/` 不存在时 stderr 含 "web/dist not found"，`sys.exit(EXIT_UNKNOWN_ERROR)` | AC8 |
| P0-7 | `test_web_dist_missing_does_not_open_browser` | dist 缺失时不调 `webbrowser.open` | AC8 派生 |
| P0-8 | `test_browser_open_failure_falls_back_to_log` | `webbrowser.open()` 返回 False / 抛 `webbrowser.Error` → 打印 URL 到 stdout，继续运行 | 健壮性 |
| P0-9 | `test_browser_open_headless_env_skipped` | `DISPLAY` 未设置 + `SSH_CONNECTION` 存在 → 跳过 open，仅打印 URL | 健壮性 |
| P0-10 | `test_browser_url_contains_ws_port_query` | URL = `http://localhost:<http_port>/?ws=<ws_port>`（验收 v1.1 #4） | AC2 |

**回归（现有测试不动）**：
- `tests/unit/test_cli_flow.py:253,262,319` 保留绿（Phase 5 才改写）
- `tests/unit/test_resume_tui_dispatch.py:97` 保留绿

**Phase 0 准入门槛**：P0-1 ~ P0-10 + 前端 smoke（`npm run build` 通过）。

---

## 3. Phase 1 — ws-client + store + L1 Dashboard

### 3.1 后端：`tests/unit/test_serializers.py`（新文件）

7 个 serializer × 字段完整性，**重点验证 v1.1 新透出字段**：

| # | 用例 ID | 验证点 |
|---|---|---|
| P1B-1 | `test_serialize_state_includes_cost_summary` | snapshot["cost_summary"] 镜像 `state.cost_summary`（state.py:244） |
| P1B-2 | `test_serialize_state_includes_phase_elapsed` | snapshot["phase_elapsed"] 含当前 phase 起始时间差（秒） |
| P1B-3 | `test_serialize_state_includes_decision_record_counts` | snapshot["decision_record_counts"] 按 `DecisionSource` 4 枚举 key 聚合（decision.py:17-21） |
| P1B-4 | `test_serialize_state_omits_internal_fields` | 不透出 ReadOnlyStateView 内部 / 私有字段（`_*`） |
| P1B-5 | `test_serialize_pending_user_decisions_shape` | items 含 file_path / options（5 选项，去 ESCALATE_HUMAN） |
| P1B-6 | `test_serialize_conflict_points_includes_intent_and_risk` | upstream_intent / fork_intent / risk_factors 三个字段都在（对应 P2 验收 #3） |
| P1B-7 | `test_serialize_state_idempotent` | 同一 state 二次序列化结果一致（结构稳定，防 ordering flake） |
| P1B-8 | `test_serializers_no_state_mutation` | serializer 执行后 `state.model_dump()` 与执行前 deep-equal（反模式 A1） |

### 3.2 后端：`tests/unit/test_ws_bridge_extensions.py`（新文件）

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P1B-9 | `test_cancel_run_in_awaiting_human_sets_events` | AWAITING_HUMAN 状态收 `cancel_run` → 设 `_cancel_event` / `_plan_review_received` / `_human_decisions_received` 三个 Event | AC5 |
| P1B-10 | `test_cancel_run_outside_awaiting_human_returns_error_frame` | 非 AWAITING_HUMAN 收 `cancel_run` → 返回 `{type:"error",code:"cancel_not_allowed"}`，不设 Event | AC4 |
| P1B-11 | `test_cancel_run_terminal_state_returns_error_frame` | COMPLETED/FAILED 下 cancel → 同 P1B-10 行为 | 健壮性 |
| P1B-12 | `test_cancel_run_idempotent_in_awaiting_human` | AWAITING_HUMAN 下连发 2 次 cancel → 第二次为 no-op（Event 已 set） | 健壮性 |
| P1B-13 | `test_activity_buffer_capped_at_200` | 第 201 条入 buffer → 最旧的一条被 drop（环形 buffer） | AC3 |
| P1B-14 | `test_send_snapshot_followed_by_activity_replay` | 新连接 / 重连 → 先收 `state_snapshot` 再立即收 `agent_activity_replay`（含 ≤200 条） | AC3 |
| P1B-15 | `test_activity_replay_empty_when_no_history` | 历史为空时 replay 帧的 events 为 `[]` | 边界 |
| P1B-16 | `test_existing_5_commands_unchanged` | 回归：`submit_decision` / `submit_conflict_decisions_batch` / `submit_plan_review` / `submit_user_plan_decisions` / `pause` / `resume` 行为不变 | 回归 |
| P1B-17 | `test_unknown_command_returns_error_frame` | 未知 type → 错误帧，不崩溃 | 健壮性 |
| P1B-18 | `test_malformed_json_does_not_crash` | 非 JSON / 缺 `type` → 错误帧 + warning 日志 | 健壮性 |
| P1B-19 | `test_broadcast_debounce_300ms` | 100ms 内 5 次触发合并为 1 次 | 现有契约回归 |
| P1B-20 | `test_notify_agent_activity_threadsafe` | 跨线程 `call_soon_threadsafe`，不阻塞 | 现有契约回归 |
| P1B-21 | `test_ws_bridge_no_direct_state_writes` | grep `state.<field> =` 在 ws_bridge.py 不出现（反模式 A1）| 反模式 |
| P1B-22 | `test_ws_bridge_line_count_under_600` | `wc -l src/web/ws_bridge.py` ≤ 600（Phase 1 拆分后） | AC7 |

### 3.3 前端：`web/src/ws/client.test.ts`（vitest）

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P1F-1 | `resolveWsUrl: ?ws=8765 returns ws://localhost:8765` | query 参数解析 | v1.1 修订 #1 |
| P1F-2 | `resolveWsUrl: ?ws=9000 returns ws://localhost:9000` | port 覆盖 | AC: ?ws=9000 |
| P1F-3 | `resolveWsUrl: no query returns ws://localhost:8765` | 默认值 | |
| P1F-4 | `resolveWsUrl: ?ws=abc throws or falls back to default` | 非法 port 容错 | 健壮性 |
| P1F-5 | `resolveWsUrl: ?ws=99999 rejects out-of-range` | port > 65535 拒绝 | 健壮性 |
| P1F-6 | `client: emits "WS connected" log on handshake` | 用户可见日志 | AC3 |
| P1F-7 | `client: exponential backoff reconnect 1/2/4/8/30s cap` | 重连策略 | 健壮性 |
| P1F-8 | `client: visibilitychange visible triggers reconnect` | tab 切回立即重连 | 健壮性 |
| P1F-9 | `client: outbound queue flushes on reconnect` | 断线期间命令入队 | 健壮性 |
| P1F-10 | `client: dispatches typed frames to store` | snapshot / patch / agent_activity / agent_activity_replay / error 五类分发 | AC4 派生 |

### 3.4 前端：`web/src/store/runStore.test.ts`（vitest）

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P1F-11 | `state_snapshot replaces full state immutably` | snapshot 收到后完整替换 | AC4 |
| P1F-12 | `state_patch applied via immer merge` | patch 合并 | AC1 |
| P1F-13 | `agent_activity_replay resets buffer then appends` | replay 帧先清空，再 append（首次连接 / 重连后历史还原） | AC3 |
| P1F-14 | `agent_activity appends incrementally bounded 200` | 增量 append，buffer 上限 200 | AC3 |
| P1F-15 | `phase timeline highlights current phase` | `currentPhase` selector 正确 | AC1 |
| P1F-16 | `costSummary / phaseElapsed / decisionRecordCounts render fields` | 三个新字段 store + selector | AC6 |
| P1F-17 | `connection state machine: connecting/connected/disconnected/reconnecting` | 状态转换 | |
| P1F-18 | `cancel button enabled only in AWAITING_HUMAN` | selector `canCancel` 反映 phase | AC4 |

---

## 4. Phase 2 — L3 Conflict Resolution

**主要靠 E2E**（§8），单元测试覆盖关键逻辑：

### 4.1 前端：`web/src/views/ConflictResolution.test.tsx`（vitest + RTL）

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P2F-1 | `renders 5 decision options, excludes ESCALATE_HUMAN` | 选项渲染严格 5 个（TAKE_CURRENT / TAKE_TARGET / SEMANTIC_MERGE / MANUAL_PATCH / SKIP）| AC5 |
| P2F-2 | `three-column diff renders upstream/base/fork` | 三栏 diff 容器 | AC2 |
| P2F-3 | `conflict marker hover shows upstream_intent / fork_intent / risk_factors` | tooltip 内容字段 | AC3 |
| P2F-4 | `"Apply recommended to all" only stages local drafts` | 仅写本地 draft，未发 ws | AC4 |
| P2F-5 | `Confirm button sends submit_conflict_decisions_batch with all drafts` | 确认 → 一次性 batch 命令 | AC4 |
| P2F-6 | `single-file decide sends submit_decision` | 单文件路径仍走原命令 | 回归 |
| P2F-7 | `pending drafts persist across ws disconnect` | 断线不丢 draft（store 内存） | 健壮性 |

### 4.2 构建侧

| # | 用例 ID | 验证点 |
|---|---|---|
| P2-Build-1 | `npm_ls_react_single_version_check` | CI 步骤 `npm ls react` 输出仅一个版本（dedupe） |

---

## 5. Phase 3 — L2 Plan Review（P1 修订 #3 关键）

### 5.1 前端：`web/src/store/uiStore.test.ts`（vitest）

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P3F-1 | `setDraftPlanDecision writes to draftPlanDecisions only, no ws send` | 单文件选项不发 ws | AC1 |
| P3F-2 | `applyRecommendedToAll fills drafts locally without ws send` | 批量本地填充 | AC: §3 #5 |
| P3F-3 | `canApprovePlan returns true only when 100% covered` | Approve 按钮 enabled 条件 | AC2 |
| P3F-4 | `canApprovePlan false when any pendingUserDecisions lacks draft` | 反向覆盖 | AC2 |
| P3F-5 | `commitPlanReview(approve) sends 2 commands in order` | 顺序：`submit_user_plan_decisions {items}` → `submit_plan_review {decision:"approve"}` | AC3 |
| P3F-6 | `commitPlanReview(reject) sends only submit_plan_review {decision:"reject"}` | reject 路径不发 user_plan_decisions | AC4 |
| P3F-7 | `commitPlanReview clears drafts on server ack` | 收到 server ack 后清空 | 健壮性 |
| P3F-8 | `drafts survive ws reconnect` | 断线不丢 | 健壮性 |

### 5.2 前端：`web/src/views/PlanReview.test.tsx`（vitest + RTL）

| # | 用例 ID | 验证点 |
|---|---|---|
| P3F-9 | `Approve button disabled until 100% coverage` | UI 反映 P3F-3 |
| P3F-10 | `dirty badge shown when drafts present` | 视觉指示 |
| P3F-11 | `single file option click does not trigger network` | 网络 mock 断言 |
| P3F-12 | `"Apply recommended to all" fills UI options visibly` | 渲染断言 |

---

## 6. Phase 4 — L4 Judge + L5 Report 简版

### 6.1 后端：`tests/unit/test_static_server.py`（新文件）

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P4B-1 | `spa_fallback_unknown_path_returns_index_html` | GET `/foo/bar` → 200 + `index.html` 内容 | AC: SPA |
| P4B-2 | `path_traversal_dotdot_rejected` | GET `/../etc/passwd` → 403 / 404，不返回真实文件 | 安全 |
| P4B-3 | `path_traversal_url_encoded_rejected` | GET `/%2e%2e/etc/passwd` → 拒绝 | 安全 |
| P4B-4 | `path_traversal_absolute_rejected` | GET `//etc/passwd` → 拒绝 | 安全 |
| P4B-5 | `runs_endpoint_serves_md` | GET `/runs/<id>/merge_report.md` → 200 + content-type text/markdown | AC2 |
| P4B-6 | `runs_endpoint_serves_json` | GET `/runs/<id>/checkpoint.json` → 200 + application/json | AC3 |
| P4B-7 | `runs_endpoint_rejects_other_extensions` | GET `/runs/<id>/secret.env` → 404（仅 .md / .json 白名单） | 安全 |
| P4B-8 | `runs_endpoint_rejects_traversal_in_id` | GET `/runs/../etc/passwd.md` → 拒绝 | 安全 |
| P4B-9 | `static_server_line_count_under_90` | `wc -l src/web/static_server.py` ≤ 90 | 文件上限 |

### 6.2 前端：`web/src/views/JudgeReview.test.tsx`（vitest + RTL）

| # | 用例 ID | 验证点 |
|---|---|---|
| P4F-1 | `VETO verdict renders issues list` | issues 数组渲染 |
| P4F-2 | `VETO verdict renders repair_instructions` | repair instructions 渲染 |
| P4F-3 | `APPROVE verdict hides repair section` | approve 时无 repair UI |
| P4F-4 | `report markdown renders via marked/markdown-it` | `/runs/<id>/merge_report.md` 拉取并渲染 |

---

## 7. Phase 5 — 清理 + 测试改写

### 7.1 改写既有测试

| # | 用例 ID | 改写内容 |
|---|---|---|
| P5-1 | `test_cli_flow.py:253,262,319` | 改 parametrize `--no-tui` / `--no-web`：行为等价 + `--no-tui` 触发 `DeprecationWarning` |
| P5-2 | `test_resume_tui_dispatch.py:1,97` | 改 parametrize `--tui` / `--web`：行为等价 + `--tui` 触发 `DeprecationWarning` |

### 7.2 新增清理验证：`tests/unit/test_cleanup_residue.py`（新文件）

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P5-3 | `test_no_tui_module_imports_remain` | grep `from src.cli.commands.tui` 在 src/ tests/ 无残留 | AC1 |
| P5-4 | `test_no_tui_command_impl_refs_remain` | grep `tui_command_impl` / `tui_resume_impl` 在 src/ tests/ 无残留 | AC1 |
| P5-5 | `test_tui_directory_deleted` | `tui/` 目录不存在 | AC2 |
| P5-6 | `test_docs_no_no_tui_except_deprecation_note` | grep `--no-tui` 在 docs/ 仅一处（deprecation note） | AC6 |

### 7.3 覆盖率 / 静态检查

| # | 用例 ID | 验证点 | 对应验收 |
|---|---|---|---|
| P5-7 | `pytest_unit_pass_with_coverage_80` | `pytest tests/unit/ --cov-fail-under=80` 通过 | AC3 |
| P5-8 | `mypy_strict_passes` | `mypy src` 无错误 | AC4 |
| P5-9 | `ruff_check_passes` | `ruff check src/` 通过 | AC5 |

---

## 8. E2E 手动验收矩阵

跑真实小 fork merge（用 `dify-official-plugins` baseline 测试，参见 memory），全程在浏览器：

| # | 场景 | 触发 | 期望 | 对应验收 |
|---|---|---|---|---|
| E1 | L1 phase timeline 高亮 | `merge upstream/main` | 切换 INITIALIZED → ANALYZING → ... 时高亮当前 phase | Phase 1 AC1 |
| E2 | L2 Approve plan | Plan Review 全选 + Approve | orchestrator 推进 AUTO_MERGING | Phase 3 AC3 |
| E3 | L2 Reject plan | Plan Review 点 Reject | orchestrator 走 PLAN_REVISING | Phase 3 AC4 |
| E4 | L2 单文件 click 不发网络 | DevTools Network tab 监控 | 仅本地 state 变化 | Phase 3 AC1 |
| E5 | L3 决策 5 个冲突文件 | 选项点击 + Confirm | orchestrator 继续 | Phase 2 AC1 |
| E6 | L3 "Apply recommended to all" | 按钮 + Confirm | 一次性 batch ws 命令 | Phase 2 AC4 |
| E7 | L3 三栏 diff + hover tooltip | hover marker | upstream_intent / fork_intent / risk_factors 显示 | Phase 2 AC2-3 |
| E8 | L4 VETO 场景 | 模拟 VETO 状态 | issues + repair_instructions 完整 | Phase 4 AC1 |
| E9 | L5 报告渲染 | 终态 | `/runs/<id>/merge_report.md` markdown 正确渲染 | Phase 4 AC2 |
| E10 | L5 checkpoint 下载 | 点击下载 | `/runs/<id>/checkpoint.json` 下载成功 | Phase 4 AC3 |
| E11 | Cancel button IO 阶段 | AUTO_MERGING 中观察按钮 | disabled + tooltip "Only available in AWAITING_HUMAN" | Phase 1 AC4 |
| E12 | Cancel button AWAITING_HUMAN | 暂停时点 Cancel | 30s 内进程退出 + `EXIT_NEEDS_HUMAN` | Phase 1 AC5 |
| E13 | 关浏览器 orchestrator 续跑 | 关闭 tab | 后台 orchestrator 不受影响（pytest 看 state 持续推进） | 健壮性 |
| E14 | `--no-browser` | `merge ... --no-browser` | 仅 stdout 打印 URL | Phase 0 AC1 反向 |
| E15 | `--ci` | `merge ... --ci` | 不起 ws server + JSON 输出 | Phase 0 AC7 |
| E16 | `--no-tui` deprecation | `merge ... --no-tui` | stderr `DeprecationWarning` + 行为 = `--no-web` | Phase 0 AC6 |
| E17 | `?ws=9000` URL query | 浏览器手动 `http://localhost:5173/?ws=9000` | 连 9000 端口 | Phase 0 AC2 |
| E18 | `web/dist/` 缺失 | 重命名 dist + `merge ...` | 友好错误 + exit code `EXIT_UNKNOWN_ERROR` | Phase 0 AC8 |
| E19 | ws 断线自动重连 | 杀掉 ws server 进程 | 前端 banner + ws server 重启后自动重连 + replay 历史 | Phase 1 AC3 |
| E20 | `merge resume --run-id` | 中断后 resume | 浏览器恢复界面 + checkpoint 状态 | 回归 |

---

## 9. Lighthouse / 性能（非 CI，release smoke）

| # | 检查项 | 目标 |
|---|---|---|
| L1 | First Contentful Paint | < 1.5s (localhost) |
| L2 | bundle size gzip | < 250KB |
| L3 | ws roundtrip P95 | < 50ms (localhost) |
| L4 | 30 min 内存稳定 | DevTools 内存快照对比无 leak |

---

## 10. 覆盖率门槛

| 模块 | 目标 |
|---|---|
| 后端整体 | 80%（CI `--cov-fail-under=80`） |
| `src/web/` | 85% |
| 前端 store + ws-client | 90% |
| 前端组件 | 70% |

---

## 11. 各 Phase 准入门槛汇总

| Phase | 必须先绿 | 总测试数 |
|---|---|---|
| Phase 0 | P0-1 ~ P0-10（10） + 前端 build smoke | 10 |
| Phase 1 | P1B-1 ~ P1B-22（22） + P1F-1 ~ P1F-18（18） | 40 |
| Phase 2 | P2F-1 ~ P2F-7（7） + P2-Build-1 + E5-E7 手测 | 8 + 手测 |
| Phase 3 | P3F-1 ~ P3F-12（12） + E2-E4 手测 | 12 + 手测 |
| Phase 4 | P4B-1 ~ P4B-9（9） + P4F-1 ~ P4F-4（4） + E8-E10 手测 | 13 + 手测 |
| Phase 5 | P5-1 ~ P5-9（9） + 全部前期测试仍绿 | 9 + 全量回归 |

**总计**：单元测试 92 case + E2E 手测 20 case + Lighthouse 4 项。

---

## 12. 待澄清议题（Phase 0 前请定）

1. **ws 鉴权方案**：v1.1 plan 未提鉴权（仅 `?ws=<port>` 是连接地址）。本地启动是否需要 token？建议 Phase 1 加 `Origin` 校验（拒绝非 `localhost` / `127.0.0.1`），并补 P1B-23 `test_origin_check_rejects_remote`。**这是新增项目，请 planner 确认。**
2. **`agent_activity_replay` 帧 schema**：planner v1.1 提到此帧名，但未给具体 payload 格式。需补 P1B-14 用例的 payload 断言。建议 executor Phase 1 PR 给出 schema。
3. **`EXIT_NEEDS_HUMAN` / `EXIT_UNKNOWN_ERROR` 常量来源**：是否在 `src/cli/exit_codes.py`？需确认导入路径以便 P0-6 / E12 写断言。
4. **`http_port`（SPA static server）默认值**：是否同样支持 `?ws=` 风格的 query？影响 P0-10 URL 拼装。

→ 12 议题在 Phase 0-4 实施期间已落定，详见 §13.4 偏离/supersede 记录。

---

## 13. Phase 0-5 实施汇总报告（2026-05-15 收尾）

> 本节由 team-lead（main agent）在 Phase 5 终审 GO 后追加，作为整个 Web UI 重构（TUI → 浏览器）工作流的最终汇总。多 agent 协作：team-lead（main agent）/ planner / verifier / executor / gatekeeper，team 名 `web-ui-redesign`。

### 13.1 Commit 链（feat/web 分支）

| Hash | Phase | 标题 |
|---|---|---|
| `d0c18af` | Phase 0 | feat(web): Phase 0 — Web UI 脚手架 + CLI 路由切换 |
| `595270f` | H1 hotfix | fix(web): 移除 src/web/* 覆盖率排除，纳入 80% 门槛 |
| `bbee60d` | Phase 1 | feat(web): Phase 1 — cancel_run + serializers 拆分 + L1 Dashboard |
| `bdd326a` | M6 hotfix | fix(web): M6 — notify_agent_activity 跨线程安全 marshal 到 event loop |
| `702484b` | Phase 2 | feat(web): Phase 2 — L3 Conflict Resolution |
| `c306def` | H3 hotfix | fix(web): H3 — manual_patch reviewer_notes/custom_content 端到端传输 |
| `24509de` | Phase 3 | feat(web): Phase 3 — L2 Plan Review 决策视图 |
| `8cac90c` | Phase 4 | feat(web): Phase 4 — L4 Judge verdict + L5 Report & Memory + M13/M14/M15 修复 |
| `8e405e1` | Phase 5a | chore(web): Phase 5a — 清理 React Ink TUI 与改写 CLI 路由测试 |
| `d66a64a` | Phase 5b | chore(web): Phase 5b — wheel 打包 web/dist + CI 接入 npm build |
| `12b1e26` | Phase 5c | docs(web): Phase 5c — 标记 TUI 退役与 Web UI 接管，新增 web-ui 章节 |
| `072a9e9` | Phase 5d | fix(web): Phase 5d — L4 独立 event hotfix + minor 修复 + pre-existing 残留 |
| `87b466a` | Phase 5 收尾 | docs(web): Phase 5 收尾 — 清理 TUI prose 残留与 serializer 三层防御 |

**Phase 5 净变更**：84 files / +690 / -6111（净删 5421 行，主要源自删除 `tui/` 目录的 React Ink 实现）。

### 13.2 最终整体 Verify 结果（2026-05-15 收尾基线）

| 项目 | 结果 |
|---|---|
| `pytest tests/unit/ -p no:randomly --cov=src --cov-fail-under=80` | **2238 passed / 1 skipped / TOTAL 82.52%** ✅ |
| `pytest tests/unit/ --cov=src`（默认随机顺序，本机 seed） | **2238 passed / TOTAL 82.54%** ✅ |
| `mypy src` | **Success: no issues found in 159 source files** ✅ |
| `ruff check src/` | **All checks passed!** ✅ |
| `ruff format --check src/` | 159 files already formatted ✅ |
| `cd web && npm run build` | **164 modules / CSS 20.20 kB / JS 348.53 kB (gzip 110.97 kB)** ✅ |
| `cd web && npm test` | **7 test files / 57 tests passed** ✅ |
| `python -m build` (wheel) | `code_merge_system-0.1.0-py3-none-any.whl` 含 `src/web/dist/*` 6 文件 ✅ |
| wheel install + `_resolve_web_dist()` 命中 packaged 路径 | ✅（Executor 在 C2 阶段验证；team-lead 在 task #6 整体回归确认） |

**Web 相关测试集中跑**：97 passed（test_serializers / test_ws_bridge_extensions / test_web_command / test_ws_bridge_judge_resolution / test_static_server_runs / test_cleanup_residue / test_cli_flow / test_resume_web_dispatch / test_ws_bridge_threading / test_ws_bridge_h3_reviewer_fields）。

**`src/web/` 模块覆盖率**：

| 模块 | 覆盖率 | 备注 |
|---|---|---|
| `__init__.py` | 100% | |
| `app.py` | 100% | |
| `serializers.py` | **93%** | 已超 plan §10 目标 |
| `static_server.py` | **94%** | 已超 plan §10 目标 |
| `ws_bridge.py` | 65% | 缺口集中在 debounce timing / `_serialize_state` 重复路径，见 §13.5 |
| **整体 src/web/** | **79.96%** | plan v1.1 §10 期望 ≥85%，差 ~5%，team-lead 已接受为独立 hotfix（见 §13.5） |

### 13.3 测试矩阵实际落地情况对照（§2–§7）

| Phase | 计划 | 实际落地 | 备注 |
|---|---|---|---|
| Phase 0 | P0-1 ~ P0-10（10 cases） | ✅ 全部落地在 `tests/unit/test_web_command.py` + `test_cli_flow.py` parametrized | 10/10 |
| Phase 1 后端 | P1B-1 ~ P1B-22（22 cases） | ✅ 落地在 `test_serializers.py` (34 cases，覆盖含后续 Phase 扩展) + `test_ws_bridge_extensions.py` + `test_ws_bridge_threading.py`（M6 hotfix） | 含 Phase 4 扩展至 34 case |
| Phase 1 前端 | P1F-1 ~ P1F-18（18 cases） | ✅ 落地在 `web/src/ws/client.test.ts` + `runStore.test.ts` | — |
| Phase 2 | P2F-1 ~ P2F-7 + P2-Build-1（8 cases） | ✅ 落地在 `web/src/views/ConflictResolution.test.tsx` | — |
| Phase 3 | P3F-1 ~ P3F-12（12 cases） | ✅ 落地在 `web/src/store/planReviewDraftStore.test.ts` + `views/PlanReview.test.tsx` | 含 H3 hotfix (`test_ws_bridge_h3_reviewer_fields.py`) |
| Phase 4 | P4B-1 ~ P4B-9 + P4F-1 ~ P4F-4（13 cases） | ✅ 落地在 `tests/unit/test_static_server_runs.py` (6 cases) + `test_ws_bridge_judge_resolution.py` (5+2 cases) + `web/src/lib/markdown.test.tsx` (6) + `classifyView.test.ts` 扩展 | judge_resolution 在 Phase 5d hotfix 后新增 2 case 校验独立 event 不再 set `_plan_review_received` |
| Phase 5 | P5-1 ~ P5-9（9 cases） | ✅ 落地在 `test_cleanup_residue.py` (4) + `test_cli_flow.py` parametrize + `test_resume_web_dispatch.py` parametrize + verify 全套 | P5-6 在 C3 之后无 xfail |
| E2E 手测 | E1-E20（20 场景） | ⚠️ 单元/集成测试 + chrome-devtools MCP 实测（Phase 1 L1 / Phase 2 L3 / Phase 3 L2 / Phase 4 L4+L5）等价覆盖；未跑完整真实 fork merge E2E | mock-bridge.py 五场景 (dashboard / plan_review / conflict / judge / report) 与 chrome-devtools MCP 实测构成等价 E2E 基线 |
| Lighthouse / 性能 (§9) | L1-L4 | ⏸ 未跑，留作发布前 smoke | localhost build 348 kB JS / gzip 111 kB 已远低于 §9 L2 目标 250 KB（gzip）门槛 |

**单元测试总量**：2238 passed（Phase 0 基线 2173 → +65 net）。前端 vitest 57 passed。

### 13.4 偏离 / Supersede 记录

| # | 原计划 / handoff §1 决策 | 实际实施 | Team-lead 裁决 | 落地位置 |
|---|---|---|---|---|
| 1 | handoff §1 第 7 条：L4 用**独立** `_judge_resolution_received` event | Phase 4 (8cac90c) 复用 `_plan_review_received`；Phase 5d (072a9e9) hotfix 改为独立 event + `wait_for_judge_resolution()` + run loop 三分支 + cancel_run 三 event 同步 set | **选项 A — 补 hotfix**（理由：独立 event 语义清晰、cancel/test 可读性更好；§1 是用户锁定决策） | `src/web/ws_bridge.py:43,80-89,204-211,357-371` + `src/cli/commands/web.py:145-161` + `tests/unit/test_ws_bridge_judge_resolution.py:52-98` |
| 2 | handoff §1 第 9 条：L5 markdown 用 **marked**（轻量）+ DOMPurify 防御层 | 自研 `web/src/lib/markdown.tsx`（H1-H3 / fenced code / list / 段落），直接构建 React 树，未走任何 raw-HTML 注入路径 | **正式 supersede §1 第 9 条**（理由：XSS 攻击面更小 / 依赖更少 / 显式 test 校验 `<script>` 不渲染） | `web/src/lib/markdown.tsx` + `markdown.test.tsx:30-38`；记录于 `doc/web-ui.md:135-138` |
| 3 | handoff §8：`max_judge_repair_rounds` 前端写死 3 | serializer 透出 `state.config.max_rerun_rounds` 实际字段，前端读取 `maxRerunRounds` 而非硬编码 | 顺手 supersede（config 实际有此字段） | `src/web/serializers.py:451-455` + `web/src/views/JudgeVerdict.tsx:121-125` |
| 4 | handoff §5：M14 改 OutboundMessage 直 import | ✅ 已实施 | — | `web/src/views/PlanReview.tsx:3,81` |
| 5 | handoff §5：M15 注释 gate id | 注释精确链接 `plan_review.py:1020-1068`，options[0] 由非 LLM 代码构造（无对应 gate id） | 视作满足（指向 ground truth 行号比虚构 gate id 更准确） | `web/src/store/planReviewDraftStore.ts:80-89` |
| 6 | §12 #1 ws 鉴权 | 仅 localhost-only，未引入 ws token / Origin 校验 | 按 handoff §1 决策 "localhost-only" 保留现状；后续若有远程使用场景再加 | — |
| 7 | §12 #2 `agent_activity_replay` schema | Phase 1 落地时 schema 在 `serializers.py` + `ws/messages.ts` 双侧定义 | — | `web/src/ws/messages.ts` `AgentActivityReplay` |

### 13.5 已知遗留议题（不阻断 ship）

| 议题 | 严重度 | 描述 | 处理路径 |
|---|---|---|---|
| `src/web/ws_bridge.py` 覆盖率 65%，整 `src/web/` 79.96%，距 plan §10 目标 ≥85% 差 ~5% | MEDIUM | 缺口集中在 debounce timing / `_serialize_state` 重复路径分支。已有 H3 + threading + judge_resolution + extensions 4 套测试覆盖核心命令流 | 独立 hotfix：补 `test_ws_bridge_debounce.py` + `test_serialize_state_branches.py` ≈10-15 case；预计 60-90 分钟 |
| `tests/unit/test_p1_fixes::test_apply_bytes_with_snapshot_base64_snapshot_of_existing_file` 在某些 random seed 下 fail | MINOR | Pre-existing 测试顺序污染（与 src/tools/ + src/models/ 相关），与 Phase 4/5 无关。`-p no:randomly` 与本机默认 seed 均不复现 | 独立 hotfix；不在 Web UI 重构 scope |
| `src/web/server.py` / `src/web/app.py` 旧 HTTP shim | INFO | server.py 已在 Phase 5d 删除；app.py 仍保留并 100% 覆盖（用作 CI 状态查询 fallback） | 现状即可；如确无消费者再决定删除 |
| E2E 完整真实 fork merge | INFO | mock-bridge + chrome-devtools MCP 实测覆盖五屏旅程；完整真实 fork merge E2E 留作发布前 smoke | 发布前 release smoke 阶段执行 |
| §12 #3 `EXIT_NEEDS_HUMAN` / `EXIT_UNKNOWN_ERROR` 常量 | INFO | 当前未在 CI 显式断言；E12 / P0-6 在 mock-bridge 手测中等价校验 | 可在 ws_bridge 覆盖率 hotfix 顺手补 `test_exit_codes.py` |

### 13.6 工作流复盘要点

| 类别 | 内容 |
|---|---|
| Team 拓扑 | team-lead（main agent，外部调度）+ planner（Plan，只读，已完成 v1+v1.1）+ verifier（general-purpose，只读，已交测试矩阵）+ executor（general-purpose，读写，7+5=12 commit）+ gatekeeper（general-purpose，只读，审 7+1=8 次） |
| 协作约定 | Peer DM + gatekeeper 反馈格式必须开头列「已通过事实（禁止改动）」；每个 Phase commit 前必须跑完整 Verify；commit message 中文 conventional commits 不带 Co-Authored-By；Executor 每 Step 起步前发简短 SendMessage |
| 已发现的失败模式 | (1) Executor 长时间静默 → 加固"每 Step 前发起步消息"；(2) 消息延迟 → 信号源以仓库 `git log` 为准；(3) 旧测试断言路由冲突 → A 路径最小化更新 mock target；(4) Phase 4 偏离 §1 锁定决策（复用 event） → gatekeeper 终审捕获 + team-lead 走选项 A 补 hotfix |
| Phase 推进节奏 | Phase 0/1/2/3/4 各一次 gatekeeper round（含 H1/M6/H3 三次 hotfix）；Phase 5 拆 4 commit（5a/5b/5c/5d）+ team-lead 自补 1 cleanup commit；终审一次性而非 per-commit |

### 13.7 Push / PR 决策

- 分支：`feat/web`（本地领先 origin/main `Phase 0 起 13 commit`）
- Remote：`git@github.com:GOSICK-Angel/code-merge-system.git`
- 决策：**push origin feat/web**（task #6 收尾动作）；PR base = main；PR 标题与 body 由 team-lead 在 push 后写
- 风险：MEDIUM `src/web/` 覆盖率缺口 + INFO 完整 E2E 留作 release smoke；均不阻断 PR open

### 13.8 后续 hotfix 待办（不在 Phase 5 scope，挪到下一会话）

1. `src/web/ws_bridge.py` 覆盖率从 65% 提升至 ≥85%（独立 commit，与本 Phase 5 链解耦）
2. `tests/unit/test_p1_fixes` 测试顺序污染根因定位（独立 commit，src/tools/ 或 src/models/ 修复）
3. Release smoke：完整真实 fork merge E2E + Lighthouse §9 L1-L4 项
4. `merge plan v1.1 §12 #3` `EXIT_*` 常量在 CI 显式断言（可与 ws_bridge 覆盖率 hotfix 同 commit）

---

> 本汇总报告由 team-lead 在 Phase 5 整体 verify + gatekeeper 终审 GO 之后写入。整个 Web UI 重构工作至此达到 ship-ready 基线；所有 Phase 0-5 commit 已在 `feat/web` 分支落地。
