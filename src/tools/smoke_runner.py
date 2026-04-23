"""P1-3: SmokeRunner — execute smoke test suites.

Supports three case kinds:
- ``shell``: spawn a shell command, pass iff exit code == 0
- ``http``: perform an HTTP request, pass iff status code == expect_status
- ``playwright``: delegated to external playwright runner via tag — best-effort
  shell invocation ``npx playwright test --grep <tag>``

All cases run sequentially per suite to respect ordering requirements of
end-to-end tests. Suites themselves run sequentially to avoid port
conflicts on shared test servers.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

from src.models.config import SmokeTestCase, SmokeTestConfig, SmokeTestSuite
from src.models.smoke import (
    SmokeSuiteReport,
    SmokeTestReport,
    SmokeTestResult,
)

logger = logging.getLogger(__name__)


class SmokeRunner:
    def __init__(self, repo_path: Path | str):
        self.repo_path = Path(repo_path)

    async def run(self, cfg: SmokeTestConfig) -> SmokeTestReport:
        suites: list[SmokeSuiteReport] = []
        for suite in cfg.suites:
            suite_report = await self._run_suite(suite)
            suites.append(suite_report)

        all_passed = all(s.all_passed for s in suites) if suites else True
        return SmokeTestReport(all_passed=all_passed, suites=suites)

    async def _run_suite(self, suite: SmokeTestSuite) -> SmokeSuiteReport:
        results: list[SmokeTestResult] = []
        for case in suite.cases:
            if suite.kind == "shell":
                result = await self._run_shell(suite, case)
            elif suite.kind == "http":
                result = await self._run_http(suite, case)
            elif suite.kind == "playwright":
                result = await self._run_playwright(suite, case)
            else:
                result = SmokeTestResult(
                    suite_name=suite.name,
                    case_id=case.id,
                    kind=suite.kind,
                    status="error",
                    error_message=f"Unknown kind: {suite.kind}",
                )
            results.append(result)
        return SmokeSuiteReport(suite_name=suite.name, kind=suite.kind, results=results)

    async def _run_shell(
        self, suite: SmokeTestSuite, case: SmokeTestCase
    ) -> SmokeTestResult:
        if not case.cmd:
            return SmokeTestResult(
                suite_name=suite.name,
                case_id=case.id,
                kind="shell",
                status="error",
                error_message="Missing cmd for shell case",
            )

        work_dir = self.repo_path / suite.working_dir
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                case.cmd,
                cwd=str(work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=case.timeout_seconds
            )
            exit_code = proc.returncode if proc.returncode is not None else 0
            duration = time.monotonic() - start
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            status: Literal["pass", "fail", "skipped", "error"] = (
                "pass" if exit_code == 0 else "fail"
            )
            return SmokeTestResult(
                suite_name=suite.name,
                case_id=case.id,
                kind="shell",
                status=status,
                duration_seconds=round(duration, 3),
                exit_code=exit_code,
                stdout_tail="\n".join(stdout.strip().splitlines()[-20:]),
                stderr_tail="\n".join(stderr.strip().splitlines()[-20:]),
            )
        except asyncio.TimeoutError:
            return SmokeTestResult(
                suite_name=suite.name,
                case_id=case.id,
                kind="shell",
                status="error",
                duration_seconds=time.monotonic() - start,
                error_message=f"Timeout after {case.timeout_seconds}s",
            )
        except Exception as exc:
            return SmokeTestResult(
                suite_name=suite.name,
                case_id=case.id,
                kind="shell",
                status="error",
                duration_seconds=time.monotonic() - start,
                error_message=str(exc),
            )

    async def _run_http(
        self, suite: SmokeTestSuite, case: SmokeTestCase
    ) -> SmokeTestResult:
        if not case.url:
            return SmokeTestResult(
                suite_name=suite.name,
                case_id=case.id,
                kind="http",
                status="error",
                error_message="Missing url for http case",
            )

        def _do_request() -> tuple[int, str]:
            data = case.body.encode("utf-8") if case.body else None
            req = urllib.request.Request(
                case.url, data=data, method=case.method, headers=case.headers
            )
            try:
                with urllib.request.urlopen(req, timeout=case.timeout_seconds) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    return resp.status, body
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                return e.code, body

        start = time.monotonic()
        try:
            status_code, body = await asyncio.to_thread(_do_request)
            duration = time.monotonic() - start
            passed = status_code == case.expect_status
            return SmokeTestResult(
                suite_name=suite.name,
                case_id=case.id,
                kind="http",
                status="pass" if passed else "fail",
                duration_seconds=round(duration, 3),
                http_status=status_code,
                stdout_tail=body[:1000],
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return SmokeTestResult(
                suite_name=suite.name,
                case_id=case.id,
                kind="http",
                status="error",
                duration_seconds=time.monotonic() - start,
                error_message=str(exc),
            )

    async def _run_playwright(
        self, suite: SmokeTestSuite, case: SmokeTestCase
    ) -> SmokeTestResult:
        tag = case.tag or ""
        cmd = f"npx playwright test --grep {tag}" if tag else "npx playwright test"
        case_for_shell = case.model_copy(update={"cmd": cmd})
        return await self._run_shell(suite, case_for_shell)
