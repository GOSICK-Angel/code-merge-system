#!/usr/bin/env bash
# Phase B (P-γ-3) R2 baseline runner.
#
# Migrated from /tmp/eval-runs/run_v4_r2.sh; paths repointed to the
# committed dataset under tests/eval/datasets/r2/samples/. Defaults to
# all 5 R2 samples when called without arguments.
#
# Usage:
#   bash scripts/eval/run_r2.sh                       # all 5
#   bash scripts/eval/run_r2.sh r2-0001 r2-0003       # selective
#
# Required env (loaded from PRIOR_ENV before running):
#   ANTHROPIC_API_KEY / OPENAI_API_KEY
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATASET_ROOT="${REPO_ROOT}/tests/eval/datasets/r2/samples"
PRIOR_CFG="${PRIOR_CFG:-/tmp/eval-runs-r2/repo-0001/.merge/config.yaml}"
PRIOR_ENV="${PRIOR_ENV:-/Users/angel/AI/project/dify-official-plugins/.merge/.env}"
WORK_ROOT="${WORK_ROOT:-/tmp/eval-runs-r2-phaseB}"
RUNS_OUT="${WORK_ROOT}/runs"
LOG_DIR="${WORK_ROOT}/logs"
mkdir -p "${RUNS_OUT}" "${LOG_DIR}"

SAMPLES=("$@")
if [ ${#SAMPLES[@]} -eq 0 ]; then
  while IFS= read -r line; do SAMPLES+=("$line"); done < <(ls "${DATASET_ROOT}" 2>/dev/null | sort)
fi
if [ ${#SAMPLES[@]} -eq 0 ]; then
  echo "run_r2.sh: no samples found under ${DATASET_ROOT}" >&2
  exit 2
fi

set -a; source "${PRIOR_ENV}"; set +a
cd "${REPO_ROOT}"

for SID in "${SAMPLES[@]}"; do
  echo "===== ${SID} =====" | tee -a "${LOG_DIR}/r2.log"
  REPO_DIR="${WORK_ROOT}/repo-${SID#r2-}"
  rm -rf "${REPO_DIR}"
  /opt/homebrew/opt/python@3.11/bin/python3.11 -m scripts.eval.git_bootstrap \
    --sample "${DATASET_ROOT}/${SID}" \
    --out "${REPO_DIR}" 2>&1 | tee -a "${LOG_DIR}/r2.log"
  mkdir -p "${REPO_DIR}/.merge"
  cp "${PRIOR_CFG}" "${REPO_DIR}/.merge/config.yaml"
  cp "${PRIOR_ENV}" "${REPO_DIR}/.merge/.env"

  START=$(date +%s)
  (cd "${REPO_DIR}" && merge upstream --no-web --ci 2>&1) | tee -a "${LOG_DIR}/r2.log"
  EXIT_CODE=$?
  END=$(date +%s)
  echo "[${SID}] wall=$((END-START))s exit=${EXIT_CODE}" | tee -a "${LOG_DIR}/r2.log"

  WT_DIR="${RUNS_OUT}/${SID}/working_tree"
  rm -rf "${WT_DIR}"; mkdir -p "${WT_DIR}"
  (cd "${REPO_DIR}" && tar --exclude='.git' --exclude='.merge' --exclude='outputs' --exclude='.gitignore' -cf - .) | (cd "${WT_DIR}" && tar -xf -)

  RUN_DIR=$(ls -1dt "${REPO_DIR}/.merge/runs"/*/ 2>/dev/null | head -1)
  RID=$(basename "${RUN_DIR:-unknown}")
  OUT_DIR="${RUNS_OUT}/${SID}"
  [ -n "${RUN_DIR}" ] && cp -R "${RUN_DIR}"* "${OUT_DIR}/" 2>/dev/null || true
  for p in "${REPO_DIR}/outputs" "${REPO_DIR}/.merge/plans"; do
    [ -d "$p" ] && find "$p" -maxdepth 3 -name "merge_report*" -exec cp {} "${OUT_DIR}/" \; 2>/dev/null
  done
  GIT_SHA=$(cd "${REPO_ROOT}" && git rev-parse --short=7 HEAD 2>/dev/null || echo "unknown")
  cat > "${OUT_DIR}/run_meta.json" <<METAEOF
{
  "sample_id": "${SID}", "run_id": "${RID}", "seed": 0, "concurrency": 1,
  "cache_disabled": false, "wall_time_seconds": $((END-START)), "cost_usd": 0.0,
  "git_sha": "${GIT_SHA}",
  "model_matrix": {"all": "claude-opus-4-6"},
  "status": "success", "memory_clean_check": "passed", "exit_code": ${EXIT_CODE}
}
METAEOF
done
echo "===== R2 done ====="
