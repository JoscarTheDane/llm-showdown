# Showdown: Swift NVFP4 vs cdiamond NVFP4

_Generated 2026-09-25T09:18:48Z — weights: correctness 0.45, token_cost 0.25, latency 0.2, longctx 0.1_

## Verdict: **Swift NVFP4**

Class wins: Swift NVFP4 1 — 0 cdiamond NVFP4

| | Swift NVFP4 | cdiamond NVFP4 |
|---|---|---|
| served model | `/home/joshua/.cache/huggingface/hub/models--HuggingJoost--Swift-Qwen3.8-27B-NVFP4-GGUF/snapshots/440d42219353617be1c2a43cd8c2420c7aaedefe/Swift-Qwen3.8-27B-NVFP4-Q8mix.gguf` | `/home/joshua/.cache/huggingface/hub/models--cdiamond--Qwen3.8-27B-iMatrix-NVFP4-MTP-GGUF/snapshots/ac343e8f44caef0896f79d372ecc07ef7ab34ec8/Qwen3.8-27B-iMatrix-NVFP4-MTP.gguf` |
| pass rate | 97.6% (40/41) | 95.1% (39/41) |
| median wall per call | 3.34s | 3.01s |
| p90 wall | 9.75s | 11.0s |
| max wall | 11.21s | 16.42s |
| median tokens / call | 396 | 398 |
| median reasoning tokens / call | 0 | 0 |
| **tokens per CORRECT answer** | **396** | **380** |
| **seconds per CORRECT answer** | **3.34** | **2.87** |
| weighted score | 0.9784 | 0.9559 |

## By class

| class | Swift NVFP4 | cdiamond NVFP4 | winner |
|---|---|---|---|
| arithmetic | 6/6 (100%) | 6/6 (100%) | tie |
| codegen | 6/6 (100%) | 6/6 (100%) | tie |
| doctrine | 6/6 (100%) | 6/6 (100%) | tie |
| extraction | 5/6 (83%) | 4/6 (67%) | Swift NVFP4 |
| format | 3/3 (100%) | 3/3 (100%) | tie |
| grounding | 3/3 (100%) | 3/3 (100%) | tie |
| longctx | 2/2 (100%) | 2/2 (100%) | tie |
| protocol | 6/6 (100%) | 6/6 (100%) | tie |
| refusal | 3/3 (100%) | 3/3 (100%) | tie |

## What this does NOT prove

- The battery is self-authored: the same author wrote the tasks, the graders and the scoring weights. Every grader bug found in this run was failing a model unfairly until fixed.
- One suite pass is one sample. Re-running the same model over the same fixtures moves individual class verdicts; the raw logs are kept so any row can be re-graded offline without touching a GPU.
- Text-only. Nothing here measures vision, voice or multi-system integration.
- Single-turn. Long autonomous multi-step runs are where weaker models wobble, and this battery does not reproduce that.
- Raw replies: `runs/raw_swift-27b-nvfp4.jsonl` and `runs/raw_qwen38-27b-nvfp4-imatrix.jsonl` — re-grade from these before believing any single row.
