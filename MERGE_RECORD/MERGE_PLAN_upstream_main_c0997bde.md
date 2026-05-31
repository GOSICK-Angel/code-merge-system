# Merge Plan: upstream/main → feature/fork

Generated: 2026-05-27 03:28
Merge base: `abc123`
Run ID: `c0997bde-e7e0-465d-847f-7b02f0e1c417`

**Project Context**: # CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"          # install with dev deps
pytest tests/unit/               # unit tests only
pytest                           # all tests
pytest -k "test_name"            # single test
mypy src                         # type check (strict mode)
ruff check src/                  # lint
ruff format src/                 # format
merge --help…

| Metric | Value |
|------|------|
| Total files | 0 |
| Auto-safe | 0 |
| Auto-risky | 0 |
| Human required | 0 |
| Security sensitive | 0 |
| Auto-merge rate | 50.0% |

---

## Merge Batch Plan

---

## Planner-Judge Review Log

### Round 0
- **Verdict**: approved
- **Summary**: test verdict
- **Issues**: 0
