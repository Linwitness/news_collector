#!/bin/bash
# Alert poller wrapper. Set PYTHON_BIN to use a specific interpreter
# (e.g. from a conda/venv env); defaults to python3 on PATH.
cd "$(dirname "$0")/.."
mkdir -p logs
"${PYTHON_BIN:-python3}" scripts/alert_news.py >> logs/alert.log 2>&1
