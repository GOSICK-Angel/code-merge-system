"""PR-D-B.5a: ``harvest_imports_for_file`` — path resolution layer.

The pure helper (PR-D-B.1) consumes a resolver callback; this layer
turns ``(source_path, source_content, ref, git_tool)`` into a resolver
that handles the practical mess of TS / JS import paths: the canonical
zod pattern ``import * as core from "../core/api.js"`` resolves to
``packages/zod/src/v4/core/api.ts`` on disk (different extension), and
``./mod`` may be a file or an ``index.ts`` inside a folder.

Best-effort: any failure produces a missing entry rather than an
exception, so the analyst flow degrades to "no surface block" instead
of crashing.
"""

from __future__ import annotations

from src.tools.import_symbol_harvester import harvest_imports_for_file


class _FakeGitTool:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.calls: list[tuple[str, str]] = []

    def get_file_content(self, ref: str, path: str) -> str | None:
        self.calls.append((ref, path))
        return self.files.get(path)


class TestHarvestImportsForFile:
    def test_resolves_relative_js_to_ts_extension(self) -> None:
        # zod-shape: ``../core/api.js`` in source actually lives at
        # ``packages/zod/src/v4/core/api.ts`` in the repo.
        git = _FakeGitTool(
            {
                "packages/zod/src/v4/core/api.ts": (
                    "export function _isoDateTime() {}\nexport function _isoDate() {}\n"
                ),
            }
        )
        result = harvest_imports_for_file(
            source_path="packages/zod/src/v4/classic/schemas.ts",
            source_content='import * as core from "../core/api.js";\n',
            ref="test/fork",
            git_tool=git,
        )
        assert result == {
            "../core/api.js": ["_isoDateTime", "_isoDate"],
        }

    def test_resolves_to_same_extension(self) -> None:
        # Source already uses .ts extension — passes through.
        git = _FakeGitTool({"packages/foo/bar.ts": "export const X = 1;\n"})
        result = harvest_imports_for_file(
            source_path="packages/foo/main.ts",
            source_content='import * as M from "./bar.ts";\n',
            ref="test/fork",
            git_tool=git,
        )
        assert result == {"./bar.ts": ["X"]}

    def test_resolves_extensionless_to_ts(self) -> None:
        # Bare relative paths ``./bar`` (no extension): try .ts first.
        git = _FakeGitTool({"packages/foo/bar.ts": "export const X = 1;\n"})
        result = harvest_imports_for_file(
            source_path="packages/foo/main.ts",
            source_content='import * as M from "./bar";\n',
            ref="test/fork",
            git_tool=git,
        )
        assert result == {"./bar": ["X"]}

    def test_unresolvable_path_skipped(self) -> None:
        # Bare-module imports (no leading ./ or ../) like
        # ``import * as zod from "zod"`` cannot be resolved within
        # the repo — silently dropped, not an error.
        git = _FakeGitTool({})
        result = harvest_imports_for_file(
            source_path="src/main.ts",
            source_content='import * as Z from "zod";\n',
            ref="test/fork",
            git_tool=git,
        )
        assert result == {}

    def test_git_tool_returns_none_for_all_candidates_dropped(self) -> None:
        git = _FakeGitTool({})
        result = harvest_imports_for_file(
            source_path="src/main.ts",
            source_content='import * as M from "./missing.js";\n',
            ref="test/fork",
            git_tool=git,
        )
        assert result == {}

    def test_empty_source_returns_empty(self) -> None:
        git = _FakeGitTool({})
        assert (
            harvest_imports_for_file(
                source_path="x.ts",
                source_content="",
                ref="test/fork",
                git_tool=git,
            )
            == {}
        )

    def test_no_git_tool_returns_empty(self) -> None:
        # Safe degrade when the agent was constructed without git_tool.
        result = harvest_imports_for_file(
            source_path="x.ts",
            source_content='import * as M from "./bar.js";\n',
            ref="test/fork",
            git_tool=None,
        )
        assert result == {}
