我需要先了解当前代码的状态和 CVTE 定制内容。让我并行调查。

在执行命令前先按 Fact-Forcing Gate 要求陈述事实：

**1. 当前用户请求（一句话）：**
分析当前 `0.6.0-cvte` 分支是否已经把 CVTE 定制内容正确合入 upstream 0.6.0，参考依据是 Dify 仓库下 `md/plugin-daemon/` 中的 `plugins_readme.md` 与 `TechnicalSolution.md` 两份文档。

**2. 即将执行的命令将验证/产出什么：**
- `git log --oneline -30`：查看 `0.6.0-cvte` 分支最近 30 条提交，识别合并提交、CVTE 定制提交以及 upstream 0.6.0 升级提交的存在。
- `cat .merge/config.yaml`：查看本次合并任务配置（基线版本、目标版本、定制策略），确认合并目标是否就是 upstream 0.6.0 + CVTE 定制。
- `ls .merge/runs/2448e309-.../`：列出本次合并 run 产物（plan / report / checkpoint），定位可读的合并报告。
- `git status --short`：列出当前工作区未提交的文件，识别还未落盘的合并变更或脏状态。

下面重试这些只读命令：

事实已清楚：本次合并目标是从 CVTE 的 `upgrade/0.6.0` 抽取 **11 个 must-port 功能** + OSS 上传校验，应用到 upstream 0.6.0 基线，最终落到 `0.6.0-cvte` 工作分支。让我读取合并报告并验证关键文件实际状态。

Judge Verdict 显示存在 70 个 critical 问题。让我并行验证关键文件的实际状态。

Go 工具链未安装，改用静态检查。重点核对 Judge 报告中"严重损坏"的关键文件。

分析完成，无需更多检查。下面给出结论。

---

## 结论：**部分达成，存在关键缺口，未通过可编译性验证**

### 合并目标（来自 `.merge/config.yaml`）
从 CVTE `upgrade/0.6.0` 抽取 11 个 must-port features + OSS 上传校验，应用到 upstream 0.6.0 基线，落在 `0.6.0-cvte` 分支。第二份文档（TechnicalSolution.md）描述的是 daemon 通用架构（Local vs Serverless），CVTE 仅跑 Local 模式，serverless 取 upstream 为准。

### ✅ 已合入并通过静态验证
| # | Feature | 证据 |
|---|---------|------|
| 1 | Session TTL + cleanupLoop | [session.go:20-309](internal/core/session_manager/session.go:20)（落点是 `session_manager/`，非 plan 中提到的 `plugin_manager/manager.go`） |
| 2 | venv uninstall cleanup | [environment_python.go:58](internal/core/plugin_manager/local_runtime/environment_python.go:58) |
| 3 | GarbageCollector | [garbage_collector.go](internal/core/plugin_manager/garbage_collector.go) |
| 4 | Parallel plugin loading | [run.go:163](internal/core/plugin_manager/local_runtime/run.go:163) `sync.WaitGroup` |
| 6 | gevent Python 3.12 兼容 | [environment_python.go:588-601](internal/core/plugin_manager/local_runtime/environment_python.go:588) |
| 8 | Media Cache 引用计数 | [reference_counter.go](internal/core/plugin_manager/media_transport/reference_counter.go) + [asset_reference.go](internal/types/models/asset_reference.go) + commit `14992b93` |
| 11 | TCP graceful close | [hooks.go:99,236](internal/core/plugin_manager/debugging_runtime/hooks.go:99) |
| Extra | Prometheus 监控 | [prometheus.go](internal/server/controllers/prometheus.go) + commit `da5a5463` |

### ❌ 未合入 / 关键缺口
| # | Feature | 证据 |
|---|---------|------|
| 7 | Re-upload Declaration 未更新 | `git diff 0.6.0..HEAD -- internal/service/install_plugin.go` = **0 行差异**（即 [install_plugin.go](internal/service/install_plugin.go) 是 upstream 原版） |
| 9 | Plugin-not-found after upgrade | 同上，install_plugin.go 全文取 upstream |
| 10 | Plugin-not-found after uninstall+reinstall | 同上 |
| Extra | OSS eventual-consistency upload validation | install_plugin.go 内无任何 oss/eventual/consistency/HeadObject 字样；`config.yaml` 标注 **MUST PRESERVE**（与 plugins_readme.md 中的租户隔离约束直接挂钩） |
| 5 | 插件 stop race fix | `local_runtime/instance.go` 不存在（plan 指向位置）；HEAD 全 `local_runtime/` 中无 stopOnce/stopFlight 同步原语。CVTE 老分支 `stdio.go:27` 与 `type.go:44` 有相关 mutex，**需逐字段对比确认锁是否完整移植**（未确认） |

> `install_plugin.go` 与 upstream 完全一致是最强信号——它是 4 项 must-port + OSS 校验的核心载体。`upgrade/0.6.0:internal/service/install_plugin.go` 与 HEAD 差 **1160 行**，全部丢失。

### ⚠️ 报告本身存在矛盾
- `merge_report.md` 给的 Judge Verdict：`Result=pass / Confidence=0.70`，但同帧列出 **70 critical + 13 high**。
- Judge 列出的"truncation markers / `# ... (N sections omitted)`"在以下 5 个文件**已不存在**（grep 实测）：[config.go](internal/types/app/config.go) 400 行、[atomic.go](internal/types/models/curd/atomic.go) 729 行、[persistence_test.go](internal/core/persistence/persistence_test.go) 203 行、[session.go](internal/core/session_manager/session.go) 355 行、[plugins.go](internal/server/controllers/plugins.go) 323 行。推测最后那次提交 `94e50c62 merge(human_review): resolve 162 files` 已修掉。
- Judge 说"serverless 整目录消失"不准确：upstream 0.6.0 本身就**不含** `plugin_manager/serverless_*`（git ls-tree 验证），CVTE 旧路径 `internal/core/serverless_runtime/` 仍在磁盘上。

### ⚠️ 未通过的硬验证
- **`go build ./...` 未跑**：本机无 Go 工具链。可编译性未确认。
- **测试未跑**（按用户规则不主动跑）。
- `.github/workflows/tests-{db-integration,plugin-integration,unit}.yml` 已删除——CI 也不会兜底。

### 建议下一步（按优先级）
1. **必补**：从 `upgrade/0.6.0:internal/service/install_plugin.go` 把 Features 7/9/10 + OSS 校验 cherry-pick 到 HEAD。注意 upstream 0.6.0 把 `internal/utils/...` 迁到 `pkg/utils/...`，import 需重写。
2. **核实 Feature 5**：对比 `git show upgrade/0.6.0:internal/core/plugin_manager/local_runtime/stdio.go` 与 HEAD 同名文件的锁字段、`waitChanLock` 等，确认 race fix 完整移植。
3. **可编译性闸门**：装 Go 1.23.3 跑 `go build ./...` 与 `go vet ./...`；恢复至少一个 unit-test workflow 让 PR CI 兜底。
4. **存疑修复**：`internal/types/exception/factory.go`、`internal/core/dify_invocation/invcation.go`、`internal/core/persistence/*` 在报告里被标 `escalate_human / confidence=0.00`，需要逐文件人工 review 确认是否仍是 upstream 原版而非半合并状态。

是否需要我把 install_plugin.go 这部分按方案 A（每租户独立上传，参考 plugins_readme.md §4）整理一份具体修改计划？