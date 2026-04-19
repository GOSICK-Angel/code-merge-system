# Migration-Aware Merge: Design Document

## 1. Problem Statement

### 1.1 Background

In long-running fork maintenance, a common scenario is:

```
upstream:  C1 → C2 → ... → C50 → C51 → ... → C100 (HEAD)
                              ↑
fork:      F1 → F2 → ... → Fm → ... → Fn (HEAD)
                              ↑
                   Code migration at this point:
                   Files from C1~C50 were copied into the fork
                   WITHOUT using git merge/cherry-pick
```

The fork was created at some historical point, and at a later time, upstream code was manually copied ("migrated") into the fork — losing the git commit history for those changes. This creates a **stale merge-base** problem:

- `git merge-base upstream fork` returns the original fork point (before C1), not the effective sync point (C50)
- The system's three-way file classification treats C1~C50 changes as "unmerged", producing false diffs
- Cherry-pick replay (implemented in `commit_replayer.py`) attempts to replay ALL upstream commits since the ancient merge-base, including ones whose content already exists in the fork

### 1.2 Current System Behavior

The system partially addresses this via `PollutionAuditor` (`src/tools/pollution_auditor.py`):
- Searches fork history for commits with messages matching `merge.*upstream|Merge.*upstream|merge.*release`
- Reclassifies files touched in those commits using hash comparison
- **Limitation**: Only detects migration commits with specific message patterns; silent code copies are missed
- **Limitation**: No `merge_base_override` config option exists
- **Limitation**: No user-facing choice when migration is detected

### 1.3 Goal

When a migration scenario is detected:
1. Automatically identify the effective sync point (the upstream commit where code was last synchronized)
2. Offer the user explicit choices for how to proceed
3. Only process upstream commits **after** the sync point, avoiding redundant work and false conflicts

---

## 2. Recommended Approach: Merge-Base Override + Auto Sync-Point Detection

### 2.1 Overview

```
InitializePhase._run_sync():
  1. Compute git merge-base (existing)
  2. If config.merge_base_override is set → use it directly
  3. Otherwise, run SyncPointDetector:
     a. Enumerate upstream commits since merge-base
     b. For each commit (newest → oldest), check if its file changes
        are already present in fork HEAD
     c. Find the boundary: last "already synced" commit = effective merge-base
  4. If effective_base != git_merge_base (migration detected):
     → Store migration_info in state
     → In TUI/report, show detection result and recommended action
     → User can confirm or override via config
  5. Use final merge-base for file classification + commit replay
```

### 2.2 User-Facing Options (when migration is detected)

| Option | Description | When to Use |
|--------|-------------|-------------|
| **A: Use detected sync-point** | System auto-detected that upstream commits up to `C50` are already in the fork. Use `C50` as merge-base. | Recommended for most cases |
| **B: Specify commit ID** | User provides a specific upstream commit SHA as the merge-base override. | When auto-detection is inaccurate |
| **C: Use git merge-base** | Ignore migration detection, use the original `git merge-base`. May produce many false conflicts. | When fork truly diverged from the ancient point |
| **D: Migration mode** | No git history preservation. Apply all changes via `apply_with_snapshot`. | When history is too messy to salvage |

---

## 3. Detailed Design

### 3.1 New: SyncPointDetector (`src/tools/sync_point_detector.py`)

```python
class SyncPointResult(BaseModel):
    detected: bool                     # Whether migration was detected
    effective_merge_base: str          # The commit to use as merge-base
    git_merge_base: str                # Original git merge-base
    synced_file_count: int             # Number of upstream-changed files already in fork
    upstream_changed_file_count: int   # Total files changed in upstream since merge-base
    sync_ratio: float                  # synced / total (0.0 ~ 1.0)
    last_synced_commit: str | None     # SHA of last upstream commit found in fork
    first_unsynced_commit: str | None  # SHA of first upstream commit NOT in fork
    confidence: float                  # Detection confidence (0.0 ~ 1.0)
    skipped_commit_count: int = 0      # Number of commits skipped (already synced)
    patch_id_promoted_count: int = 0   # Files promoted to synced via Patch-ID (Phase 1b)

class SyncPointDetector:
    def __init__(
        self,
        sync_ratio_threshold: float = 0.3,   # min sync_ratio to trigger detection
        min_synced_files: int = 5,            # min synced files to trigger detection
        enable_patch_id: bool = True,         # enable Phase 1b Patch-ID verification
    ) -> None: ...

    def detect(
        self,
        git_tool: GitTool,
        merge_base: str,
        fork_ref: str,
        upstream_ref: str,
    ) -> SyncPointResult:
        """
        Three-phase algorithm:
        Phase 1  — File-level sync detection (O(3) git ls-tree calls):
          Compare blob hashes at merge_base / fork / upstream.
          A file is "synced" when upstream changed it AND fork_hash == up_hash.
          A file is "ambiguous" when all three hashes differ (both sides changed).

        Phase 1b — Patch-ID verification (only for ambiguous files, optional):
          For each ambiguous file, compare the patch-ID of upstream's diff vs fork's
          diff relative to merge_base. If patch-IDs match, the fork applied the same
          logical change (e.g., a copy with minor tweaks) → promote to synced.

        Phase 2  — Commit-level boundary detection (only if Phase 1/1b detects migration):
          Walk upstream commits oldest→newest. For each commit, check if ALL
          its modified files belong to the synced set.
          Uses binary search when commit count > 50; linear scan otherwise.
          The last commit where this is true = effective merge-base.

        Confidence = file_factor × ratio_factor, capped at 1.0.
        """
```

#### Detection Algorithm (detailed)

```
Input:
  merge_base: str (git merge-base result)
  fork_ref: str
  upstream_ref: str

Phase 1 — File-level detection (3 bulk ls-tree calls):
  base_hashes = git_tool.list_files_with_hashes(merge_base)
  fork_hashes = git_tool.list_files_with_hashes(fork_ref)
  up_hashes   = git_tool.list_files_with_hashes(upstream_ref)

  synced_files: set[str] = set()
  upstream_changed_files: set[str] = set()
  ambiguous_files: set[str] = set()

  for fp in (up_hashes.keys() | base_hashes.keys()):
    up_hash   = up_hashes.get(fp)
    base_hash = base_hashes.get(fp)
    fork_hash = fork_hashes.get(fp)

    if up_hash is None or up_hash == base_hash:
      continue  # upstream didn't change this file
    upstream_changed_files.add(fp)

    if fork_hash == up_hash:
      synced_files.add(fp)                       # exact blob match → migrated
    elif fork_hash is not None and fork_hash != base_hash and fork_hash != up_hash:
      ambiguous_files.add(fp)                    # all three differ → may be tweaked copy

  sync_ratio = len(synced_files) / len(upstream_changed_files) if upstream_changed_files else 0.0

Phase 1b — Patch-ID verification (only when enable_patch_id=True and ambiguous_files non-empty):
  for fp in ambiguous_files:
    up_pid   = git_tool.get_diff_patch_id(merge_base, upstream_ref, fp)
    fork_pid = git_tool.get_diff_patch_id(merge_base, fork_ref, fp)
    if up_pid and fork_pid and up_pid == fork_pid:
      synced_files.add(fp)   # same logical change, just tweaked → promote to synced

  # Re-compute sync_ratio after promotion
  sync_ratio = len(synced_files) / len(upstream_changed_files) if upstream_changed_files else 0.0
  detected = (sync_ratio >= sync_ratio_threshold and len(synced_files) >= min_synced_files)

Phase 2 — Commit boundary detection (only when detected=True):
  commits = git_tool.list_commits(merge_base, upstream_ref)  # oldest first

  if len(commits) > 50:
    # Binary search: assume synced commits form a contiguous prefix
    lo, hi, boundary = 0, len(commits) - 1, -1
    while lo <= hi:
      mid = (lo + hi) // 2
      files = set(commits[mid]["files"])
      if not files or files.issubset(synced_files):
        boundary = mid; lo = mid + 1
      else:
        hi = mid - 1
    effective_base = commits[boundary]["sha"] if boundary >= 0 else merge_base
  else:
    # Linear scan: more robust for non-contiguous synced commits
    effective_base = merge_base
    for commit in commits:
      commit_files = set(commit.get("files", []))
      if not commit_files or commit_files.issubset(synced_files):
        effective_base = commit["sha"]   # advance boundary
      else:
        break                            # first unsynced commit → stop

  # Note: synced_files is built from file-level hash/patch-ID comparison,
  # so it correctly handles cases where later upstream commits revisit the
  # same files — the set membership check always reflects HEAD-level state.
```

#### 3.1.1 Phase 1b: Patch-ID Verification (for tweaked copies)

> **Implementation note**: The original design proposed checking intermediate commit blob hashes for "ambiguous" files (all three hashes differ). This approach requires O(commits × ambiguous_files) `git show` calls and was not implemented. Instead, the actual implementation uses **git patch-ID**, which compares the semantic diff rather than the blob, and requires only O(ambiguous_files) calls.

When a file was copied with minor tweaks (whitespace, comment changes, local adaptations), blob hashes will differ between fork and upstream even though the logical change is the same. Patch-ID catches this case:

```
Ambiguous file: base_hash != up_hash AND fork_hash != up_hash AND fork_hash != base_hash
  → Both sides changed the file differently by blob; exact-match fails

Patch-ID check:
  up_pid   = git diff <merge_base>..<upstream_ref> -- <fp> | git patch-id
  fork_pid = git diff <merge_base>..<fork_ref>     -- <fp> | git patch-id

  if up_pid == fork_pid:
    → The diff applied to the file is semantically identical → promote to synced
    → patch_id_promoted_count += 1
```

**Trade-off vs. per-commit blob check (original design)**:

| | Patch-ID (implemented) | Per-commit blob check (original design) |
|---|---|---|
| API calls | O(ambiguous_files) | O(commits × ambiguous_files) |
| Detects | Tweaked copies at HEAD | Partial sync at any intermediate commit |
| Per-file merge-base | No (commit-level only) | Yes (most granular) |
| Complexity | Low | High |

Per-file effective merge-base (the most granular approach from the original design) remains a future enhancement.

### 3.2 Config Changes (`src/models/config.py`)

```python
class MigrationConfig(BaseModel):
    merge_base_override: str | None = Field(
        default=None,
        description="Override the git merge-base with a specific commit SHA. "
        "Use this when the fork was created via code migration.",
    )
    auto_detect_sync_point: bool = Field(
        default=True,
        description="Automatically detect if upstream commits have already been "
        "migrated into the fork.",
    )
    sync_detection_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Ratio of synced upstream-changed files that triggers migration "
        "detection. 0.3 means >30% of files must be synced.",
    )
    min_synced_files: int = Field(
        default=5,
        ge=1,
        description="Minimum number of synced files required to trigger detection. "
        "Prevents false positives when few files changed.",
    )

class MergeConfig(BaseModel):
    ...
    migration: MigrationConfig = Field(default_factory=MigrationConfig)
```

### 3.3 State Changes (`src/models/state.py`)

```python
from src.tools.sync_point_detector import SyncPointResult

class MergeState(BaseModel):
    ...
    migration_info: SyncPointResult | None = Field(
        default=None,
        description="Migration detection results from SyncPointDetector.",
    )
```

### 3.4 InitializePhase Changes (`src/core/phases/initialize.py`)

```python
def _run_sync(self, state, ctx):
    # 1. Compute git merge-base (existing)
    git_merge_base = ctx.git_tool.get_merge_base(
        state.config.upstream_ref, state.config.fork_ref
    )
    merge_base = git_merge_base

    # 2. Check for explicit override
    migration_cfg = state.config.migration
    if migration_cfg.merge_base_override:
        merge_base = migration_cfg.merge_base_override
        logger.info("Using merge_base_override: %s", merge_base)
    
    # 3. Auto-detect sync point
    elif migration_cfg.auto_detect_sync_point:
        detector = SyncPointDetector(
            sync_ratio_threshold=migration_cfg.sync_detection_threshold,
            min_synced_files=migration_cfg.min_synced_files,
        )
        result = detector.detect(
            ctx.git_tool, merge_base,
            state.config.fork_ref, state.config.upstream_ref,
        )
        state.migration_info = result
        
        if result.detected:
            logger.info(
                "Migration detected: %d/%d upstream-changed files synced "
                "(%.0f%%), effective merge-base: %s",
                result.synced_file_count,
                result.upstream_changed_file_count,
                result.sync_ratio * 100,
                result.effective_merge_base,
            )
            merge_base = result.effective_merge_base
    
    # 4. Proceed with classification using final merge-base
    state.merge_base_commit = merge_base
    ...
```

### 3.5 Integration with Cherry-Pick Replay

The cherry-pick replay system (from the previous session) already works correctly with the adjusted merge-base:

- `list_commits(merge_base, upstream_ref)` will only return commits **after** the effective merge-base
- `classify_commits()` will correctly classify these remaining commits
- Already-migrated commits are excluded because the effective merge-base is set past them

No changes needed in `commit_replayer.py` or `auto_merge.py`.

### 3.6 TUI / Report Integration

When migration is detected, the system should:

1. **In TUI**: Display a notification during InitializePhase:
   ```
   ⚠ Migration detected: 42/100 upstream commits (42%) appear to be
     already present in the fork.
     
     Effective merge-base: abc1234 (2025-11-15)
     Git merge-base:       def5678 (2024-03-01)
     
     Using detected sync-point. Override with:
       merge_base_override: "abc1234"  in config YAML
   ```

2. **In plan report** (`merge_plan_report.py`): Include a migration section showing the detection result and which commits were skipped.

3. **In state checkpoint**: Store `migration_info` for resume support.

---

## 4. Implementation Plan

> **状态**: 已全部实现，并在以下方面超出原设计。

### Phase 1: Core Detection

| File | Action | 说明 |
|------|--------|------|
| `src/tools/sync_point_detector.py` | **New** | `SyncPointDetector` + `SyncPointResult`，含三阶段算法 |
| `src/models/config.py` | Modify | 新增 `MigrationConfig` + `MergeConfig.migration` 字段 |
| `src/models/state.py` | Modify | 新增 `migration_info: SyncPointResult \| None` 字段 |

### Phase 2: Integration

| File | Action | 说明 |
|------|--------|------|
| `src/core/phases/initialize.py` | Modify | merge-base 获取后立即运行检测，覆写 `state.merge_base_commit` |
| `src/tools/pollution_auditor.py` | — | 未单独修改；迁移检测后运行，自然覆盖边缘情况 |

### Phase 3: Reporting

| File | Action | 说明 |
|------|--------|------|
| `src/tools/merge_plan_report.py` | Modify | 新增 `_migration_section()` |
| `src/cli/commands/tui.py` | — | TUI 通知未单独实现；通过 `ctx.notify()` 日志可见 |

### Phase 4: Tests

| File | Action | 说明 |
|------|--------|------|
| `tests/unit/test_sync_point_detector.py` | **New** | 三阶段算法单元测试 |
| `tests/unit/test_migration_config.py` | **New** | 配置验证测试 |

### 超出原设计的实现项

| 项目 | 原设计 | 实际实现 |
|------|--------|---------|
| 模糊文件处理 | 无（未提及） | **Phase 1b Patch-ID 验证**：对所有三 hash 均不同的文件比对 patch-ID，检测带微调的 copy |
| commit 边界搜索 | 线性扫描 | **>50 commits 时自动切换二分搜索**，复杂度从 O(n) 降至 O(log n) |
| `SyncPointResult` 字段 | 9 个字段 | 新增 `patch_id_promoted_count`（Phase 1b 升级的文件数） |
| `SyncPointDetector` 参数 | 类常量 | 改为 `__init__` 参数（`sync_ratio_threshold`, `min_synced_files`, `enable_patch_id`），可按调用方配置 |
| 对 ambiguous 文件的追踪 | 无 | `_file_level_detection` 返回三元组，额外输出 `ambiguous_files` 集合供 Phase 1b 消费 |

---

## 5. Config Examples

### 5.1 Automatic detection (default)

```yaml
upstream_ref: "upstream/main"
fork_ref: "origin/cvte-main"

# No migration config needed — auto-detection is on by default
```

### 5.2 Explicit merge-base override

```yaml
upstream_ref: "upstream/main"
fork_ref: "origin/cvte-main"

migration:
  merge_base_override: "abc1234def5678"  # Last migrated upstream commit
```

### 5.3 Disable auto-detection

```yaml
upstream_ref: "upstream/main"
fork_ref: "origin/cvte-main"

migration:
  auto_detect_sync_point: false
```

### 5.4 Adjust detection sensitivity

```yaml
migration:
  sync_detection_threshold: 0.15  # Trigger at 15% synced (more sensitive)
  min_synced_files: 3             # Lower minimum (default 5)
```

---

## 6. Edge Cases

### 6.1 Partial Migration
Only some files were copied from upstream. The commit-level detection may not find a clean boundary.

**Mitigation**: The `sync_ratio` and `confidence` fields indicate detection quality. If confidence is low (< 0.5), the system should log a warning and recommend manual `merge_base_override`.

### 6.2 Multiple Migrations
The fork was synced multiple times via code copy at different points.

**Mitigation**: The algorithm finds the **most recent** sync point, which is the correct one for determining what still needs to be merged.

### 6.3 No Shared History
The fork was created by copying files into a new repo (`git init`), with no shared git history at all. `git merge-base` fails.

**Mitigation**: `GitTool.get_merge_base()` already falls back to `rev-parse upstream_ref`. The sync-point detector will then check all upstream commits and find the effective boundary. If no boundary exists (no files match), it falls back to the original merge-base.

### 6.4 Cherry-Pick Failures After Adjusted Merge-Base
Even with the correct merge-base, some cherry-picks may fail due to intermediate dependencies.

**Mitigation**: The existing `CommitReplayer` already handles this — failed cherry-picks are aborted and fall back to `apply_with_snapshot` + new commit.

### 6.5 Merge-Base Override Points to Wrong Commit
User provides an incorrect commit SHA.

**Mitigation**: Validate that the commit exists and is an ancestor of `upstream_ref`. Log a warning if the override produces more Category C files than expected (compared to auto-detection).

---

## 7. Interaction with Existing Systems

### 7.1 PollutionAuditor
The sync-point detection **replaces** much of the PollutionAuditor's purpose for migration scenarios. However, the auditor still provides value for:
- Detecting pollution from non-migration sources (e.g., accidental cherry-picks)
- Reclassifying files that slipped through sync-point detection

**Recommendation**: Run PollutionAuditor AFTER sync-point adjustment. It will find fewer issues but still catches edge cases.

### 7.2 History Preservation (HistoryPreservationConfig)
The `history.enabled` and `history.cherry_pick_clean` settings work correctly with the adjusted merge-base:
- Cherry-pick replay only processes commits after the effective merge-base
- Phase commits only cover files that were actually changed in this merge run

### 7.3 LLM Risk Scoring
No interaction. Risk scoring operates on file diffs, which are computed after merge-base is finalized.

---

## 8. Verification Plan

### 8.1 Unit Tests
- `SyncPointDetector.detect()` with mock git data:
  - All commits synced → `detected=True`, `sync_ratio=1.0`
  - No commits synced → `detected=False`
  - Partial sync (50%) → `detected=True`, correct boundary
  - Empty upstream → `detected=False`
- `MigrationConfig` validation:
  - Valid commit SHA override
  - Threshold range validation

### 8.2 Integration Test
Create a test repository:
1. Initialize upstream repo with 10 commits
2. Create fork by copying files (not git fork)
3. Copy files from upstream commits 1-5 into fork
4. Run CodeMergeSystem with auto-detection
5. Verify: only commits 6-10 are processed
6. Verify: `git log` shows cherry-picked commits 6-10

### 8.3 Regression Test
- `migration.auto_detect_sync_point = false` → behavior identical to before
- `migration.merge_base_override = None` + no migration → uses git merge-base as before
- Existing unit tests continue to pass (1133 tests)
