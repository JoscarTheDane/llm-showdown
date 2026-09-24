"""1v1 report: turn two suite summaries into a decisive head-to-head.

The headline metric for this rig is not raw accuracy and not raw speed — it is
the cost of a CORRECT answer:

    * tokens per correct answer  (thinking tokens included: they are billed
      as output and dominate the bill on a reasoning model)
    * seconds per correct answer (wall time, so a slow-but-right model is not
      mistaken for a cheap one)

A model that is 40 percent faster and 20 percent wrong is not faster — the
retry costs more than it saved. So correctness gates everything: the weighted
score below multiplies the speed terms by the pass rate.

Weights are declared here, in the open, and can be re-run against the saved
summaries without touching the model.
"""

from __future__ import annotations

import json
from pathlib import Path

# Priority for this rig (a 24/7 personal agent on one GPU):
#   correctness first, then the token bill (thinking included), then wall time,
#   then behaviour on long context (where the agent spends most of its life).
WEIGHTS = {
    "correctness": 0.45,   # pass rate across all classes
    "token_cost": 0.25,    # median output+reasoning tokens per correct answer
    "latency": 0.20,       # median wall seconds per correct answer
    "longctx": 0.10,       # long-context class pass rate specifically
}


def _load(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        return path_or_dict
    return json.loads(Path(path_or_dict).read_text())


def _class_map(summary: dict) -> dict:
    return {c["class"]: c for c in summary.get("by_class", [])}


def _safe_div(a, b):
    return (a / b) if b else None


def _score(summary: dict, ref: dict) -> dict:
    """Weighted score in [0,1]. Speed terms are normalised against `ref` and
    multiplied by the pass rate, so being fast and wrong earns nothing."""
    t = summary["totals"]
    classes = _class_map(summary)
    acc = t["pass_rate"]
    longctx = classes.get("longctx", {}).get("pass_rate", acc)
    tok = t.get("median_completion_tokens_per_correct") or ref.get("tok") or 1
    sec = t.get("median_wall_s_per_correct") or ref.get("sec") or 1
    ref_tok = ref.get("tok") or tok
    ref_sec = ref.get("sec") or sec
    tok_term = min(1.0, ref_tok / tok) if tok else 0.0
    sec_term = min(1.0, ref_sec / sec) if sec else 0.0
    return {
        "correctness_rate": acc,
        "tokens_per_correct": tok,
        "seconds_per_correct": sec,
        "longctx_rate": longctx,
        "token_term": round(tok_term, 3),
        "latency_term": round(sec_term, 3),
        "token_term_gated": round(tok_term * acc, 3),
        "latency_term_gated": round(sec_term * acc, 3),
        "score": round(
            WEIGHTS["correctness"] * acc
            + WEIGHTS["token_cost"] * tok_term * acc
            + WEIGHTS["latency"] * sec_term * acc
            + WEIGHTS["longctx"] * longctx, 4),
    }


def head_to_head(a_path, b_path, out_md: str | None = None,
                 label_a: str | None = None, label_b: str | None = None) -> dict:
    A, B = _load(a_path), _load(b_path)
    label_a = label_a or A.get("label", "A")
    label_b = label_b or B.get("label", "B")

    ref = {"tok": A["totals"].get("median_completion_tokens_per_correct"),
           "sec": A["totals"].get("median_wall_s_per_correct")}
    sa, sb = _score(A, ref), _score(B, ref)

    ca, cb = _class_map(A), _class_map(B)
    all_classes = sorted(set(ca) | set(cb))
    rows = []
    for cls in all_classes:
        x, y = ca.get(cls), cb.get(cls)
        pa = x["pass_rate"] if x else None
        pb = y["pass_rate"] if y else None
        if pa is None or pb is None:
            winner = label_a if pb is None else label_b
        elif pa > pb:
            winner = label_a
        elif pb > pa:
            winner = label_b
        else:
            winner = "tie"
        rows.append({
            "class": cls,
            "a_rate": pa, "b_rate": pb,
            "a_pass": f"{x['passed']}/{x['trials']}" if x else "-",
            "b_pass": f"{y['passed']}/{y['trials']}" if y else "-",
            "a_tokens": x["median_completion_tokens"] if x else None,
            "b_tokens": y["median_completion_tokens"] if y else None,
            "a_sec": x["median_wall_s"] if x else None,
            "b_sec": y["median_wall_s"] if y else None,
            "winner": winner,
        })

    wins_a = sum(1 for r in rows if r["winner"] == label_a)
    wins_b = sum(1 for r in rows if r["winner"] == label_b)
    verdict = label_a if sa["score"] > sb["score"] else (
        label_b if sb["score"] > sa["score"] else "tie")

    result = {
        "generated_utc": A.get("generated_utc"),
        "a": {"label": label_a, "model": A.get("model"), "totals": A["totals"], "score": sa},
        "b": {"label": label_b, "model": B.get("model"), "totals": B["totals"], "score": sb},
        "weights": WEIGHTS,
        "classes": rows,
        "class_wins": {label_a: wins_a, label_b: wins_b},
        "verdict": verdict,
        "raw_a": A.get("raw_log"), "raw_b": B.get("raw_log"),
    }
    if out_md:
        Path(out_md).write_text(render_markdown(result))
    return result


def render_markdown(r: dict) -> str:
    a, b = r["a"], r["b"]
    ta, tb = a["totals"], b["totals"]
    L = []
    add = L.append
    add(f"# Showdown: {a['label']} vs {b['label']}\n")
    add(f"_Generated {r['generated_utc']} — weights: "
        + ", ".join(f"{k} {v}" for k, v in r["weights"].items()) + "_\n")
    add(f"## Verdict: **{r['verdict']}**\n")
    add(f"Class wins: {a['label']} {r['class_wins'][a['label']]} — "
        f"{r['class_wins'][b['label']]} {b['label']}\n")
    add("| | " + a["label"] + " | " + b["label"] + " |")
    add("|---|---|---|")
    add(f"| served model | `{a['model']}` | `{b['model']}` |")
    add(f"| pass rate | {ta['pass_rate']:.1%} ({ta['passed']}/{ta['trials']}) | "
        f"{tb['pass_rate']:.1%} ({tb['passed']}/{tb['trials']}) |")
    add(f"| median wall per call | {ta['median_wall_s']}s | {tb['median_wall_s']}s |")
    add(f"| p90 wall | {ta['p90_wall_s']}s | {tb['p90_wall_s']}s |")
    add(f"| max wall | {ta['max_wall_s']}s | {tb['max_wall_s']}s |")
    add(f"| median tokens / call | {ta['median_completion_tokens']} | "
        f"{tb['median_completion_tokens']} |")
    add(f"| median reasoning tokens / call | {ta['median_reasoning_tokens']} | "
        f"{tb['median_reasoning_tokens']} |")
    add(f"| **tokens per CORRECT answer** | **{ta['median_completion_tokens_per_correct']}** "
        f"| **{tb['median_completion_tokens_per_correct']}** |")
    add(f"| **seconds per CORRECT answer** | **{ta['median_wall_s_per_correct']}** "
        f"| **{tb['median_wall_s_per_correct']}** |")
    add(f"| weighted score | {a['score']['score']} | {b['score']['score']} |")
    add("")
    add("## By class\n")
    add("| class | " + a["label"] + " | " + b["label"] + " | winner |")
    add("|---|---|---|---|")
    for row in r["classes"]:
        fa = f"{row['a_pass']} ({row['a_rate']:.0%})" if row["a_rate"] is not None else "-"
        fb = f"{row['b_pass']} ({row['b_rate']:.0%})" if row["b_rate"] is not None else "-"
        add(f"| {row['class']} | {fa} | {fb} | {row['winner']} |")
    add("")
    add("## What this does NOT prove\n")
    add("- The battery is self-authored: the same author wrote the tasks, the "
        "graders and the scoring weights. Every grader bug found in this run was "
        "failing a model unfairly until fixed.")
    add("- One suite pass is one sample. Re-running the same model over the same "
        "fixtures moves individual class verdicts; the raw logs are kept so any "
        "row can be re-graded offline without touching a GPU.")
    add("- Text-only. Nothing here measures vision, voice or multi-system "
        "integration.")
    add("- Single-turn. Long autonomous multi-step runs are where weaker models "
        "wobble, and this battery does not reproduce that.")
    add(f"- Raw replies: `{r['raw_a']}` and `{r['raw_b']}` — re-grade from these "
        "before believing any single row.")
    return "\n".join(L) + "\n"
