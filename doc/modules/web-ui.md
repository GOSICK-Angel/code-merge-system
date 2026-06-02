# Web UI（浏览器前端）参考

> **版本**：2026-05-15（feat/web 分支）
>
> 本文档总结当前 Web UI 的源码锚点、运行模型与五屏用户旅程。历史
> 重构交接请见 [`doc/web-ui-redesign-handoff.md`](web-ui-redesign-handoff.md)
> （仅供历史参考，已被本文 + `doc/architecture.md` §11 取代）。

---

## 1. 源码锚点

### CLI / 启动

| 锚点 | 含义 |
|---|---|
| `src/cli/main.py` | `merge_command`：无参默认走 Web（浏览器）；`--ci` 非交互；`--web-port`（默认 5173） / `--ws-port`（默认 8765） / `--auto-decisions` |
| `src/cli/main.py` | `resume_command`：`--web` 走浏览器，否则纯 Orchestrator 路径；`--no-browser` / `--web-port` / `--ws-port` |
| `src/cli/commands/web.py:1-175` | `web_command_impl` / `web_resume_impl` / `_run_web` / `_resolve_web_dist` / `_bridge_cancelled` |
| `src/cli/commands/web.py:27-39` | `_resolve_web_dist()`：先 `importlib.resources.files("src.web") / "dist"`，失败 fallback 到 `<repo>/web/dist` |

### 后端通信

| 锚点 | 含义 |
|---|---|
| `src/web/ws_bridge.py` | `MergeWSBridge.start(host, port=8765)`；入站类型 `submit_decision` / `submit_conflict_decisions_batch` / `submit_plan_review` / `submit_user_plan_decisions` / `pause` / `resume` / `cancel_run` / `submit_judge_resolution` |
| `src/web/ws_bridge.py:41` | `_plan_review_received` / `_human_decisions_received` / `_judge_resolution_received` 三个独立 asyncio.Event |
| `src/web/ws_bridge.py` | `_cancel_event` + `is_cancelled()`；ring buffer `ACTIVITY_BUFFER_MAX = 200`；出站 `state_snapshot` / `agent_activity` / `agent_activity_replay` / `cancel_error` |
| `src/web/serializers.py` | `serialize_state(state)` 透出：runId / status / currentPhase / phaseResults / mergePlan / fileClassifications / fileDiffs / fileDecisionRecords / humanDecisionRequests / humanDecisions / judgeVerdict / judgeRepairRounds / planReviewLog / reviewConclusion / pendingUserDecisions / gateHistory / errors / messages / memory / costSummary / phaseElapsed / decisionRecordCounts / planHumanReview / judgeResolution / maxJudgeRepairRounds |
| `src/web/static_server.py` | stdlib ThreadingHTTPServer + SPA fallback + 路径穿越防护（`resolve().relative_to(root_resolved)`）；`/runs/<id>/*` 路由 |

### Orchestrator / phases

| 锚点 | 含义 |
|---|---|
| `src/core/orchestrator.py:193-194` | `set_activity_callback(cb)` 注册 `ActivityEvent` 回调 |
| `src/core/state_machine.py:12-71` | 13 个 SystemStatus 状态机；`AWAITING_HUMAN` 是唯一人工决策门 |
| `src/core/phases/base.py:29-39` | `ActivityEvent(agent, action, phase, event_type, elapsed, extra)`；`ctx.notify(agent, action)` |
| `src/core/phases/human_review.py:148-161` | Judge 阻塞：`judge_verdict != None && current_phase == JUDGE_REVIEW && judge_resolution is None` → AWAITING_HUMAN |

### 前端

| 锚点 | 含义 |
|---|---|
| `web/package.json` | Vite 6 + React 18 + TypeScript strict + Zustand + Tailwind + Radix Dialog/Tooltip + lucide-react + react-diff-viewer-continued |
| `web/src/App.tsx` | `useWsClient` hook + `classifyView` derived selector 切屏 |
| `web/src/lib/classifyView.ts` | 路由优先级：**L5（终态）> L2 > L3 > L4 > L1** |
| `web/src/ws/client.ts` | `resolveWsUrl()` 从 `?ws=` URL 参数解析；指数退避重连 500ms→30s + `manuallyClosed` flag |
| `web/src/store/` | zustand store：snapshotStore / activityStore / conflictDraftStore（L3）/ planReviewDraftStore（L2，含 commitApprove/Reject/Modify 两步协议） |
| `web/src/views/` | RunDashboard (L1) / PlanReview (L2) / ConflictResolution (L3) / JudgeVerdict (L4) / Report (L5) |
| `web/src/lib/markdown.tsx` | 自研轻量 markdown 渲染器，零原生 innerHTML 注入，无需 DOMPurify |

---

## 2. 五屏用户旅程

启动 `merge <branch>` 后，浏览器自动打开 `http://localhost:<web-port>/?ws=<ws-port>`，
URL 不变，前端依据 `classifyView(snapshot)` 在五屏之间切换：

| Layer | 触发条件（snapshot 状态） | 主要交互 |
|---|---|---|
| **L1** Dashboard `RunDashboard.tsx` | 默认 / 运行中 | 实时 phase 时间轴、Agent 活动流、成本卡、决策计数、风险徽章 |
| **L2** Plan Review `PlanReview.tsx` | `AWAITING_HUMAN` + `pendingUserDecisions` 有 HUMAN_REQUIRED | 文件级 Approve / Reject / Modify；本地 draft + 两步提交（`submit_user_plan_decisions` + `submit_plan_review approve`）；Reject 路径**不**发 `submit_user_plan_decisions`（绕过自动 APPROVE 副作用） |
| **L3** Conflict Resolution `ConflictResolution.tsx` | `AWAITING_HUMAN` + `humanDecisionRequests` 有未决 | 冲突点 marker / DiffViewer / DecisionPanel（TAKE_CURRENT / TAKE_TARGET / SEMANTIC_MERGE / MANUAL_PATCH / ESCALATE_HUMAN / SKIP）；BatchActionBar 批量同决策 |
| **L4** Judge Verdict `JudgeVerdict.tsx` | `judgeVerdict != null && judgeResolution == null && status == AWAITING_HUMAN` | VetoBanner（veto_triggered）/ IssuesList（按 severity 配色 + file_path 分组）/ Failed files diff / Repair rounds progress / Accept / Abort / Rerun 三按钮（Rerun 达 `maxJudgeRepairRounds` 时禁用） |
| **L5** Report & Memory `Report.tsx` | `status === COMPLETED \|\| status === FAILED`（终态最高） | 渲染 `merge_report.md`、cost 详情、memory 浏览器、checkpoint.json 下载链接 |

### L2 两步提交协议（关键）

```
[Approve all]   → ws send "submit_user_plan_decisions" with item map
                → ws send "submit_plan_review" {action: "approve"}
[Reject]        → ws send "submit_plan_review" {action: "reject", reason}
                  （绝不发 submit_user_plan_decisions，避免 _apply_user_plan_decisions
                   把 HUMAN_REQUIRED 全部自动标 APPROVE 的副作用）
[Modify]        → 修改 draft 后等同 Approve 路径
```

### L4 三按钮语义

| 按钮 | 后端 cmd payload | 效果 |
|---|---|---|
| Accept | `submit_judge_resolution {value:"accept"}` | 接受 verdict，进 commit |
| Abort | `submit_judge_resolution {value:"abort"}` | 终止本 run（FAILED） |
| Rerun | `submit_judge_resolution {value:"rerun"}` | 触发一次 repair round，phase 回到 EXECUTION |

后端 `_apply_judge_resolution` 设 `state.judge_resolution`，并 `set()`
**独立** event `_judge_resolution_received`（不复用 `_plan_review_received`，
避免语义被 abuse），主循环用 `wait_for_judge_resolution()` 阻塞等待。

---

## 3. 开发与调试

```bash
# 前端 dev server（连后端 mock-bridge）
cd web && npm install && npm run dev

# 后端 mock state_snapshot 推流（无需真实 fork repo / LLM API key）
MOCK_VIEW=dashboard python web/dev/mock-bridge.py
MOCK_VIEW=plan_review python web/dev/mock-bridge.py
MOCK_VIEW=conflict python web/dev/mock-bridge.py
MOCK_VIEW=judge python web/dev/mock-bridge.py
MOCK_VIEW=report python web/dev/mock-bridge.py
```

dev 默认 ws 端口 8765，前端 Vite dev server 默认 5173；浏览器开
`http://localhost:5173/?ws=8765`。

---

## 4. 部署

pip 安装的 wheel（`code_merge_system-*.whl`）通过 hatch
`[tool.hatch.build.targets.wheel.force-include]` 把 `web/dist` 打包到
`src/web/dist`，运行时 `_resolve_web_dist()` 经
`importlib.resources.files("src.web") / "dist"` 解析到 site-packages
内，因此用户**无需 Node.js** 即可运行 Web UI。

CI 在 `.github/workflows/ci.yml` 的 `web-build` job 跑 `npm ci + npm
run build + npm test`；`test` job 在 `pip install` 之前先跑
`npm ci + npm run build` 以确保 `web/dist/` 存在。

---

## 5. Design decisions superseded vs handoff §1

本节记录 `doc/web-ui-redesign-handoff.md` §1 中两条决策的最终落地结果，
供后续回顾时对照：

- **handoff §1 第 7 条（L4 用独立 event）**：保留并已落地。后端
  `_apply_judge_resolution` 触发独立 `_judge_resolution_received`，
  CLI 主循环用 `wait_for_judge_resolution()` 等待，与 plan_review
  / conflict 三路彻底解耦。
- **handoff §1 第 9 条（marked + DOMPurify 渲染 L5 markdown）**：
  最终改用 `web/src/lib/markdown.tsx` 自研轻量渲染器，避免使用
  原生 HTML 注入 API，XSS 面更小，构建体积更轻，去掉 `marked`
  与 `dompurify` 两条 npm 依赖。
