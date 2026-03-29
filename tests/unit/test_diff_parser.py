from src.tools.diff_parser import parse_unified_diff, parse_conflict_markers


def test_parse_conflict_markers():
    content = """\
def hello():
<<<<<<< HEAD
    print("hello from fork")
=======
    print("hello from upstream")
>>>>>>> upstream/main
    return True
"""
    hunks = parse_conflict_markers(content)
    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.has_conflict is True
    assert "hello from fork" in hunk.content_current
    assert "hello from upstream" in hunk.content_target
    assert len(hunk.conflict_marker_lines) >= 3


def test_three_way_diff_extraction():
    unified_diff = """\
--- a/src/module.py
+++ b/src/module.py
@@ -1,5 +1,6 @@
 def existing_function():
-    return "old"
+    return "new"
+    # added line

 def another():
     pass
"""
    hunks = parse_unified_diff(unified_diff, "src/module.py")
    assert len(hunks) >= 1
    first = hunks[0]
    assert first.start_line_target >= 1
    assert first.start_line_current >= 1
    assert "new" in first.content_current or "old" in first.content_target
