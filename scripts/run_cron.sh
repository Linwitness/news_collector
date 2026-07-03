#!/bin/bash
# Daily pipeline wrapper. Set PYTHON_BIN to use a specific interpreter
# (e.g. from a conda/venv env); defaults to python3 on PATH.
cd "$(dirname "$0")/.."

PYTHON="${PYTHON_BIN:-python3}"
mkdir -p logs

# Idempotency guard: skip only when *all* topics already have a report for today.
# This makes launchd's wake catch-up (and manual re-runs) safe — it avoids a
# second round of paid Claude calls once the day is complete — while still
# allowing a re-run to fill in any topic that failed transiently (e.g. a network
# blip dropping one topic, as happened 2026-06-20).
today="$(date +%Y%m%d)"
missing="$($PYTHON - "$today" <<'PY'
import json, os, sys
today = sys.argv[1]
with open("config/topics.json") as f:
    topics = json.load(f)
missing = [t["id"] for t in topics
           if not os.path.exists(f"output/daily/{t['id']}_{today}.md")]
print(",".join(missing))
PY
)"

if [ -z "$missing" ]; then
    echo "[run_cron] $(date '+%Y-%m-%d %H:%M') — all topics for $today already done; skipping." >> logs/cron.log
    exit 0
fi
echo "[run_cron] $(date '+%Y-%m-%d %H:%M') — running; missing topics: ${missing}" >> logs/cron.log

$PYTHON scripts/run_workflow.py >> logs/cron.log 2>&1
