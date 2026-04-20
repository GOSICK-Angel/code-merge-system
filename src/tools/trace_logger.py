import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AgentUtilizationStats:
    """Per-agent cumulative context utilization stats."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_prompt_tokens: int = 0
    total_budget_tokens: int = 0
    peak_utilization: float = 0.0
    total_elapsed_seconds: float = 0.0

    @property
    def avg_utilization(self) -> float:
        if self.total_budget_tokens == 0:
            return 0.0
        return self.total_prompt_tokens / self.total_budget_tokens


class TraceLogger:
    def __init__(self, debug_dir: str, run_id: str):
        self._path = Path(debug_dir) / f"llm_traces_{run_id}.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._agent_stats: dict[str, AgentUtilizationStats] = {}

    def record(
        self,
        agent: str,
        model: str,
        provider: str,
        prompt_chars: int,
        response_chars: int,
        elapsed_seconds: float,
        attempt: int,
        max_attempts: int,
        success: bool,
        error: str | None = None,
        prompt_preview: str = "",
        response_preview: str = "",
        estimated_tokens: int | None = None,
        budget_available: int | None = None,
        utilization: float | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "agent": agent,
            "model": model,
            "provider": provider,
            "prompt_chars": prompt_chars,
            "response_chars": response_chars,
            "elapsed_s": round(elapsed_seconds, 2),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "success": success,
        }
        if error:
            entry["error"] = error
        if prompt_preview:
            entry["prompt_preview"] = prompt_preview[:300]
        if response_preview:
            entry["response_preview"] = response_preview[:300]
        if estimated_tokens is not None:
            entry["estimated_tokens"] = estimated_tokens
        if budget_available is not None:
            entry["budget_available"] = budget_available
        if utilization is not None:
            entry["utilization"] = utilization

        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

            stats = self._agent_stats.setdefault(agent, AgentUtilizationStats())
            stats.total_calls += 1
            stats.total_elapsed_seconds += elapsed_seconds
            if success:
                stats.successful_calls += 1
            else:
                stats.failed_calls += 1
            if estimated_tokens is not None:
                stats.total_prompt_tokens += estimated_tokens
            if budget_available is not None:
                stats.total_budget_tokens += budget_available
            if utilization is not None:
                stats.peak_utilization = max(stats.peak_utilization, utilization)

    def record_phase_transition(
        self,
        run_id: str,
        from_status: str,
        to_status: str,
        triggered_by: str,
        elapsed: float,
        reason: str = "",
    ) -> None:
        entry: dict[str, Any] = {
            "type": "phase_transition",
            "run_id": run_id,
            "from": from_status,
            "to": to_status,
            "agent": triggered_by,
            "elapsed": round(elapsed, 2),
            "reason": reason,
            "ts": time.time(),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def get_agent_stats(self, agent: str) -> AgentUtilizationStats | None:
        return self._agent_stats.get(agent)

    def get_all_stats(self) -> dict[str, AgentUtilizationStats]:
        return dict(self._agent_stats)

    def get_utilization_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for agent_name, stats in self._agent_stats.items():
            summary[agent_name] = {
                "total_calls": stats.total_calls,
                "successful_calls": stats.successful_calls,
                "failed_calls": stats.failed_calls,
                "avg_utilization": round(stats.avg_utilization, 4),
                "peak_utilization": round(stats.peak_utilization, 4),
                "total_elapsed_s": round(stats.total_elapsed_seconds, 2),
            }
        return summary

    @property
    def path(self) -> Path:
        return self._path
