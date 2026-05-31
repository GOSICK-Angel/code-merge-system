"""Module inference: explicit > forks-profile > topology precedence,
container-dir handling, top-level fallback, and the off/config regimes."""

from __future__ import annotations

from src.models.config import ModuleConfig
from src.tools.module_inference import DEFAULT_MODULE, infer_modules


def test_container_dir_child_names_the_module() -> None:
    cfg = ModuleConfig()  # auto, default container_dirs include "packages"
    out = infer_modules(["packages/ui/src/Button.tsx"], cfg)
    assert out["packages/ui/src/Button.tsx"] == "ui"


def test_file_directly_in_container_falls_to_top_level() -> None:
    cfg = ModuleConfig()
    # src is a container dir, but src/main.py has no sub-package → module
    # is the container itself, not the filename.
    out = infer_modules(["src/main.py"], cfg)
    assert out["src/main.py"] == "src"


def test_top_level_dir_used_when_no_container() -> None:
    cfg = ModuleConfig()
    out = infer_modules(["api/handler.py"], cfg)
    assert out["api/handler.py"] == "api"


def test_root_level_file_is_default() -> None:
    cfg = ModuleConfig()
    out = infer_modules(["README.md"], cfg)
    assert out["README.md"] == DEFAULT_MODULE


def test_explicit_glob_overrides_topology() -> None:
    cfg = ModuleConfig(explicit={"api/auth/**": "auth-service"})
    out = infer_modules(["api/auth/login.py", "api/orders/o.py"], cfg)
    assert out["api/auth/login.py"] == "auth-service"
    assert out["api/orders/o.py"] == "api"  # unmatched → topology


def test_rewritten_module_path_used_when_no_explicit() -> None:
    cfg = ModuleConfig()
    out = infer_modules(
        ["plugins/llm/foo.py"],
        cfg,
        rewritten_module_paths=["plugins/llm/**"],
    )
    assert out["plugins/llm/foo.py"] == "plugins/llm"


def test_explicit_beats_rewritten() -> None:
    cfg = ModuleConfig(explicit={"plugins/llm/**": "llm-pkg"})
    out = infer_modules(
        ["plugins/llm/foo.py"],
        cfg,
        rewritten_module_paths=["plugins/llm/**"],
    )
    assert out["plugins/llm/foo.py"] == "llm-pkg"


def test_wildcard_free_rewritten_path_matches_as_prefix() -> None:
    cfg = ModuleConfig()
    out = infer_modules(
        ["api/auth/login.py", "api/auth.py"],
        cfg,
        rewritten_module_paths=["api/auth"],
    )
    assert out["api/auth/login.py"] == "api/auth"
    # exact prefix file also matches
    assert out["api/auth.py"] == "api"  # not under api/auth/ → topology


def test_off_mode_collapses_to_single_module() -> None:
    cfg = ModuleConfig(mode="off")
    out = infer_modules(["packages/ui/x.tsx", "api/h.py"], cfg)
    assert set(out.values()) == {DEFAULT_MODULE}


def test_config_mode_skips_topology() -> None:
    cfg = ModuleConfig(mode="config", explicit={"api/auth/**": "auth"})
    out = infer_modules(["api/auth/login.py", "api/orders/o.py"], cfg)
    assert out["api/auth/login.py"] == "auth"
    # config mode does NOT fall back to topology — unmatched → default
    assert out["api/orders/o.py"] == DEFAULT_MODULE
