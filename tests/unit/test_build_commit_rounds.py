from src.core.phases.conflict_analysis import build_commit_rounds


def _commit(sha: str, files: list[str]) -> dict:
    return {"sha": sha, "files": files}


def test_round_size_only_packs_up_to_limit():
    commits = [_commit(f"c{i}", [f"f{i}.py"]) for i in range(11)]
    rounds = build_commit_rounds(commits, round_size=5)
    assert [len(r) for r in rounds] == [5, 5, 1]


def test_file_overlap_splits_round():
    commits = [
        _commit("c1", ["shared.py"]),
        _commit("c2", ["shared.py"]),
        _commit("c3", ["other.py"]),
    ]
    rounds = build_commit_rounds(commits, round_size=5)
    assert [len(r) for r in rounds] == [1, 2]


def test_file_cap_splits_round_before_count_cap():
    # 3 commits, each adding 30 files; cap at 60 files closes the round
    # after 2 commits even though count cap is 5.
    commits = [_commit(f"c{i}", [f"c{i}_f{j}.py" for j in range(30)]) for i in range(3)]
    rounds = build_commit_rounds(commits, round_size=5, max_files_per_round=60)
    assert [len(r) for r in rounds] == [2, 1]


def test_token_cap_splits_round_before_file_cap():
    # 5 commits, 20 files each = 100 files total. token_cap=50_000 (~50 files
    # at 1000 tokens/file) closes after the 2nd commit (40 files) since the
    # 3rd would project to 60 files * 1000 = 60_000 > 50_000.
    commits = [_commit(f"c{i}", [f"c{i}_f{j}.py" for j in range(20)]) for i in range(5)]
    rounds = build_commit_rounds(
        commits,
        round_size=5,
        max_files_per_round=500,
        max_est_tokens_per_round=50_000,
    )
    assert [len(r) for r in rounds] == [2, 2, 1]


def test_caps_dont_split_single_commit_below_limit():
    # Even a fat single commit must not be split into zero-commit rounds.
    fat_commit = _commit("big", [f"f{j}.py" for j in range(200)])
    rounds = build_commit_rounds([fat_commit], round_size=5, max_files_per_round=60)
    assert rounds == [[fat_commit]]
