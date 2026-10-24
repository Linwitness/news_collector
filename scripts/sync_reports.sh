#!/bin/bash
# Mirror the Raspberry Pi's output/ (reports and state) into this checkout, so the
# reports can be read on the Mac. Run every 60 min by the launchd agent
# com.lin.newscollector.reportsync (see README "Run on a Raspberry Pi").
# Tries the Tailscale name first (works anywhere), then the LAN name.
cd "$(dirname "$0")/.."
mkdir -p logs output

for host in ${PI_HOSTS:-newspi newspi.local}; do
    if rsync -az --delete --exclude '.lock' --exclude '.rednote.lock' \
        -e "ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new" \
        "${PI_USER:-lin}@${host}:news_collector/output/" output/ 2>>logs/sync.log; then
        echo "[sync_reports] $(date '+%Y-%m-%d %H:%M') — synced via ${host}" >> logs/sync.log
        exit 0
    fi
done
echo "[sync_reports] $(date '+%Y-%m-%d %H:%M') — Pi unreachable; will retry next slot" >> logs/sync.log
exit 1
