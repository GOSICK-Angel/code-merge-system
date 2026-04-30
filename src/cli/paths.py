"""Path resolution for dev mode vs production mode.

Dev mode  — running CodeMergeSystem against its OWN source tree (or MERGE_DEV=1):
  checkpoint  ./outputs/debug/checkpoints/
  reports     ./outputs/
  plans       <repo>/MERGE_RECORD/
  logs        ./outputs/debug/

Production mode — running against any other repo (pip-installed or editable):
  checkpoint  <repo>/.merge/runs/<run_id>/
  reports     <repo>/.merge/runs/<run_id>/
  plans       <repo>/.merge/plans/
  logs        ~/.local/share/code-merge-system/logs/  (via platformdirs)
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import platformdirs as _pd

    _HAS_PLATFORMDIRS = True
except ImportError:
    _HAS_PLATFORMDIRS = False


def is_dev_mode(repo_path: str | None = None) -> bool:
    """True when running CodeMergeSystem against its OWN source tree.

    Editable installs (``pip install -e``) keep the package's pyproject.toml
    visible to the running interpreter. Without ``repo_path``, that wrongly
    flagged every run as dev — so when ``repo_path`` is supplied, the
    function additionally requires the *target* repo to match the package
    source root. Running against any other repo therefore returns False
    (prod mode), which sends artifacts into ``.merge/plans/`` as documented.
    """
    if os.environ.get("MERGE_DEV") == "1":
        return True
    pkg_root = Path(__file__).resolve().parents[2]
    pyproject = pkg_root / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        if "code-merge-system" not in pyproject.read_text(encoding="utf-8"):
            return False
    except OSError:
        return False
    if repo_path is None:
        return True
    try:
        return Path(repo_path).resolve() == pkg_root
    except OSError:
        return False


def get_project_merge_dir(repo_path: str = ".") -> Path:
    """<repo>/.merge/ — root of all merge artifacts in production mode."""
    return Path(repo_path).resolve() / ".merge"


def get_config_path(repo_path: str = ".") -> Path:
    """<repo>/.merge/config.yaml"""
    return get_project_merge_dir(repo_path) / "config.yaml"


def get_run_dir(repo_path: str = ".", run_id: str = "") -> Path:
    """Per-run directory used for checkpoint.json.

    Dev mode:  <repo>/outputs/debug/checkpoints/
    Prod mode: <repo>/.merge/runs/<run_id>/
    """
    if is_dev_mode(repo_path):
        return Path(repo_path).resolve() / "outputs" / "debug" / "checkpoints"
    return get_project_merge_dir(repo_path) / "runs" / run_id


def get_report_dir(
    repo_path: str = ".", run_id: str = "", fallback_dir: str = "./outputs"
) -> Path:
    """Directory for merge reports (merge_report, living_plan, plan_review).

    Dev mode:  fallback_dir (usually config.output.directory = ./outputs/)
    Prod mode: <repo>/.merge/runs/<run_id>/
    """
    if is_dev_mode(repo_path):
        return Path(fallback_dir)
    return get_project_merge_dir(repo_path) / "runs" / run_id


def get_plans_dir(repo_path: str = ".") -> Path:
    """Directory for MERGE_PLAN_*.md reports.

    Dev mode:  <repo>/MERGE_RECORD/
    Prod mode: <repo>/.merge/plans/
    """
    if is_dev_mode(repo_path):
        return Path(repo_path).resolve() / "MERGE_RECORD"
    return get_project_merge_dir(repo_path) / "plans"


def get_project_memory_db_path(repo_path: str = ".") -> Path:
    """Project-level shared memory.db path.

    Dev mode:  <repo>/outputs/debug/memory.db
    Prod mode: <repo>/.merge/memory.db
    """
    if is_dev_mode(repo_path):
        return Path(repo_path).resolve() / "outputs" / "debug" / "memory.db"
    return get_project_merge_dir(repo_path) / "memory.db"


def get_project_hit_stats_path(repo_path: str = ".") -> Path:
    """Project-level shared MemoryHitTracker sidecar JSON.

    Dev mode:  <repo>/outputs/debug/memory_hit_stats.json
    Prod mode: <repo>/.merge/memory_hit_stats.json
    """
    if is_dev_mode(repo_path):
        return Path(repo_path).resolve() / "outputs" / "debug" / "memory_hit_stats.json"
    return get_project_merge_dir(repo_path) / "memory_hit_stats.json"


def get_system_log_dir(repo_path: str = ".") -> Path:
    """Directory for run logs and LLM traces.

    Dev mode:  <repo>/outputs/debug/
    Prod mode: ~/.local/share/code-merge-system/logs/  (XDG/platformdirs)
    """
    if is_dev_mode(repo_path):
        return Path(repo_path).resolve() / "outputs" / "debug"
    if _HAS_PLATFORMDIRS:
        return Path(_pd.user_data_dir("code-merge-system")) / "logs"
    return Path("~/.local/share/code-merge-system/logs").expanduser()


def get_global_env_path() -> Path:
    """Global .env fallback: ~/.config/code-merge-system/.env"""
    if _HAS_PLATFORMDIRS:
        return Path(_pd.user_config_dir("code-merge-system")) / ".env"
    return Path("~/.config/code-merge-system/.env").expanduser()


def ensure_merge_dir(repo_path: str = ".") -> Path:
    """Create <repo>/.merge/ and write .gitignore if missing.  Returns the .merge/ Path."""
    merge_dir = get_project_merge_dir(repo_path)
    merge_dir.mkdir(parents=True, exist_ok=True)
    gitignore = merge_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Auto-generated by code-merge-system\n.env\nruns/\n",
            encoding="utf-8",
        )
    return merge_dir
