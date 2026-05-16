#!/usr/bin/env bash
# Phase B (P-γ-3) Tier 1 (dify v5) 30-sample regression runner.
#
# Wraps the existing /tmp/eval-runs/run_v4_full.sh and forces the full
# 30-sample default (the upstream wrapper defaults to only 5 SIDs;
# Phase B GO #2 requires all 30 to be executed). Pass explicit SIDs
# to override.
#
# Usage:
#   bash scripts/eval/run_tier1.sh                    # all 30
#   bash scripts/eval/run_tier1.sh t1-0001 t1-0005    # subset
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATASET_ROOT="${REPO_ROOT}/tests/eval/datasets/tier1/samples"
WRAPPER="${WRAPPER:-/tmp/eval-runs/run_v4_full.sh}"

if [ ! -x "${WRAPPER}" ] && [ ! -f "${WRAPPER}" ]; then
  echo "run_tier1.sh: missing wrapper ${WRAPPER}" >&2
  exit 2
fi

SAMPLES=("$@")
if [ ${#SAMPLES[@]} -eq 0 ]; then
  while IFS= read -r line; do SAMPLES+=("$line"); done < <(ls "${DATASET_ROOT}" 2>/dev/null | sort)
fi
if [ ${#SAMPLES[@]} -eq 0 ]; then
  echo "run_tier1.sh: no samples found under ${DATASET_ROOT}" >&2
  exit 2
fi

echo "run_tier1.sh: dispatching ${#SAMPLES[@]} sample(s) via ${WRAPPER}" >&2
bash "${WRAPPER}" "${SAMPLES[@]}"
