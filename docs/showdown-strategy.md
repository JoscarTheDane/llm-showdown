# Single-GPU stand-off strategy

## The constraint

One card, roughly 32 GB. The incumbents are dense 27B-class models at 17-20 GB of
weights plus a large KV cache. Two of them do not fit. A fair comparison
therefore is not "load both and hit them alternately" — it is a **handover**.

## The sequence

For each candidate, in order:

1. **Lock.** An advisory lock file holds the PID of the running bench. If a live
   PID is present the rig refuses to start. Two servers competing for one card
   produce OOM kills and timing numbers that mean nothing.
2. **Preflight.**
   - the GGUF exists on disk
   - `nvidia-smi` reports enough free VRAM for the weights plus headroom for the
     KV cache and compute buffers
   - **swap is not already in use** — benchmarking into swap measures the disk
   - the bench port is free
3. **Stop the incumbent** systemd service. This is the only privileged step.
4. **Start the candidate** on a dedicated bench port with the registry's flags,
   `--sleep-idle-seconds -1` so the model cannot unload mid-suite, logging to
   `/tmp/llm_showdown_server.log`.
5. **Wait for health** by polling the OpenAI-compatible `/models` endpoint, with a
   generous load timeout — loading a 20 GB model from disk is slow, and the
   process is watched for early exit so a crash is reported rather than waited on.
6. **Run the suite** (or the speed rig).
7. **Tear down and wait for VRAM to actually return** before touching anything
   else; a stopped process is not the same as a freed card.
8. **Restore the incumbent** and *verify* by reading the served model name back
   from the API. "The service started" is not the same as "the right model is
   serving".

## Fairness rules

- **Same server binary for every candidate.** Put it in the registry `defaults`
  and override only deliberately. A quantisation-specific build advantage is a
  build advantage, not a model advantage.
- **Same context size, KV cache type, batch sizes, threads and MTP settings.**
- **Only the weights differ.** Anything else is recorded in the report.
- Run the speed rig and the capability battery in separate passes — speculative
  decoding can change decode rate without changing quality, and mixing them
  invites misattribution.

## Root access

Only stopping and starting the incumbent unit needs root. Two options:

**Preferred — one narrow drop-in, then everything runs unattended:**

```bash
echo '%sudo ALL=(root) NOPASSWD: /usr/bin/systemctl stop llama-server, \
/usr/bin/systemctl start llama-server, /usr/bin/systemctl is-active llama-server' \
  | sudo tee /etc/sudoers.d/llama-showdown
sudo chmod 440 /etc/sudoers.d/llama-showdown
```

Adjust the unit name to match the installed service. This grants exactly the
three verbs on exactly one unit.

**Zero-config — no root at all:** start the candidate yourself and bench in place:

```bash
python -m bench.cli suite --model <path-or-name> --url http://127.0.0.1:8095/v1 --no-swap
```

The rig **never** drives an interactive password prompt: if `sudo -n` is not
available it prints the drop-in above and stops.

## Failure modes and their symptoms

- **Both models resident** — OOM kill, or timing that collapses. Prevented by the
  lock and the preflight VRAM check.
- **Benchmarking into swap** — absurdly low decode rates. Prevented by the swap
  check; the rig refuses to start rather than publish a meaningless number.
- **Idle unload mid-suite** — one trial mysteriously slow. Prevented by
  `--sleep-idle-seconds -1` on the bench server, plus a warm-up call.
- **Cold-start penalty** — first request pays the weight-load cost and shows a
  huge TTFT. Every run warms up before measuring.
- **A crash mid-swap leaves the box without a model** — the state file
  (`/tmp/llm_showdown_state.json`) records the incumbent; `bench.cli recover`
  restores it. The state file is written before the incumbent is stopped.
- **Ctrl-C during a run** — a signal handler restores the incumbent before exit.

## Why not compare in parallel on two GPUs

If a second card is ever available, run one model per card with the identical
harness and identical flags rather than time-slicing. Time-slicing adds
contention noise precisely where the comparison is closest — in latency.
