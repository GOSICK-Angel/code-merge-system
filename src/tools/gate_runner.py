from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.models.config import GateCommandConfig
from src.tools.baseline_parsers import (
    BaselineSnapshot,
    diff_new_failures,
    empty_snapshot,
    get_parser,
)

logger = logging.getLogger(__name__)


class GateResult(BaseModel):
    gate_name: str
    passed: bool
    exit_code: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class GateReport(BaseModel):
    all_passed: bool
    results: list[GateResult] = Field(default_factory=list)
    baseline_comparison: dict[str, str] = Field(default_factory=dict)
    new_failures: dict[str, list[str]] = Field(
        default_factory=dict,
        description="P1-2: gate_name -> list of failed_ids newly introduced "
        "versus baseline (empty when no regression).",
    )


class GateRunner:
    def __init__(self, repo_path: Path):
        self.repo_path = repo_path

    async def run_gate(self, gate: GateCommandConfig) -> GateResult:
        work_dir = self.repo_path / gate.working_dir
        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_shell(
                gate.command,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=gate.timeout_seconds,
            )
            exit_code = proc.returncode if proc.returncode is not None else 0
            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            logger.warning(
                "Gate '%s' timed out after %ds", gate.name, gate.timeout_seconds
            )
            return GateResult(
                gate_name=gate.name,
                passed=False,
                exit_code=-1,
                stdout_tail="",
                stderr_tail=f"Timeout after {gate.timeout_seconds}s",
                duration_seconds=time.monotonic() - start,
            )
        except Exception as exc:
            logger.error("Gate '%s' execution error: %s", gate.name, exc)
            return GateResult(
                gate_name=gate.name,
                passed=False,
                exit_code=-2,
                stderr_tail=str(exc),
                duration_seconds=time.monotonic() - start,
            )

        duration = time.monotonic() - start
        stdout_lines = stdout_str.strip().splitlines()
        stderr_lines = stderr_str.strip().splitlines()

        passed = exit_code == 0

        logger.info(
            "Gate '%s': exit=%d passed=%s (%.1fs)",
            gate.name,
            exit_code,
            passed,
            duration,
        )

        return GateResult(
            gate_name=gate.name,
            passed=passed,
            exit_code=exit_code,
            stdout_tail="\n".join(stdout_lines[-20:]),
            stderr_tail="\n".join(stderr_lines[-20:]),
            duration_seconds=round(duration, 2),
        )

    async def run_all_gates(
        self,
        gates: list[GateCommandConfig],
        baselines: dict[str, str] | None = None,
    ) -> GateReport:
        results: list[GateResult] = []
        new_failures: dict[str, list[str]] = {}
        gate_by_name: dict[str, GateCommandConfig] = {g.name: g for g in gates}

        for gate in gates:
            result = await self.run_gate(gate)

            if (
                gate.pass_criteria == "not_worse_than_baseline"
                and baselines
                and not result.passed
            ):
                baseline_output = baselines.get(gate.name)
                if baseline_output is not None:
                    baseline_failed = _extract_failed_count(baseline_output)
                    current_failed = _extract_failed_count(result.stdout_tail)
                    if (
                        baseline_failed is not None
                        and current_failed is not None
                        and current_failed <= baseline_failed
                    ):
                        result = result.model_copy(update={"passed": True})
                        logger.info(
                            "Gate '%s' failed=%d <= baseline=%d, treating as pass",
                            gate.name,
                            current_failed,
                            baseline_failed,
                        )

            if gate.pass_criteria == "no_new_regression":
                result, regressions = self._apply_baseline_diff(
                    gate=gate,
                    result=result,
                    baselines=baselines or {},
                )
                if regressions:
                    new_failures[gate.name] = regressions

            results.append(result)

        comparison: dict[str, str] = {}
        if baselines:
            for result in results:
                baseline = baselines.get(result.gate_name)
                gate_cfg = gate_by_name.get(result.gate_name)
                if baseline is None and (
                    not gate_cfg or gate_cfg.pass_criteria != "no_new_regression"
                ):
                    comparison[result.gate_name] = "no_baseline"
                elif result.passed:
                    comparison[result.gate_name] = "passed"
                else:
                    comparison[result.gate_name] = "regressed"

        all_passed = all(r.passed for r in results) if results else True

        return GateReport(
            all_passed=all_passed,
            results=results,
            baseline_comparison=comparison,
            new_failures=new_failures,
        )

    def _apply_baseline_diff(
        self,
        gate: GateCommandConfig,
        result: GateResult,
        baselines: dict[str, str],
    ) -> tuple[GateResult, list[str]]:
        """Apply P1-2 ``no_new_regression`` semantics using a structured parser.

        Returns ``(possibly-updated result, list of newly-failed ids)``.
        A gate passes when the set of current failed_ids is a subset of the
        baseline — even if exit code is non-zero. Fails when at least one new
        failed_id appears, regardless of total count trend.
        """
        parser_name = gate.baseline_parser
        if not parser_name:
            return result, []

        parser = get_parser(parser_name)
        if parser is None:
            logger.warning(
                "Gate '%s' baseline_parser='%s' not registered", gate.name, parser_name
            )
            return result, []

        current_snapshot = parser(result.stdout_tail)

        baseline_raw = baselines.get(gate.name)
        baseline_snapshot: BaselineSnapshot = empty_snapshot()
        if baseline_raw:
            baseline_snapshot = self._parse_or_fallback(parser, baseline_raw)

        new_ids = diff_new_failures(baseline_snapshot, current_snapshot)

        if not new_ids:
            if not result.passed:
                logger.info(
                    "Gate '%s' no_new_regression: 0 new failed_ids, treating as pass",
                    gate.name,
                )
            return result.model_copy(update={"passed": True}), []

        updated = result.model_copy(update={"passed": False})
        logger.warning(
            "Gate '%s' no_new_regression: %d new failed_ids: %s",
            gate.name,
            len(new_ids),
            new_ids[:5],
        )
        return updated, new_ids

    @staticmethod
    def _parse_or_fallback(parser: Any, baseline_raw: str) -> BaselineSnapshot:
        """Accept either a JSON-encoded snapshot or raw stdout.

        Newer baselines are recorded via ``record_baseline_structured`` as
        JSON; legacy baselines are raw stdout_tail strings that we re-parse
        at diff time.
        """
        candidate = baseline_raw.strip()
        if candidate.startswith("{"):
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return {
                        "passed": int(data.get("passed", 0)),
                        "failed": int(data.get("failed", 0)),
                        "failed_ids": list(data.get("failed_ids", []) or []),
                    }
            except (ValueError, TypeError):
                pass
        result: BaselineSnapshot = parser(baseline_raw)
        return result

    async def record_baseline(self, gates: list[GateCommandConfig]) -> dict[str, str]:
        baselines: dict[str, str] = {}
        for gate in gates:
            result = await self.run_gate(gate)
            if gate.baseline_parser:
                parser = get_parser(gate.baseline_parser)
                if parser is not None:
                    snapshot = parser(result.stdout_tail)
                    baselines[gate.name] = json.dumps(snapshot)
                    continue
            baselines[gate.name] = result.stdout_tail
        return baselines

    async def record_baseline_structured(
        self, gates: list[GateCommandConfig]
    ) -> dict[str, BaselineSnapshot]:
        """P1-2: record structured baselines keyed by gate name."""
        out: dict[str, BaselineSnapshot] = {}
        for gate in gates:
            result = await self.run_gate(gate)
            snapshot: BaselineSnapshot = empty_snapshot()
            if gate.baseline_parser:
                parser = get_parser(gate.baseline_parser)
                if parser is not None:
                    snapshot = parser(result.stdout_tail)
            out[gate.name] = snapshot
        return out


def _extract_failed_count(output: str) -> int | None:
    import re

    patterns = [
        r"(\d+)\s+failed",
        r"failed[:\s]+(\d+)",
        r"failures[:\s]+(\d+)",
        r"errors[:\s]+(\d+)",
        r"(\d+)\s+error",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None
