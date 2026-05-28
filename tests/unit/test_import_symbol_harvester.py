"""PR-D-B.1: import symbol harvester for analyst prompt context.

Without seeing the actual exports of imported modules, the LLM in
conflict_analyst is forced to pattern-complete symbol names — that is
how the zod ``core._isoWeek`` fabrication slipped through. This
harvester gives the analyst prompt a concrete list of what each
namespace import actually exposes, so the model knows whether a
symmetric name exists before recommending it.

Scope (kept small intentionally):

- Only ``import * as <name> from "<path>"`` (namespace imports).
  Named / default imports rarely produce qualified-ref fabrication
  (the failure mode this targets), so they are out of scope.
- TypeScript / JavaScript only — extension routing happens at the
  resolver layer; this function works on string content.
- Resolution is delegated via a ``resolver`` callback so the harvester
  itself has no git / filesystem dependency (testable in isolation).
"""

from __future__ import annotations

from src.tools.import_symbol_harvester import harvest_imported_symbols


class TestHarvestImportedSymbols:
    def test_namespace_import_lists_exports(self) -> None:
        source = 'import * as core from "../core/api.js";\n'
        module = (
            "export function _isoDateTime() {}\n"
            "export function _isoDate() {}\n"
            "export const _e164 = /xxx/;\n"
            "export class ZodFoo {}\n"
            "function _private() {}\n"  # NOT exported
        )
        result = harvest_imported_symbols(
            source, lambda p: module if p == "../core/api.js" else None
        )
        assert result == {
            "../core/api.js": ["_isoDateTime", "_isoDate", "_e164", "ZodFoo"],
        }

    def test_multiple_namespace_imports(self) -> None:
        source = (
            'import * as core from "../core/api.js";\n'
            'import * as iso from "./iso.js";\n'
        )
        modules = {
            "../core/api.js": "export function _isoDateTime() {}\n",
            "./iso.js": "export function datetime() {}\n",
        }
        result = harvest_imported_symbols(source, modules.get)
        assert result == {
            "../core/api.js": ["_isoDateTime"],
            "./iso.js": ["datetime"],
        }

    def test_resolver_returns_none_drops_module(self) -> None:
        # Cross-package / unresolvable imports are silently skipped —
        # the harvester is best-effort context, never a hard dep.
        source = 'import * as ext from "external-pkg";\n'
        result = harvest_imported_symbols(source, lambda p: None)
        assert result == {}

    def test_named_and_default_imports_ignored(self) -> None:
        # Out of scope: only namespace imports produce the qualified
        # base.member references where fabrication happens.
        source = (
            'import { foo, bar } from "./utils.js";\n'
            'import defaultThing from "./mod.js";\n'
        )
        result = harvest_imported_symbols(source, lambda p: "export const x = 1;")
        assert result == {}

    def test_export_types_all_picked_up(self) -> None:
        source = 'import * as M from "./mod.js";\n'
        module = (
            "export function fn() {}\n"
            "export const cn = 1;\n"
            "export let ln = 1;\n"
            "export var vn = 1;\n"
            "export class Cls {}\n"
            "export interface Iface {}\n"
            "export type T = number;\n"
            "export enum E { A }\n"
            "export async function af() {}\n"
        )
        result = harvest_imported_symbols(source, lambda p: module)
        assert result == {
            "./mod.js": ["fn", "cn", "ln", "vn", "Cls", "Iface", "T", "E", "af"],
        }

    def test_empty_source_returns_empty(self) -> None:
        assert harvest_imported_symbols("", lambda p: "anything") == {}

    def test_no_imports_returns_empty(self) -> None:
        source = "const x = 1;\nexport const y = 2;\n"
        assert harvest_imported_symbols(source, lambda p: "exports") == {}

    def test_module_without_exports_returns_empty_list(self) -> None:
        # Resolver provides the module but it exposes nothing public —
        # still record the import (caller may want to know it was
        # resolved successfully even if barren).
        source = 'import * as M from "./empty.js";\n'
        result = harvest_imported_symbols(
            source, lambda p: "// only comments\nconst priv = 1;\n"
        )
        assert result == {"./empty.js": []}
