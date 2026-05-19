from src.core.phases.conflict_analysis import _analyze_round_with_bisect
from src.models.conflict import ConflictAnalysis, ConflictType
from src.models.decision import MergeDecision


def _ca(fp: str) -> ConflictAnalysis:
    return ConflictAnalysis(
        file_path=fp,
        conflict_points=[],
        overall_confidence=0.9,
        recommended_strategy=MergeDecision.TAKE_TARGET,
        conflict_type=ConflictType.SEMANTIC_EQUIVALENT,
        rationale="ok",
        confidence=0.9,
    )


class _FakeAnalyst:
    def __init__(self, fail_when: callable):
        self.fail_when = fail_when
        self.call_count = 0

    async def analyze_commit_round(
        self,
        round_commits,
        round_llm_files,
        file_languages,
        project_context: str = "",
        per_file_instructions=None,
    ):
        self.call_count += 1
        if self.fail_when(round_commits):
            return {}
        return {fp: _ca(fp) for fp in round_llm_files}


def _commit(sha: str, files: list[str]) -> dict:
    return {"sha": sha, "files": files}


async def test_bisect_recovers_when_halves_succeed():
    commits = [_commit(f"c{i}", [f"f{i}.py"]) for i in range(4)]
    files = {f"f{i}.py": (None, None, None) for i in range(4)}
    langs = {f"f{i}.py": "py" for i in range(4)}

    # full round fails (4 commits); any half succeeds
    analyst = _FakeAnalyst(fail_when=lambda c: len(c) >= 4)
    out = await _analyze_round_with_bisect(
        analyst, commits, files, langs, project_context=""
    )
    assert set(out) == set(files)
    assert analyst.call_count == 3  # 1 fail + 2 halves


async def test_bisect_single_commit_no_split():
    commits = [_commit("solo", ["x.py"])]
    files = {"x.py": (None, None, None)}
    langs = {"x.py": "py"}
    analyst = _FakeAnalyst(fail_when=lambda c: True)
    out = await _analyze_round_with_bisect(
        analyst, commits, files, langs, project_context=""
    )
    assert out == {}
    assert analyst.call_count == 1  # no bisect, single commit


async def test_bisect_respects_max_depth():
    # always-failing analyst — bisect should stop at max_depth=1
    commits = [_commit(f"c{i}", [f"f{i}.py"]) for i in range(4)]
    files = {f"f{i}.py": (None, None, None) for i in range(4)}
    langs = {f"f{i}.py": "py" for i in range(4)}
    analyst = _FakeAnalyst(fail_when=lambda c: True)
    out = await _analyze_round_with_bisect(
        analyst, commits, files, langs, project_context="", max_depth=1
    )
    assert out == {}
    # depth-1: 1 (root) + 2 (halves) = 3 calls total, no further bisect
    assert analyst.call_count == 3
