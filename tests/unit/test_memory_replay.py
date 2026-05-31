"""PR-0a: offline memory ablation harness + inject_enabled switch tests."""

import json

import pytest

from src.models.config import MemoryExtractionConfig
from src.models.memory_effectiveness import MemoryEffectivenessReport
from src.tools.memory_replay import (
    REPORT_FILENAME,
    build_ablation_comparison,
    load_effectiveness_report,
    render_ablation_table,
)


def _report(run_id: str, correct_rate: float, harmful_rate: float = 0.0):
    return MemoryEffectivenessReport(
        run_id=run_id,
        total_judged_decisions=10,
        overall_correct_rate=correct_rate,
        memory_influenced_decisions=4,
        correct_after_influence=3,
        harmful_influence_count=1,
        correct_rate_after_influence=0.75,
        harmful_influence_rate=harmful_rate,
        total_tracked_entries=2,
        effective_observations=4,
    )


# --- loading ----------------------------------------------------------------


def test_load_from_json_file(tmp_path):
    report = _report("run-on", 0.9)
    p = tmp_path / REPORT_FILENAME
    p.write_text(report.model_dump_json(), encoding="utf-8")
    loaded = load_effectiveness_report(p)
    assert loaded == report


def test_load_from_run_directory(tmp_path):
    report = _report("run-off", 0.7)
    (tmp_path / REPORT_FILENAME).write_text(report.model_dump_json(), encoding="utf-8")
    loaded = load_effectiveness_report(tmp_path)
    assert loaded.run_id == "run-off"


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_effectiveness_report(tmp_path / "nope.json")


def test_load_dir_without_report_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match=REPORT_FILENAME):
        load_effectiveness_report(tmp_path)


def test_load_rejects_malformed_json(tmp_path):
    p = tmp_path / REPORT_FILENAME
    p.write_text(json.dumps({"run_id": "x"}), encoding="utf-8")  # missing fields
    with pytest.raises(Exception):
        load_effectiveness_report(p)


# --- comparison -------------------------------------------------------------


def test_comparison_positive_lift():
    cmp = build_ablation_comparison(_report("on", 0.9), _report("off", 0.7))
    assert cmp.memory_decision_lift == pytest.approx(0.2)
    assert cmp.memory_beneficial is True
    assert cmp.on_run_id == "on"
    assert cmp.off_run_id == "off"


def test_comparison_non_positive_lift_not_beneficial():
    cmp = build_ablation_comparison(_report("on", 0.7), _report("off", 0.7))
    assert cmp.memory_decision_lift == pytest.approx(0.0)
    assert cmp.memory_beneficial is False


# --- rendering --------------------------------------------------------------


def test_render_table_contains_key_figures():
    cmp = build_ablation_comparison(_report("on", 0.9, 0.25), _report("off", 0.7))
    table = render_ablation_table(cmp)
    assert "memory_decision_lift" in table
    assert "BENEFICIAL" in table
    assert "25.00%" in table  # harmful_influence_rate_on
    assert "`on`" in table and "`off`" in table


def test_render_table_negative_lift():
    cmp = build_ablation_comparison(_report("on", 0.6), _report("off", 0.8))
    table = render_ablation_table(cmp)
    assert "NOT beneficial" in table
    assert "-" in table  # negative lift rendered with sign


# --- inject_enabled ablation switch -----------------------------------------


def test_inject_enabled_defaults_true():
    assert MemoryExtractionConfig().inject_enabled is True


def test_inject_disabled_skips_store_wiring():
    """When inject_enabled is False, _inject_memory must leave each agent's
    store at None so get_memory_context() returns empty (the memory=off arm)."""

    class _Agent:
        def __init__(self):
            self.store = "UNSET"
            self.tracker = None
            self.cfg = None
            self.upstream = None

        def set_memory_store(self, store):
            self.store = store

        def set_memory_hit_tracker(self, tracker):
            self.tracker = tracker

        def set_memory_config(self, cfg):
            self.cfg = cfg

        def set_upstream_ref(self, ref):
            self.upstream = ref

    class _Cfg:
        memory = MemoryExtractionConfig(inject_enabled=False)
        upstream_ref = "upstream/main"

    class _Orch:
        config = _Cfg()
        _memory_store = object()
        _memory_hit_tracker = object()

        def __init__(self):
            self._all_agents = [_Agent()]

    from src.core.orchestrator import Orchestrator

    orch = _Orch()
    Orchestrator._inject_memory(orch)  # type: ignore[arg-type]
    agent = orch._all_agents[0]
    # store-wiring skipped → stays at the sentinel "UNSET" (never set to None
    # either, but crucially never set to the real store)
    assert agent.store == "UNSET"
    assert agent.tracker is orch._memory_hit_tracker
    assert agent.cfg is orch.config.memory


def test_inject_enabled_wires_store():
    class _Agent:
        store = None
        tracker = None
        cfg = None
        upstream = None

        def set_memory_store(self, store):
            self.store = store

        def set_memory_hit_tracker(self, tracker):
            self.tracker = tracker

        def set_memory_config(self, cfg):
            self.cfg = cfg

        def set_upstream_ref(self, ref):
            self.upstream = ref

    class _Cfg:
        memory = MemoryExtractionConfig(inject_enabled=True)
        upstream_ref = "upstream/main"

    class _Orch:
        config = _Cfg()
        _memory_store = object()
        _memory_hit_tracker = object()

        def __init__(self):
            self._all_agents = [_Agent()]

    from src.core.orchestrator import Orchestrator

    orch = _Orch()
    Orchestrator._inject_memory(orch)  # type: ignore[arg-type]
    assert orch._all_agents[0].store is orch._memory_store
