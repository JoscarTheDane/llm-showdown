#!/usr/bin/env bash
# Safety net for the unattended bake-off. Independent of the orchestrator.
# - If the bake-off marker exists but goes STALE (chain hard-killed mid-run),
#   force a recover so the incumbent (the orchestrator's brain, port 8080)
#   comes back no matter what.
# - If there is NO bake-off marker but 8080 is down, nudge the service up.
cd ~/llm-showdown || exit 1
MARKER="runs/BAKEOFF_RUNNING"
MAX_AGE=21600   # 6 hours — longer than the whole bake-off should take

incumbent_up() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf --max-time 5 http://127.0.0.1:8080/v1/models >/dev/null 2>&1
  else
    python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/v1/models',timeout=5)" >/dev/null 2>&1
  fi
}

while true; do
  sleep 120
  if [ -f "$MARKER" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$MARKER" 2>/dev/null || echo 0) ))
    if [ "$age" -gt "$MAX_AGE" ]; then
      echo "[watchdog] stale marker (age ${age}s) — chain likely dead; forcing recover"
      python3 -m bench.cli recover || true
      sleep 4
      incumbent_up || sudo -n systemctl start llama-server || true
      sleep 4
      incumbent_up && echo "[watchdog] incumbent restored" || echo "[watchdog] STILL DOWN — manual intervention needed"
      rm -f "$MARKER"
    fi
  else
    if ! incumbent_up; then
      echo "[watchdog] 8080 down with no bake-off marker — starting service"
      sudo -n systemctl start llama-server || true
    fi
  fi
done
