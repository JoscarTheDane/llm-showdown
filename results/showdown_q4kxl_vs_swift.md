# Showdown: Q4_K_XL vs Swift NVFP4

_Generated 2026-09-25T09:13:18Z — weights: correctness 0.45, token_cost 0.25, latency 0.2, longctx 0.1_

## Verdict: **Swift NVFP4**

Class wins: Q4_K_XL 0 — 1 Swift NVFP4

| | Q4_K_XL | Swift NVFP4 |
|---|---|---|
| served model | `/home/joshua/llama.cpp/models/Qwen3.8-27B-UD-Q4_K_XL-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf` | `/home/joshua/.cache/huggingface/hub/models--HuggingJoost--Swift-Qwen3.8-27B-NVFP4-GGUF/snapshots/440d42219353617be1c2a43cd8c2420c7aaedefe/Swift-Qwen3.8-27B-NVFP4-Q8mix.gguf` |
| pass rate | 92.7% (38/41) | 97.6% (40/41) |
| median wall per call | 3.79s | 3.34s |
| p90 wall | 10.23s | 9.75s |
| max wall | 12.76s | 11.21s |
| median tokens / call | 432 | 396 |
| median reasoning tokens / call | 0 | 0 |
| **tokens per CORRECT answer** | **406** | **396** |
| **seconds per CORRECT answer** | **3.51** | **3.34** |
| weighted score | 0.9343 | 0.9784 |

## By class

| class | Q4_K_XL | Swift NVFP4 | winner |
|---|---|---|---|
| arithmetic | 6/6 (100%) | 6/6 (100%) | tie |
| codegen | 6/6 (100%) | 6/6 (100%) | tie |
| doctrine | 6/6 (100%) | 6/6 (100%) | tie |
| extraction | 3/6 (50%) | 5/6 (83%) | Swift NVFP4 |
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
- Raw replies: `runs/raw_qwen38-27b-q4kxl.jsonl` and `runs/raw_swift-27b-nvfp4.jsonl` — re-grade from these before believing any single row.
