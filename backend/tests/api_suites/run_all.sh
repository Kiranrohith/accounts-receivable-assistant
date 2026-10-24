#!/bin/bash
# Run every API suite, one throwaway database each, and print one line per suite.
#
#   tests/api_suites/run_all.sh                 everything (a few suites call the real models: Groq / Gemini keys in .env)
#   tests/api_suites/run_all.sh --no-model      only the suites that need no model
#   tests/api_suites/run_all.sh dis pay         only suites whose name contains "dis" or "pay"
#
# api_test_mail4 reads the seed kit (not in git): it runs only when KIT_DIR points at it, otherwise it is listed as
# skipped, never silently dropped. The exit code is non-zero when any suite that ran failed.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL="api_test_dis3_llm api_test_taxid_llm api_test_intake api_test_e2e api_test_pay3 api_test_fx2 api_test_po2 api_test_po3 api_test_po4 api_test_review api_test_upload api_test_sendback api_test_mail4"

NO_MODEL=0; FILTERS=()
for arg in "$@"; do
  if [ "$arg" = "--no-model" ]; then NO_MODEL=1; else FILTERS+=("$arg"); fi
done

FAILED=(); SKIPPED=(); PASSED=0
for file in "$HERE"/api_test_*.py; do
  suite=$(basename "$file" .py)
  if [ ${#FILTERS[@]} -gt 0 ]; then
    match=0; for f in "${FILTERS[@]}"; do [[ "$suite" == *"$f"* ]] && match=1; done
    [ $match -eq 1 ] || continue
  fi
  if [[ " $MODEL " == *" $suite "* ]] && [ $NO_MODEL -eq 1 ]; then SKIPPED+=("$suite (needs a model)"); continue; fi
  if [ "$suite" = "api_test_mail4" ] && [ -z "$KIT_DIR" ]; then SKIPPED+=("$suite (set KIT_DIR to the seed kit)"); continue; fi
  start=$(date +%s)
  if bash "$HERE/run_suite.sh" "$suite"; then
    PASSED=$((PASSED + 1)); echo "   PASS  $suite  ($(( $(date +%s) - start ))s)"
  else
    FAILED+=("$suite"); echo "   FAIL  $suite  ($(( $(date +%s) - start ))s)  log: ${TMPDIR:-/tmp}/$suite.log"
  fi
done

echo
echo "passed: $PASSED   failed: ${#FAILED[@]}   skipped: ${#SKIPPED[@]}"
for s in "${SKIPPED[@]}"; do echo "  skipped: $s"; done
for s in "${FAILED[@]}"; do echo "  FAILED:  $s"; done
[ ${#FAILED[@]} -eq 0 ]
