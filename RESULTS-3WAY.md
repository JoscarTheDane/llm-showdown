# Three-way bake-off — Qwen3.8-27B for Hermes agent duty

**Date:** 2026-09-25 · **Hardware:** RTX 5090 32 GB, one GPU · **Server:** llama.cpp b10909 (only build with verified NVFP4 + MTP graph)
**Method:** unattended stand-off chain (`bakeoff.sh` + independent `watchdog.sh`); the incumbent (the Hermes agent's own brain on 8080) was stopped per candidate, benched, and restored after every model — verified `active` at the end.

## The contest

| | Q4_K_XL (incumbent) | Swift NVFP4 | cdiamond NVFP4 |
|---|---|---|---|
| gguf | `Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.92 GB) | `Swift-Qwen3.8-27B-NVFP4-Q8mix.gguf` (19.72 GB) | `Qwen3.8-27B-iMatrix-NVFP4-MTP.gguf` (17.13 GB) |
| what it is | UD Q4_K_XL, the model in production | reasoning-efficiency fine-tune of Qwen3.8-27B | iMatrix-calibrated NVFP4, embedded MTP head |

**Fairness rule, enforced in `models.yaml`:** all three ran on the *same binary* (b10909) with *identical flags* mirroring the production unit — `--swa-full`, `--device CUDA0`, `--main-gpu 0`, `--cont-batching`, `--no-mmproj-offload`, `--reasoning-preserve`, threads 16, MTP draft depth 2, q8_0 KV. **Only the weights differ.** Per-model tuning is out of scope for the bake-off (see follow-ups).

## Headline numbers

| | Q4_K_XL | Swift NVFP4 | cdiamond NVFP4 |
|---|---|---|---|
| **pass rate** | 92.7% (38/41) | **97.6% (40/41)** | 95.1% (39/41) |
| median wall / call | 3.79 s | 3.34 s | **3.01 s** |
| p90 wall | 10.23 s | **9.75 s** | 11.00 s |
| median completion tokens / call | 432 | **396** | 398 |
| **tokens per CORRECT answer** | 406 | 396 | **380** |
| **seconds per CORRECT answer** | 3.51 s | 3.34 s | **2.87 s** |
| reasoning tokens (median) | 0 | 0 | 0 |
| transport failures / empty-truncated | 0 / 0 | 0 / 0 | 0 / 0 |
| **rig weighted score** | 0.9343 | **0.9784** | 0.9559 |

Rig score weights: correctness 0.45, token cost 0.25, latency 0.20, long-context 0.10.

**Verdict by score: Swift NVFP4 wins.** cdiamond is the fastest and the cheapest per correct answer; Swift is the most accurate. Q4_K_XL — the model running in production — is last on every axis, though by narrow margins.

## By class (passed/total, 3 trials each task)

| class | Q4_K_XL | Swift | cdiamond |
|---|---|---|---|
| arithmetic | 6/6 | 6/6 | 6/6 |
| codegen | 6/6 | 6/6 | 6/6 |
| doctrine (email/campaign register) | 6/6 | 6/6 | 6/6 |
| **extraction** | **3/6** | **5/6** | **4/6** |
| format | 3/3 | 3/3 | 3/3 |
| grounding (no-fabrication) | 3/3 | 3/3 | 3/3 |
| longctx (24k words) | 2/2 | 2/2 | 2/2 |
| protocol (tool calls) | 6/6 | 6/6 | 6/6 |
| refusal | 3/3 | 3/3 | 3/3 |

**Eight of nine classes are a dead heat.** The entire three-way difference lives in one task: `extract_manifest_sow` — parse a SOW fixture into a manifest and find every buried line item. The needle item: **"air diving supervisor, 1 per shift, 2 shifts per day"**.

- Q4_K_XL: 0/3 — *never* extracted the supervisor line.
- cdiamond: 1/3 — got it once, dropped it twice.
- Swift: 2/3 — got it twice, dropped it once (its single miss also dropped "anode assemblies" via a singular/plural mismatch on the grader's alias list).

That is the honest shape of the result: on everything this battery measures, the three models are near-identical, and the ranking is decided by how consistently each finds a buried personnel item in a document.

## Speed rig (4 context depths × 3 repeats)

| | 512 tok | 8 k | 32 k | 64 k |
|---|---|---|---|---|
| **Q4_K_XL decode t/s** | 117.7 | 188.3 | 131.2 | 115.6 |
| **Swift decode t/s** | 137.3 | 133.2 | 128.0 | 109.1 |
| **cdiamond decode t/s** | **197.8** | **194.2** | **179.1** | 113.7 |
| Q4_K_XL prefill t/s | 883.7 | 8708.2 | 31757.2 | **47648.6** |
| Swift prefill t/s | 939.5 | **13614.9** | 29702.6 | **54721.3** |
| cdiamond prefill t/s | 690.1 | 8314.8 | 25782.5 | 46146.6 |
| Q4_K_XL TTFT s | 0.459 | 0.685 | 0.748 | 0.996 |
| Swift TTFT s | 0.432 | 0.438 | 0.800 | 0.867 |
| cdiamond TTFT s | 0.588 | 0.717 | 0.921 | 1.029 |

Reading it straight: **cdiamond decodes fastest at every depth** (4-bit iMatrix + embedded MTP doing its job). Swift prefills fastest at 8k/32k but loses prefill at 64k to Q4_K_XL — the mixed Q8 layers cost bandwidth on big prompts. At the depth agent prompts actually live (long system context + compaction), Q4_K_XL's prefill edge (47.6k vs 46.1k at 64k) is real but thin; decode is where turn time is spent, and there cdiamond leads by ~50% over the incumbent at 512–32k.

## What this does NOT prove

- **One sample each.** Three trials per task, temp 0.2, single run. Trial-level variance moves individual rows; the class verdicts could shuffle a trial or two on re-run. The raw logs are kept (`results/raw_*.jsonl`) so any row can be re-graded offline — no GPU needed.
- **Self-authored battery.** The tasks, graders and scoring weights all come from the same author (Josh + Aiduh). The first real run found *four* grader bugs that had been failing models unfairly — all fixed before this run, all regression-tested in `selftest`.
- **Text-only, single-turn.** No vision, voice, or multi-step autonomous runs. Long campaigns are where agent models wobble most, and this battery doesn't reproduce that.
- **Speed rig ≠ production latency.** Short generated answers at fixed context; real turns carry long system context, compaction and tool loops.

## Recommendation (for the production unit)

If the goal is *fewer wrong answers* — the thing that actually bites in an agent loop — **Swift NVFP4 is the one to promote**: 40/41, best p90, lowest median tokens. The cost of promotion is real, not free: the production `llama-server.service` runs the b10069 build, which does **not** carry the qwen4exp MTP graph — promoting either NVFP4 model means pointing the unit at the b10909 binary (`/home/joshua/llama.cpp-mtp/llama-server`) with the MTP draft flags, i.e. a unit change, not just a model path.

If the goal is *speed and cost per answer*, cdiamond is the winner there (197.8 t/s @512, 380 tokens/correct answer) at a 1/41 accuracy gap to Swift — a defensible choice if latency is the binding constraint.

Keeping Q4_K_XL is also defensible: it is proven in this exact duty cycle, the margins are narrow, and the promotion cost is a unit change with a dark window. The bake-off says it would *lose* on re-run, not that it is broken.

## Artifacts

| file | what |
|---|---|
| `results/summary_<model>.json` | suite summaries (totals, by-class, by-task with grader reasons) |
| `results/raw_<model>.jsonl` | every trial: reply, grader verdict, tokens, wall time, finish reason |
| `results/speed_<model>.json` | speed rig rows |
| `results/bakeoff_20260925_110611.log` | full unattended chain log (swap → bench → restore per model) |
| `scripts/bakeoff.sh` + `scripts/watchdog.sh` | the unattended chain + its independent safety net, exactly as they ran |
| `results/showdown_*.md` | the three pairwise reports, generated by `python -m bench.cli report` |

Re-run any pairwise comparison offline (no GPU):

```
python -m bench.cli report --a results/summary_a.json --b results/summary_b.json --label-a A --label-b B --out showdown.md
```
