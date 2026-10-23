#!/bin/bash
# Run ONE API suite against a throwaway database, then drop it.
#
#   tests/api_suites/run_suite.sh <suite> [db-name]      e.g.  run_suite.sh api_test_pay8
#
# The database name defaults to the one the suite insists on (each suite refuses to run on anything else); the real
# development database is never touched. Needs: Docker Postgres up (docker compose up -d postgres), `uv`, and for the
# suites that send mail, Mailpit on :8025 (docker compose up -d mailpit).
# Prints only the lines that matter (failures, errors, the final count); the full log is in $TMPDIR/<suite>.log.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/../.." && pwd)"
cd "$BACKEND" || exit 1

SUITE=$1
[ -f "$HERE/$SUITE.py" ] || { echo "no such suite: $SUITE"; exit 2; }
DB=${2:-$(grep -o 'assert "ar_[a-z0-9_]*" in' "$HERE/$SUITE.py" | head -1 | grep -o 'ar_[a-z0-9_]*')}
DB=${DB:-ar_${SUITE#api_test_}}
PG_CONTAINER=${PG_CONTAINER:-accounts-receivable-assistant-postgres-1}
PSQL="docker exec $PG_CONTAINER psql -U ar_assistant -d postgres -q"
LOG="${TMPDIR:-/tmp}/$SUITE.log"

$PSQL -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" >/dev/null 2>&1

# Everything a suite must not do by accident is switched off here: no Gmail polling, mail only to Mailpit.
export GMAIL_POLL_ENABLED=false SMTP_HOST=localhost SMTP_PORT=1025 SMTP_USER= SMTP_PASSWORD= SMTP_ALLOWLIST_BYPASS=true \
       DATABASE_URL=postgresql+asyncpg://ar_assistant:ar_assistant@localhost:5433/$DB \
       RAW_FILES_DIR=./data/raw_test_$DB PYTHONIOENCODING=utf-8
uv run alembic upgrade head >/dev/null 2>&1 && uv run python scripts/seed_admin.py >/dev/null 2>&1

echo "== $SUITE ($DB)"
uv run python "$HERE/$SUITE.py" "$HERE/fixtures" > "$LOG" 2>&1
STATUS=$?
grep -E "^RESULT|Traceback|Error|assert" "$LOG" | grep -v "RESULT PASS"
rm -rf "./data/raw_test_$DB"
$PSQL -c "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1

# green only when the last line says "N/N passed"
LAST=$(grep -E "^RESULT [0-9]+/[0-9]+ passed" "$LOG" | tail -1)
PASSED=$(echo "$LAST" | sed -E 's/RESULT ([0-9]+)\/([0-9]+) passed/\1/')
TOTAL=$(echo "$LAST" | sed -E 's/RESULT ([0-9]+)\/([0-9]+) passed/\2/')
[ -n "$LAST" ] && [ "$PASSED" = "$TOTAL" ] && [ "$STATUS" = "0" ]
