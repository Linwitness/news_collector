#!/bin/bash
# Sourced by the cron wrappers: wait_for_network <log file> <tag>
# Returns 0 once the internet is reachable, or 1 after NETWORK_WAIT_MINUTES
# (default 30) of polling once a minute. Right after a wake the network is
# often not up yet; on 2026-10-17 every paper source failed DNS for this reason.

wait_for_network() {
    local log="$1" tag="$2"
    local url="${NETWORK_CHECK_URL:-https://www.google.com}"
    local minutes="${NETWORK_WAIT_MINUTES:-30}"
    local i
    for ((i = 0; i < minutes; i++)); do
        # Any HTTP response (even an error status) means DNS and routing work.
        if curl -s -m 10 -o /dev/null "$url"; then
            [ "$i" -gt 0 ] && echo "[$tag] $(date '+%Y-%m-%d %H:%M') — network up after ${i} min." >> "$log"
            return 0
        fi
        sleep 60
    done
    echo "[$tag] $(date '+%Y-%m-%d %H:%M') — network unavailable for ${minutes} min; will retry at next slot." >> "$log"
    return 1
}
