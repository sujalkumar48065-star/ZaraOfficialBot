#!/data/data/com.termux/files/usr/bin/bash
set -u

PROJECT_DIR="/sdcard/ZaraOfficialBot"
LAUNCHER="/tmp/zara-launch.py"
BOT_LOG="/tmp/zara-bot.log"
WATCHDOG_LOG="/tmp/zara-watchdog.log"

if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock || true
fi

cd "$PROJECT_DIR" || exit 1

while true; do
    if ! pgrep -f '[p]ython3 bot.py' >/dev/null 2>&1; then
        printf '%s starting Zara\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$WATCHDOG_LOG"
        python3 "$LAUNCHER" >> "$BOT_LOG" 2>&1
        code=$?
        printf '%s Zara exited with %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$code" >> "$WATCHDOG_LOG"
        sleep 5
    fi
    sleep 10
done
