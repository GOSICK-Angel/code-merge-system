"""Unit tests for the hallucinated cross-module member-access guard (方案3.2).

Mirrors the zod failure: a chunked merge invents ``core._isoWeek`` on a real
``core`` import. The guard flags a ``base.member`` ref only when it is absent
from both sources AND ``base.`` appears in a source (real object, fabricated
member), so legitimate recombination and brand-new imports are never flagged.
"""

from __future__ import annotations

from src.tools.hallucinated_symbol_guard import find_invented_member_accesses


class TestFindInventedMemberAccesses:
    def test_flags_fabricated_member_on_real_base(self):
        fork = "import * as core from './core';\nexport const a = core.foo();\n"
        upstream = "import * as core from './core';\nexport const b = core.bar();\n"
        merged = "import * as core from './core';\nexport const c = core._isoWeek();\n"
        invented = find_invented_member_accesses(merged, [fork, upstream], "x.ts")
        assert invented == ["core._isoWeek"]

    def test_recombined_reference_not_flagged(self):
        fork = "const a = util.format(x);\n"
        upstream = "const b = util.parse(y);\n"
        # Both references exist in a source → faithful recombination.
        merged = "const a = util.format(x);\nconst b = util.parse(y);\n"
        assert find_invented_member_accesses(merged, [fork, upstream], "x.ts") == []

    def test_brand_new_base_not_flagged(self):
        # `lodash` appears in no source → a new import the merge added; we do
        # not second-guess it (only fabricated members on existing bases).
        fork = "const a = 1;\n"
        upstream = "const b = 2;\n"
        merged = "import _ from 'lodash';\nconst c = lodash.map(x);\n"
        assert find_invented_member_accesses(merged, [fork, upstream], "x.ts") == []

    def test_non_code_extension_skipped(self):
        merged = "core._isoWeek\n"
        sources = ["core.foo\n"]
        assert find_invented_member_accesses(merged, sources, "data.json") == []

    def test_version_token_not_matched(self):
        # `1.2` must not match (base must start with a letter/_/$).
        merged = "const v = 1.2;\n"
        assert find_invented_member_accesses(merged, ["x = 1.0\n"], "x.ts") == []

    def test_python_member_access(self):
        fork = "import core\nx = core.foo()\n"
        upstream = "import core\ny = core.bar()\n"
        merged = "import core\nz = core.isoweek()\n"
        assert find_invented_member_accesses(merged, [fork, upstream], "m.py") == [
            "core.isoweek"
        ]

    def test_empty_merged_returns_empty(self):
        assert find_invented_member_accesses("", ["core.foo\n"], "x.ts") == []

    def test_results_sorted_and_capped(self):
        fork = "core.a; core.b;\n"
        merged = "core.z1; core.z2; core.z3; core.z4; core.z5; core.z6;\n"
        out = find_invented_member_accesses(merged, [fork], "x.ts", limit=3)
        assert out == ["core.z1", "core.z2", "core.z3"]
