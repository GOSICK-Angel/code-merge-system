# CLI / Web UI（`src/cli/`、`src/web/`、`web/`）

> **版本**：2026-05-15
> 前端面：CLI（Click） + Web UI（React + Vite，浏览器内）。

---

## 1. `src/cli/`

### 1.1 文件布局
```
src/cli/
├── main.py             # Click 入口：cli = _DefaultGroup，注册所有子命令
├── env.py              # .env 加载（.merge/.env、~/.config/code-merge-system/.env、shell）
├── exit_codes.py       # CI 退出码常量
├── paths.py            # .merge/ 目录管理（生产模式）+ dev-mode 判定
└── commands/
    ├── run.py          # run_command_impl（merge 主命令内部调用，无 UI）
    ├── resume.py       # resume 子命令实现
    ├── setup.py        # 首次运行向导（detect_or_setup）
    └── web.py          # web_command_impl / web_resume_impl —— 启动 Web UI
```

### 1.2 一站式命令

`merge <target-branch>` 是 `merge_command`，由 `_DefaultGroup` 把"第一个参数不是已注册子命令"的情况自动 forward 给它。

```bash
merge <branch>                   # 默认 → 浏览器打开 Web UI
merge <branch> --no-web          # 纯文本输出
merge <branch> --no-browser      # 启动 Web UI 但不自动打开浏览器，仅打印 URL
merge <branch> --ci              # CI 模式：无交互，JSON 摘要到 stdout
merge <branch> --dry-run         # 仅分析，不写文件
merge <branch> -r                # 强制重新进入 setup 向导
merge <branch> --ws-port 8765    # 指定 WebSocket 端口
merge <branch> --web-port 5173   # 指定 HTTP static server 端口
```

流程：
1. `setup.detect_or_setup(target_branch, repo_path=".", reconfigure=...)` — 检测/创建 `.merge/config.yaml`、检查 API Key
2. 如未指定 `--ci`/`--no-web` → 调用 `web.web_command_impl`（启动 Web UI + Orchestrator）
3. 否则 → `run.run_command_impl`

### 1.3 辅助子命令

```bash
merge resume --run-id <id>       [--decisions <yaml>] [--checkpoint <path>] [--web]
merge validate --config <path>   # 校验配置 + 所有 api_key_env 是否存在
```

### 1.4 `validate_config_and_env`

`merge validate` 的核心逻辑（`main.py::validate_config_and_env`）做两件事：
1. 遍历 `config.agents` 所有 Agent 的 `api_key_env`，检查 `os.environ[...]` 是否设置
2. 打开 git 仓库验证 `upstream_ref`、`fork_ref` 是否存在（`rev_parse`）

返回 `list[str]` 错误清单，有错即 `exit(1)`。

### 1.5 `.merge/` 目录管理（`paths.py`）

- `is_dev_mode()` — 检测代码仓库本身（有 `pyproject.toml` 且 `name == "code-merge-system"`），返回 True 则走 `outputs/` 布局
- `ensure_merge_dir(repo_path)` — 生产模式下确保 `.merge/` 存在；写 `.gitignore`（忽略 `.env` 与 `runs/`）
- `get_run_dir(repo_path, run_id)` — 返回 `<repo>/.merge/runs/<run_id>/`
- `get_system_log_dir(repo_path)` — `<run_dir>/logs/`

### 1.6 API Key 加载顺序（`env.py`）

1. Shell 环境变量（最高优先级）
2. `<repo>/.merge/.env`
3. `~/.config/code-merge-system/.env`

用户显式导出 → 生效；未导出则逐层 fallback。

---

## 2. `src/web/` + `web/` — 浏览器 Web UI

### 2.1 通信模型

```
┌──────────────┐       WebSocket         ┌──────────────┐
│ React + Vite │ ◀──── ws://:8765 ────▶  │ MergeWSBridge│
│   (web/src)  │                         │ (src/web/    │
│              │ ◀─ HTTP /index.html ──  │  ws_bridge)  │
│              │ ◀─ HTTP /runs/<id>/* ── │ StaticHTTPServer
└──────────────┘                         └───────┬──────┘
                                                 │ observer
                                                 ▼
                                          ┌────────────┐
                                          │StateMachine│
                                          └────────────┘
```

- HTTP static：`src/web/static_server.py` — stdlib ThreadingHTTPServer，
  托管 `web/dist`（SPA fallback 到 `index.html`），并暴露
  `/runs/<run_id>/*` 路由从 `<repo>/.merge/runs/<run_id>/` 读
  merge_report.md / checkpoint.json（路径穿越防护）。
- WebSocket：`src/web/ws_bridge.py` 启动 `websockets` 服务器。
- 状态机 `add_observer(bridge.notify_state_change)` 把每次 transition 推到前端。
- Orchestrator `set_activity_callback(bridge.notify_activity)` 把 Agent 活动推到前端。
- 前端（`web/src`）：React 18 + TypeScript strict + Zustand store +
  Tailwind + Radix；渲染五屏 L1/L2/L3/L4/L5，接受用户输入，把人工决策
  通过 WS 回发。

### 2.2 Web UI 启动（`commands/web.py`）

```python
def web_command_impl(config_or_config_path, ws_port, web_port, dry_run, open_browser):
    # 1. 解析 config，建 MergeState
    # 2. asyncio.run(_run_web(...))
    #    a. _resolve_web_dist()：先试 importlib.resources.files("src.web")/"dist"，
    #       失败时 fallback 到 <repo>/web/dist（source checkout）
    #    b. 启动 MergeWSBridge + StaticHTTPServer（含 runs_root）
    #    c. 同一 asyncio loop 跑 orchestrator.run(state) 主循环
    #       在 AWAITING_HUMAN 处等三类决策：conflict / plan_review / judge
```

### 2.3 `web/` 前端结构

```
web/
├── package.json
├── tsconfig.json
├── vite.config.ts
└── src/
    ├── App.tsx                # useWsClient hook + classifyView selector
    ├── lib/classifyView.ts    # 路由：L2 > L3 > L4 > L1（L5 终态最高）
    ├── ws/client.ts           # ?ws=<port> 解析 + 指数退避重连
    ├── store/                 # zustand：snapshotStore / activityStore /
    │                          # conflictDraftStore / planReviewDraftStore
    ├── components/            # 五屏的可复用组件
    └── views/                 # RunDashboard (L1) / PlanReview (L2) /
                               # ConflictResolution (L3) / JudgeVerdict (L4) /
                               # Report (L5)
```

构建与运行：
```bash
cd web
npm install      # 第一次需要
npm run start    # 启动 Vite dev server
npm run dev      # watch mode
npm run build    # tsc + vite build → web/dist/
npm test         # vitest
```

> wheel 打包通过 hatch `force-include` 把 `web/dist` 映射到
> `src/web/dist`，因此 pip install 用户无需 Node.js 即可运行 Web UI。

---

## 3. 典型使用场景

### 3.1 用户（downstream fork 维护者）本地首次运行

> 这里的"开发者"指**使用 code-merge-system 的开发者**，即在自己 fork 项目中集成该工具的人，而非 code-merge-system 本身的开发者。

```bash
cd ~/projects/my-fork
pip install code-merge-system

# 必填
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...

# 可选：使用代理或私有端点时才需要设置
# export ANTHROPIC_BASE_URL=https://your-proxy.example.com
# export OPENAI_BASE_URL=https://your-proxy.example.com/v1

merge upstream/main              # 进入向导，生成 .merge/config.yaml，浏览器打开 Web UI
```

### 3.2 CI 流水线
```yaml
- run: merge upstream/main --ci --dry-run
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    # ANTHROPIC_BASE_URL: ${{ secrets.ANTHROPIC_BASE_URL }}  # 可选
    # OPENAI_BASE_URL: ${{ secrets.OPENAI_BASE_URL }}        # 可选
```
退出码：0 = 全自动合并成功 / 非零 = 需要人工介入（具体见 `exit_codes.py`）。

### 3.3 中断后恢复
```bash
# 中断后 checkpoint.json 已落盘
merge resume --run-id <id>                # 纯文本路径
merge resume --run-id <id> --web          # 浏览器 Web UI 路径
merge resume --run-id <id> --decisions decisions.yaml
```

---

## 4. 测试

- CLI 主命令测试：`tests/unit/test_cli.py`、`tests/unit/test_cli_flow.py`、`tests/unit/test_resume_web_dispatch.py`，用 `CliRunner` 驱动 Click，覆盖 web 默认 / `--no-web` / `--ci` 与 resume `--web` 路由
- Web UI 前端：`cd web && npm run build` 做 TypeScript 类型检查；`npm test` 跑 vitest 覆盖 store / view 单测
- 后端 Web 层：`tests/unit/test_serializers.py` / `tests/unit/test_ws_bridge_extensions.py` / `tests/unit/test_ws_bridge_threading.py` / `tests/unit/test_static_server.py` 覆盖 serialize / cancel / replay / 路径穿越
- 清理回归：`tests/unit/test_cleanup_residue.py` 防止 TUI 残留再次混入 src/ / tests/ / 用户文档
- `.merge/` 目录测试：`tests/unit/test_paths.py`（如存在）用 `tmp_path` fixture

---

## 5. 扩展点

- 新子命令 → 在 `src/cli/commands/` 加实现，在 `main.py` 用 `@cli.command(...)` 注册
- 新 Web UI 视图 → 在 `web/src/views/` 加 React 组件，在 `lib/classifyView.ts` 加 selector 分支，必要时扩 serializer 字段与 `MergeWSBridge` 入站命令
- 新 Hook → 在外部代码中通过 `orchestrator.hooks.on("phase:after", cb)` 挂接，无须改源码
