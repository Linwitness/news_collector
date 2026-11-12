#!/bin/bash
# Paper-report wrapper, run Monday, Wednesday and Saturday (deploy/pi/crontab.txt).
# Set PYTHON_BIN to use a specific interpreter (e.g. from a conda/venv env);
# defaults to python3 on PATH.
cd "$(dirname "$0")/.."

PYTHON="${PYTHON_BIN:-python3}"
mkdir -p logs

# Idempotency guard: skip if today's paper report already exists, so the retry
# slot and manual re-runs don't spend a second round of LLM calls (and don't
# send a second Telegram digest).
today_report="output/papers/papers_$(date +%Y%m%d).md"
if [ -f "$today_report" ]; then
    echo "[papers_cron] $(date '+%Y-%m-%d %H:%M') — already done today ($today_report); skipping." >> logs/papers.log
    exit 0
fi

source scripts/wait_for_network.sh
wait_for_network logs/papers.log papers_cron || exit 1

echo "[papers_cron] $(date '+%Y-%m-%d %H:%M') — running." >> logs/papers.log

$PYTHON scripts/run_papers.py >> logs/papers.log 2>&1
