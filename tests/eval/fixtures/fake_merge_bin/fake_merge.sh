#!/usr/bin/env bash
# Fake `merge` CLI used by Phase 3 tests.
#
# Reads:
#   FAKE_FIXTURE_DIR  — root of the fixture (e.g. tests/eval/fixtures/dummy_run)
#   FAKE_SAMPLE_ID    — selects which fixture sample to clone
#   FAKE_DUMP_ENV     — when set to "1", dump $env / $cwd next to the artifacts
#   FAKE_EXIT_CODE    — when set, exit with that code BEFORE writing artifacts
#   FAKE_BAD_JSON     — when set to "1", emit non-JSON to stdout
#   FAKE_TOUCH_MEMORY — when set to "1", create .merge/memory.db (1 byte)
#   FAKE_NO_OUTPUT    — when set to "1", emit empty stdout
#   FAKE_MERGED_TREE_DIR — when set, overlay every file under that dir into
#                          $(pwd) so the working tree post-merge matches a
#                          ground-truth tree (used by e2e to land verdict=PASS)
#
# Required positional args from the runner: ignored — the script behaves
# the same regardless of merge-args. The runner controls behaviour via env.
set -euo pipefail

if [[ -n "${FAKE_EXIT_CODE:-}" && "${FAKE_EXIT_CODE}" != "0" ]]; then
  echo "fake-merge: forced exit ${FAKE_EXIT_CODE}" >&2
  exit "${FAKE_EXIT_CODE}"
fi

RUN_ID="r$(date +%s)$$"
MERGE_DIR="$(pwd)/.merge/runs/${RUN_ID}"
mkdir -p "${MERGE_DIR}"

FIXTURE_BASE="${FAKE_FIXTURE_DIR}/runs/${FAKE_SAMPLE_ID}"

cp "${FIXTURE_BASE}/merge_report_FIXTURE.json" "${MERGE_DIR}/merge_report_${RUN_ID}.json"
cp "${FIXTURE_BASE}/merge_report_FIXTURE.md"   "${MERGE_DIR}/merge_report_${RUN_ID}.md"
cp "${FIXTURE_BASE}/plan_review_FIXTURE.md"    "${MERGE_DIR}/plan_review_${RUN_ID}.md"
cp "${FIXTURE_BASE}/checkpoint.json"           "${MERGE_DIR}/checkpoint.json"

if [[ "${FAKE_DUMP_ENV:-}" == "1" ]]; then
  python3 -c "import json,os; print(json.dumps(dict(os.environ)))" > "${MERGE_DIR}/_env.json"
  echo "$(pwd)" > "${MERGE_DIR}/_cwd.txt"
fi

if [[ "${FAKE_TOUCH_MEMORY:-}" == "1" ]]; then
  echo "x" > "$(pwd)/.merge/memory.db"
fi

if [[ -n "${FAKE_MERGED_TREE_DIR:-}" && -d "${FAKE_MERGED_TREE_DIR}" ]]; then
  # Overlay every file from the merged-tree fixture into cwd. ``cp -R`` of
  # the directory's *contents* (note the trailing ``/.``) gives a clean
  # overlay without nesting an extra subdirectory.
  cp -R "${FAKE_MERGED_TREE_DIR}/." "$(pwd)/"
fi

if [[ "${FAKE_NO_OUTPUT:-}" == "1" ]]; then
  exit 0
fi

if [[ "${FAKE_BAD_JSON:-}" == "1" ]]; then
  echo "not a json line"
  exit 0
fi

cat <<EOF
{"status":"success","run_id":"${RUN_ID}","total_files":1,"auto_merged":1,"human_required":0,"human_decided":0,"failed_count":0,"judge_verdict":"APPROVED","errors":[]}
EOF
