# Open-Source Comparison & Enhancement Plan

## 1. Research Scope

Surveyed 15+ open-source projects related to git merge automation, fork synchronization,
migration detection, and AI-powered conflict resolution. This document summarizes findings
and identifies concrete improvements for CodeMergeSystem.

---

## 2. Related Projects by Category

### 2.1 Git Built-in Capabilities

| Tool | Mechanism | Relevance |
|------|-----------|-----------|
| `git cherry` | Compares patch-IDs (diff content hashes) to find commits already applied under different SHAs | High — can detect "silent cherry-picks" that file-hash comparison misses |
| `git merge-base --fork-point` | Uses reflog of remote-tracking branches to find true fork point | Low — requires reflog, fails after `gc`, useless for code-copy migrations |
| `git log --cherry-pick` | Filters symmetric diff to exclude patch-equivalent commits | Medium — useful for commit replay deduplication |

**Key insight**: `git cherry` uses **patch-ID** (hash of the diff content, ignoring commit metadata)
rather than blob hashes. This is more robust for detecting partial sync where the fork has
slight modifications on top of migrated code.

### 2.2 Branch Workflow Tools

| Project | Stars | Key Feature | Gap vs Our System |
|---------|-------|-------------|-------------------|
| [git-machete](https://github.com/VirtusLab/git-machete) | 1,101 | Fork-point algorithm with reflog + topology analysis; `--override-to` manual override; squash merge detection | Reflog-based detection; no content-level sync detection; no LLM integration |
| [forks-sync](https://github.com/Justintime50/forks-sync) | 5 | Concurrent fork sync via rebase + force push | Brute-force sync, no migration awareness |
| [gh-stack](https://github.com/felixge/gh-stack) | — | Stacked PRs with commit-UID tracking; merge-base computation | Focused on PR workflow, not fork maintenance |

**Key insight**: git-machete's `fork-point --override-to` validates our `merge_base_override`
config design. Its dual detection strategy (`simple` vs `exact` for squash merges) inspires
our threshold-based approach.

### 2.3 Semantic Merge Tools

| Project | Stars | Approach |
|---------|-------|----------|
| [Weave](https://github.com/ataraxy-labs/weave) | 808 | tree-sitter entity-level merge (function/class granularity); 31/31 clean merges vs git's 15/31 |
| [merge-engine](https://docs.rs/merge-engine/) | Rust crate | 4-layer pipeline: Pattern DSL → CST structured merge → Version Space Algebra → Genetic search |
| [Mergiraf](https://mergiraf.org/) | — | AST-level structured merge; 26/31 clean merges |

**Key insight**: merge-engine's 4-layer strategy is directly applicable to our executor phase.
By attempting rule-based resolution before LLM calls, we can reduce LLM cost significantly
for common conflict patterns (whitespace, import ordering, identical changes).

### 2.4 AI-Powered Conflict Resolution

| Project | Stars | Provider | Approach |
|---------|-------|----------|----------|
| [mergefix](https://pypi.org/project/mergefix/) | PyPI | Claude/OpenAI/Ollama | Parse conflict markers → LLM with full file context |
| [reconcile-ai](https://github.com/kailashchanel/reconcile-ai) | 9 | OpenAI | Batch conflict resolution (up to 80% cost reduction) |
| [pipekit/cherry-picker](https://github.com/pipekit/cherry-picker) | 1 | cursor-agent | Cherry-pick + interactive AI conflict resolution |
| [clash](https://github.com/clash-sh/clash) | 49 | — | Worktree conflict detection for parallel AI agents (read-only) |

**Key insight**: These tools operate **post-conflict** (after merge fails). Our system operates
**pre-conflict** (adjusting merge-base to prevent false conflicts). The two approaches are
complementary, not competing.

---

## 3. Comparison Matrix

| Dimension | CodeMergeSystem (ours) | git cherry | git-machete | merge-engine | mergefix |
|-----------|----------------------|------------|-------------|--------------|----------|
| Detection method | File blob hash 3-way comparison | Patch-ID comparison | Reflog + topology | N/A | N/A |
| Target scenario | Bulk code copy (no git history) | Cherry-pick deduplication | Branch rebase management | Conflict resolution | Post-conflict LLM fix |
| Auto-detection | Yes, with confidence scoring | Manual range required | Yes, fork-point | N/A | N/A |
| Manual override | `merge_base_override` | N/A | `fork-point --override-to` | N/A | N/A |
| LLM integration | Full pipeline (planner → judge → executor) | None | None | None (rule-based only) | Single-shot LLM |
| Handles code-copy migration | Yes | Partially (via patch-ID) | No (needs reflog) | No | No |
| Cost optimization | Per-agent model routing | N/A | N/A | 4-layer rule-first pipeline | Batch processing |
| Risk scoring | Weighted multi-factor + optional LLM | None | None | Confidence per strategy | None |

---

## 4. Enhancement Plan

Three concrete improvements derived from this research, ordered by impact.

### 4.1 Patch-ID Auxiliary Detection

**Source**: `git cherry` / `git log --cherry-pick`

**Problem**: Current file-hash detection fails when the fork made slight modifications on top
of migrated code (fork_hash != up_hash, but the diff is nearly identical).

**Solution**: For files classified as "ambiguous" (both changed, Category C), compute
patch-IDs for upstream commits and compare against fork-side diffs. If patch-IDs match,
the file was migrated with minor tweaks.

**Implementation**: Add `_patch_id_verification()` to `SyncPointDetector` that calls
`git patch-id` on commit diffs to detect near-matches.

**Impact**: Improved detection accuracy for partial migrations; higher confidence scores.

### 4.2 Rule-Based Pre-Resolution

**Source**: merge-engine (Pattern DSL + structured merge)

**Problem**: All Category C conflicts currently go to the LLM executor, even trivially
resolvable ones (whitespace-only, identical changes, import ordering).

**Solution**: Add a `RuleBasedResolver` that attempts deterministic resolution before
LLM invocation. Handles 4 common patterns:
1. **Whitespace-only** — normalize and compare
2. **Identical changes** — both sides made the same change, take either
3. **Import/include union** — merge import lists
4. **Adjacent edits** — non-overlapping changes in the same file

**Implementation**: New `src/tools/rule_resolver.py` invoked before executor agent.

**Impact**: Estimated 20-40% reduction in LLM calls for Category C files; faster execution.

### 4.3 Binary Search for Sync Boundary

**Source**: git-machete fork-point algorithm

**Problem**: Current commit boundary detection walks all commits linearly (O(n)).
For repos with 500+ upstream commits, this is slow.

**Solution**: Use binary search on the commit list. The invariant: commits before the
sync point have all files in the synced set; commits after do not. Find the transition
point in O(log n).

**Implementation**: Replace linear walk in `_commit_boundary_detection()` with
binary search. Fall back to linear when boundary is non-contiguous.

**Impact**: ~10x speedup for large repos (500+ commits → ~9 iterations vs 500).

---

## 5. Priority & Effort

| Enhancement | Priority | Effort | Files Changed |
|-------------|----------|--------|---------------|
| 4.1 Patch-ID Detection | High | ~80 lines | `sync_point_detector.py`, `git_tool.py` |
| 4.2 Rule-Based Resolution | Medium | ~120 lines | New `rule_resolver.py`, `conflict_analysis.py` |
| 4.3 Binary Search | Medium | ~40 lines | `sync_point_detector.py` |
