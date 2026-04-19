# CLI / TUI / Web UI（`src/cli/`、`src/web/`、`tui/`）

> **版本**：2026-04-19
> 前端面：CLI（Click） + TUI（React Ink）。

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
    ├── run.py          # run_command_impl（merge 主命令内部调用）
    ├── resume.py       # resume 子命令实现
    ├── setup.py        # 首次运行向导（detect_or_setup）
    └── tui.py          # 启动 TUI + WS 桥
```

### 1.2 一站式命令

`merge <target-branch>` 是 `merge_command`，由 `_DefaultGroup` 把"第一个参数不是已注册子命令"的情况自动 forward 给它。

```bash
merge <branch>                  # 默认 → TUI
merge <branch> --no-tui         # 纯文本
merge <branch> --ci             # CI 模式：无交互，JSON 摘要到 stdout
merge <branch> --dry-run        # 仅分析，不写文件
merge <branch> -r               # 强制重新进入 setup 向导
merge <branch> --ws-port 8765   # 指定 TUI WebSocket 端口
```

流程：
1. `setup.detect_or_setup(target_branch, repo_path=".", reconfigure=...)` — 检测/创建 `.merge/config.yaml`、检查 API Key
2. 如未指定 `--ci`/`--no-tui` → 调用 `tui.tui_command_impl`
3. 否则 → `run.run_command_impl`

### 1.3 辅助子命令

```bash
merge resume --run-id <id>       [--decisions <yaml>] [--checkpoint <path>]
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

## 2. `src/web/` + `tui/` — 交互 TUI

### 2.1 通信模型

```
┌────────────┐       WebSocket         ┌──────────────┐
│  React Ink │ ◀──── ws://:8765 ────▶  │ MergeWSBridge│
│  (tui/src) │                         │ (web/ws_bridge)│
└────────────┘                         └───────┬──────┘
                                               │ observer
                                               ▼
                                         ┌──────────┐
                                         │StateMachine│
                                         └──────────┘
```

- 后端：`src/web/ws_bridge.py` 启动 `websockets` 服务器
- 状态机 `add_observer(bridge.notify_state_change)` 把每次 transition 推到前端
- Orchestrator `set_activity_callback(bridge.notify_activity)` 把 Agent 活动推到前端
- 前端（`tui/src`）：React Ink，渲染面板、接受用户输入、把人工决策通过 WS 回发

### 2.2 TUI 启动（`commands/tui.py`）

```python
def tui_command_impl(config_or_path, ws_port, dry_run=False):
    # 1. 解析 config
    # 2. 建 MergeState，启动 MergeWSBridge
    # 3. 构造 Orchestrator，挂 observer + activity callback
    # 4. 子进程启动 `npm run start` 在 tui/ 目录
    # 5. 同一 asyncio loop 跑 orchestrator.run(state)
```

子进程退出时同时关闭 WS 桥。

### 2.3 `tui/` 前端结构

```
tui/
├── package.json
├── tsconfig.json
└── src/
    └── ... (React Ink 组件)
```

构建与运行：
```bash
cd tui
npm run start    # 启动
npm run dev      # watch mode
npm run build    # tsc --noEmit 类型检查
```

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

merge upstream/main              # 进入向导，生成 .merge/config.yaml，进入 TUI
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
merge resume --run-id <id>
# 或带人工决策续跑
merge resume --run-id <id> --decisions decisions.yaml
```

---

## 4. 测试

- CLI 主命令测试：`tests/unit/test_cli.py`、`tests/unit/test_cli_flow.py`，用 `CliRunner` 驱动 Click
- TUI 前端：`cd tui && npm run build` 只做 TypeScript 类型检查，无交互测试（TUI 以集成为主）
- `.merge/` 目录测试：`tests/unit/test_paths.py`（如存在）用 `tmp_path` fixture

---

## 5. 扩展点

- 新子命令 → 在 `src/cli/commands/` 加实现，在 `main.py` 用 `@cli.command(...)` 注册
- 新 TUI 面板 → 在 `tui/src` 加 React 组件，通过 WS 消息类型与后端通信
- 新 Hook → 在外部代码中通过 `orchestrator.hooks.on("phase:after", cb)` 挂接，无须改源码
