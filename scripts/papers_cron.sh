#!/bin/bash
# Weekly paper-report wrapper. Set PYTHON_BIN to use a specific interpreter
# (e.g. from a conda/venv env); defaults to python3 on PATH.
cd "$(dirname "$0")/.."

PYTHON="${PYTHON_BIN:-python3}"
mkdir -p logs

# Idempotency guard: skip if a paper report already exists for this ISO week,
# so launchd's wake catch-up and manual re-runs don't spend a second round of
# LLM calls (and don't send a second Telegram digest).
done_this_week="$($PYTHON - <<'PY'
import glob, os
from datetime import date, datetime
week = date.today().isocalendar()[:2]
for f in glob.glob("output/papers/papers_*.md"):
    tag = os.path.basename(f)[len("papers_"):-len(".md")]
    try:
        if datetime.strptime(tag, "%Y%m%d").date().isocalendar()[:2] == week:
            print(f)
            break
    except ValueError:
        pass
PY
)"

if [ -n "$done_this_week" ]; then
    echo "[papers_cron] $(date '+%Y-%m-%d %H:%M') — already done this week ($done_this_week); skipping." >> logs/papers.log
    exit 0
fi
echo "[papers_cron] $(date '+%Y-%m-%d %H:%M') — running." >> logs/papers.log

$PYTHON scripts/run_papers.py >> logs/papers.log 2>&1
