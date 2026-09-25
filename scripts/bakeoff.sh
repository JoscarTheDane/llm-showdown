#!/usr/bin/env bash
# Unattended three-way bake-off on the single 32GB GPU.
# Benches all three 27B models (capability suite + speed), then restores the
# incumbent llama-server (port 8080 — the orchestrator's own brain).
# Designed to run with the orchestrator OFFLINE: every model run is the
# tested, crash-safe `bench.cli suite|speed` path (each restores the box in
# its own finally), and a final trap guarantees one more restore. A separate
# watchdog.sh force-recovers if this chain hard-dies (stale marker).
set -uo pipefail
cd ~/llm-showdown || exit 1
mkdir -p runs

STAMP=$(date +%Y%m%d_%H%M%S)
LOG="runs/bakeoff_$STAMP.log"
MARKER="runs/BAKEOFF_RUNNING"

touch "$MARKER"
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }

incumbent_up() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf --max-time 5 http://127.0.0.1:8080/v1/models >/dev/null 2>&1
  else
    python3 - <<'PY' >/dev/null 2>&1
import urllib.request,sys
urllib.request.urlopen("http://127.0.0.1:8080/v1/models",timeout=5)
sys.exit(0)
PY
  fi
}

final_restore() {
  log "TRAP: final restore"
  python3 -m bench.cli recover >>"$LOG" 2>&1 || true
  sleep 4
  if incumbent_up; then
    log "INCUMBENT UP on 8080 — box is back the way it was"
  else
    log "WARNING: 8080 not responding. On the PC run: sudo systemctl start llama-server"
  fi
  rm -f "$MARKER"
}
trap final_restore EXIT

log "BAKEOFF START pid=$$ log=$LOG"
python3 -m bench.cli recover >>"$LOG" 2>&1 || true

# Grace: let the orchestrator commit and go quiet BEFORE we take the GPU.
log "grace 180s before stopping the incumbent (8080)"
sleep 180

run_model() {
  local m="$1"
  touch "$MARKER"
  log "===== SUITE $m ====="
  python3 -m bench.cli suite --model "$m" --outdir runs --label "$m" --long-words 24000 >>"$LOG" 2>&1
  log "suite $m rc=$?"
  touch "$MARKER"
  log "===== SPEED $m ====="
  python3 -m bench.cli speed --model "$m" --outdir runs --label "$m" >>"$LOG" 2>&1
  log "speed $m rc=$?"
}

run_model qwen38-27b-q4kxl
run_model swift-27b-nvfp4
run_model qwen38-27b-nvfp4-imatrix

log "ALL MODELS DONE"
log "summaries:"
ls -1 runs/summary_*.json 2>/dev/null | tee -a "$LOG"
