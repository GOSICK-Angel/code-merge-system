# 交接文档 — upstream-50-v2 验证重跑

**目的**：P0（O-M1/O-M2/O-L3）+ P1（O-B3）修复后端到端验证。
**启动时间**：2026-04-23 ~10:14
**预计检查时间**：启动后 4 小时

## 关键标识

| 项 | 值 |
|---|---|
| run_id | `6171dd37-9b02-4f86-9a08-b8bb64ee94c8` |
| repo | `/Users/angel/Desktop/WA_AI/project/dify-official-plugins` |
| 基线 | `feat_merge` @ `d73426c5`（已 reset） |
| 上游 | `test/upstream-50-commits-v2` @ `19d4300e` |
| 日志 | `/tmp/merge-upstream-50-v2-rerun.log` |
| debug log | `outputs/debug/run_6171dd37-9b02-4f86-9a08-b8bb64ee94c8.log` |
| checkpoint | `outputs/debug/checkpoints/checkpoint.json` |
| LLM traces | `outputs/debug/llm_traces_6171dd37-9b02-4f86-9a08-b8bb64ee94c8.jsonl` |

## 已应用修复（git log）

- `9c0f454` fix: P0 修复 — AUTO_MERGE 死循环（O-M1/O-M2/O-L3）
- `513e352` fix: P1 修复 — 二进制资源白名单（O-B3）

## 唤醒时的判断流程（最少 token 版）

**Step 1** — 一条命令看进程和状态：

```bash
pgrep -fl "merge resume" 2>/dev/null; \
tail -5 /tmp/merge-upstream-50-v2-rerun.log; \
python3 -c "import json; s=json.load(open('/Users/angel/Desktop/WA_AI/project/dify-official-plugins/outputs/debug/checkpoints/checkpoint.json')); print('status=',s.get('status'),'phase=',s.get('current_phase'),'pending=',len([r for r in s.get('human_decision_requests',{}).values() if r.get('human_decision') is None]),'exhausted=',s.get('auto_merge_dispute_exhausted_layers'),'verdict=',bool(s.get('judge_verdict')))"
```

**Step 2** — 按输出分支：

| 现象 | 含义 | 下一步 |
|---|---|---|
| 进程存在 + status=`auto_merging`/`judge_reviewing` | 仍在跑 | 再 schedule 1 小时 wakeup |
| 进程消失 + status=`awaiting_human` + pending>0 | 正常人工决策点 | 见 Step 3 |
| 进程消失 + status=`awaiting_human` + pending=0 + exhausted 非空 | O-L3 前进到 JUDGE_REVIEWING 后又 pause | `merge resume --run-id 6171dd37-9b02-4f86-9a08-b8bb64ee94c8`（不带 decisions） |
| 进程消失 + status=`completed` | 🎉 成功 | 生成最终报告 |
| 进程消失 + status=`failed` | 终止失败 | 读 errors，生成失败报告 |
| 进程消失 + status=`awaiting_human` + pending=0 + exhausted 空 + verdict=True | Judge 等 accept/rerun/abort | decisions 填 `judge_resolution: accept` 后 resume |

**Step 3 — 处理人工决策（仅当 pending>0）**

先 `grep -E "O-B3|O-M1|routing|conflict markers"` 日志，确认修复生效再决策。

对 CVTE tongyi 的 python 文件（llm.py/rerank.py 等）：`semantic_merge`  
对 CVTE tongyi 的 yaml：`take_target`（格式化变更）  
对 CVTE 其他插件二进制：`take_target`（非关键图标）  
对非 CVTE 插件所有文件：`take_target`

写到 `/tmp/merge-upstream-50-v2-rerun-decisions.yaml`，schema：

```yaml
decisions:
  - file_path: <path>
    decision: take_target   # 或 take_current / semantic_merge / escalate_human
    notes: <reason>
  # ... repeat per file
```

然后 `merge resume --run-id 6171dd37-9b02-4f86-9a08-b8bb64ee94c8 --decisions /tmp/merge-upstream-50-v2-rerun-decisions.yaml`

## 需要验证的修复信号

从 debug log grep：

- ✅ **O-B3 生效**：`grep "O-B3: routing.*binary asset"` 应非空
- ✅ **O-M1 生效**：`grep "O-M1:.*unresolved conflict markers"` 若有则说明拦截到；若无说明本次 cherry-pick 没产生 UU（也好）
- ✅ **O-L3 生效**：若 `auto_merge_dispute_exhausted_layers` 非空，`status=awaiting_human` 时 `human_decision_requests` **必须**有 pending 项（不是 0）
- ✅ **未死循环**：对比 51-commits 报告的 14826s（4h7m），若耗时 < 30min 认为流程通畅

## 最终报告位置

完成后写到 `doc/test-report/upstream-50-v2-rerun-test-report.md`，参考现有两份报告结构（Phase 断言 / JudgeVerdict / Memory / 上下文 / 优化建议）。

## 上下文节省提示

- **不要读整份 debug log**（会 > 50KB），用 `tail -80` 或 `grep -E "pattern"`
- **不要全量 cat checkpoint.json**，用上面的 Python 一行 summary
- 只有在"确定要生成最终报告"时才批量读 traces
