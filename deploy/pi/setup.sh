#!/bin/bash
# First-boot setup of the news collector on a Raspberry Pi (Raspberry Pi OS Lite,
# 64-bit). Runs once as root from newspi-setup.service, which retries every 5 min
# until it succeeds. Input on the boot partition (/boot/firmware):
#   newspi-bundle.tar.gz  news_collector/ (repo working tree with .env and output/)
#                         plus rednote_state.json (login exported on the Mac)
# The bundle holds secrets: it is deleted from the boot partition once extracted.
# Logs to /var/log/newspi-setup.log (also copied to the boot partition) and reports
# the result to Telegram.

BOOT=/boot/firmware
USER_NAME=lin
HOME_DIR=/home/$USER_NAME
APP=$HOME_DIR/news_collector
LOG=/var/log/newspi-setup.log
DONE=/var/lib/newspi-setup.done
ATTEMPTS=/var/lib/newspi-setup.attempts
STEP="start"
SUMMARY=""

exec >>"$LOG" 2>&1
echo "=== newspi setup $(date '+%F %T') ==="
attempt=$(( $(cat "$ATTEMPTS" 2>/dev/null || echo 0) + 1 ))
echo "$attempt" > "$ATTEMPTS"

step() { STEP="$1"; echo "--- [$(date '+%T')] $STEP"; }
as_user() { sudo -u "$USER_NAME" -H bash -c "cd '$APP' && $*"; }

notify() {
    local token chat
    [ -f "$APP/.env" ] || return 0
    token=$(grep -E '^TELEGRAM_BOT_TOKEN=' "$APP/.env" | cut -d= -f2-)
    chat=$(grep -E '^TELEGRAM_CHAT_ID=' "$APP/.env" | cut -d= -f2-)
    [ -n "$token" ] && [ -n "$chat" ] || return 0
    curl -s -m 20 "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" >/dev/null || true
}

die() {
    echo "FAILED at step: $STEP"
    cp "$LOG" "$BOOT/newspi-setup.log" 2>/dev/null
    # First failure, then every 6th retry (~30 min), so a stuck step doesn't spam.
    if [ "$attempt" -eq 1 ] || [ $(( attempt % 6 )) -eq 0 ]; then
        notify "❌ newspi setup failed at step: ${STEP} (attempt ${attempt}). It retries every 5 min. Log: /var/log/newspi-setup.log, also newspi-setup.log on the SD card's boot partition."
    fi
    exit 1
}

step "extract bundle"
if [ -f "$BOOT/newspi-bundle.tar.gz" ]; then
    tar -xzf "$BOOT/newspi-bundle.tar.gz" -C "$HOME_DIR" || die
    [ -f "$HOME_DIR/rednote_state.json" ] && mv "$HOME_DIR/rednote_state.json" "$HOME_DIR/.rednote_state.json"
    chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR" || die
    chmod 600 "$APP/.env" "$HOME_DIR/.rednote_state.json" 2>/dev/null
    rm -f "$BOOT/newspi-bundle.tar.gz"
    echo "bundle extracted and removed from the boot partition"
fi
[ -f "$APP/.env" ] || die

step "wait for internet"
for _ in $(seq 1 60); do
    curl -s -m 10 -o /dev/null https://www.google.com && break
    sleep 10
done
curl -s -m 10 -o /dev/null https://www.google.com || die

step "apt packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update || die
apt-get install -y git python3-venv python3-pip curl cron avahi-daemon unattended-upgrades || die
systemctl enable --now cron avahi-daemon

step "python environment"
as_user "[ -x .venv/bin/python ] || python3 -m venv .venv" || die
as_user ".venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt" || die
SUMMARY+="Python $(python3 -V 2>&1 | cut -d' ' -f2), Playwright $(as_user ".venv/bin/pip show playwright" | awk '/^Version/{print $2}')"$'\n'

step "browser"
BROWSER_TEST='from playwright.sync_api import sync_playwright
import os
with sync_playwright() as p:
    exe = os.environ.get("REDNOTE_BROWSER_EXECUTABLE")
    b = p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {"channel": "chromium"}))
    pg = b.new_page(); pg.goto("about:blank"); print("browser ok", b.version); b.close()'
SYSTEM_CHROMIUM=""
if "$APP/.venv/bin/python" -m playwright install-deps chromium \
   && as_user ".venv/bin/python -m playwright install chromium" \
   && as_user ".venv/bin/python -c '$BROWSER_TEST'"; then
    SUMMARY+="Browser: Playwright Chromium"$'\n'
else
    echo "Playwright Chromium failed; falling back to the system Chromium"
    apt-get install -y chromium || die
    SYSTEM_CHROMIUM=$(command -v chromium || echo /usr/bin/chromium)
    as_user "REDNOTE_BROWSER_EXECUTABLE=$SYSTEM_CHROMIUM .venv/bin/python -c '$BROWSER_TEST'" || die
    SUMMARY+="Browser: system Chromium ($SYSTEM_CHROMIUM)"$'\n'
fi

step "rednote login"
if [ -f "$HOME_DIR/.rednote_state.json" ]; then
    if as_user "REDNOTE_BROWSER_EXECUTABLE=$SYSTEM_CHROMIUM .venv/bin/python scripts/rednote_digest.py --import-state $HOME_DIR/.rednote_state.json"; then
        SUMMARY+="rednote: logged in (session imported from the Mac)"$'\n'
    else
        SUMMARY+="rednote: imported session NOT accepted; run --login (headlines still work)"$'\n'
    fi
    rm -f "$HOME_DIR/.rednote_state.json"
fi

step "crontab"
CRON=$(cat "$APP/deploy/pi/crontab.txt") || die
if [ -n "$SYSTEM_CHROMIUM" ]; then
    CRON=$(echo "$CRON" | sed "s|^#REDNOTE_BROWSER_EXECUTABLE=.*|REDNOTE_BROWSER_EXECUTABLE=$SYSTEM_CHROMIUM|")
fi
echo "$CRON" | crontab -u "$USER_NAME" - || die
SUMMARY+="Schedule: $(crontab -u "$USER_NAME" -l | grep -c '^[0-9]') cron jobs (hourly 06-23, daily 17:25/20:25, papers Sat/Sun)"$'\n'

step "self-test: LLM"
as_user ".venv/bin/python -c 'import sys; sys.path.insert(0, \"scripts\")
from run_workflow import load_settings
from llm import create, get_llm
print(\"LLM reply:\", create(get_llm(load_settings(\".\")), \"Reply with the single word OK.\")[:20])'" || die
SUMMARY+="DeepSeek API: ok"$'\n'

step "self-test: feeds"
FEEDS=$(as_user ".venv/bin/python scripts/alert_news.py --dry-run --no-rednote" | grep -m1 "candidates:") || die
SUMMARY+="Feeds:${FEEDS}"$'\n'

step "finish"
touch "$DONE"
systemctl disable newspi-setup.service
cp "$LOG" "$BOOT/newspi-setup.log" 2>/dev/null
IP=$(hostname -I | awk '{print $1}')
notify "✅ newspi ready ($(hostname), ${IP})
${SUMMARY}First real run: the next hourly alert slot (06:00-23:00). The Mac's jobs are off.
SSH: ssh ${USER_NAME}@$(hostname).local"
echo "=== done $(date '+%F %T') ==="
