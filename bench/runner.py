"""Suite runner: drives the battery against an OpenAI-compatible endpoint.

Captures, for every single call:
  * the full model reply (so the verdict is re-gradable without re-running the model)
  * usage / reasoning-token shape (output budget is dominated by thinking on
    reasoning models — that is the cost line that matters)
  * wall time and time-to-first-token (monotonic clock only; wall-clock time
    drifts on WSL and produces negative TTFT)
  * the grade, the grader's reason, and whether code actually executed
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import graders, tasks as taskmod

UA = "llm-showdown/1.0 (+local benchmark harness)"
DEFAULT_URL = "http://127.0.0.1:8080/v1"


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #

def call(url: str, model: str, prompt: str, temperature: float,
         max_tokens: int, timeout: int = 600) -> dict:
    """One completion. Never raises: transport failure is returned as a record."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        return {"ok": False, "error": f"HTTP {exc.code}: {detail}",
                "wall_s": time.monotonic() - t0}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "wall_s": time.monotonic() - t0}

    wall = time.monotonic() - t0
    try:
        choice = payload["choices"][0]
        text = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
    except (KeyError, IndexError):
        return {"ok": False, "error": f"malformed response: {str(payload)[:200]}",
                "wall_s": wall}

    usage = payload.get("usage") or {}
    # reasoning-token shapes differ per server; collect whatever is present
    reasoning = 0
    for key in ("reasoning_tokens", "completion_tokens_details"):
        val = usage.get(key)
        if isinstance(val, int):
            reasoning = val
        elif isinstance(val, dict):
            reasoning = val.get("reasoning_tokens", 0) or 0
    timings = payload.get("timings") or {}

    empty_but_truncated = (not text.strip()) and finish == "length"
    return {
        "ok": True,
        "text": text,
        "finish_reason": finish,
        "wall_s": wall,
        "usage": usage,
        "reasoning_tokens": reasoning,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "predicted_per_second": timings.get("predicted_per_second"),
        "prompt_per_second": timings.get("prompt_per_second"),
        "empty_truncated": empty_but_truncated,
    }


def health(url: str, timeout: int = 10) -> tuple[bool, str]:
    req = urllib.request.Request(url.rstrip("/") + "/models",
                                headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        ids = [m.get("id") or m.get("name") for m in data.get("data", [])]
        if not ids:
            ids = [m.get("name") for m in data.get("models", [])]
        return True, (ids[0] if ids else "unknown")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Code execution (codegen tasks are graded by RUNNING the artifact)
# --------------------------------------------------------------------------- #

def _extract_code(text: str) -> str:
    """Pull the script out of the RAW reply.

    Deliberately does NOT use graders.norm(): norm collapses runs of spaces
    (correct for prose matching) which destroys Python indentation and turns
    every generated script into an IndentationError. The whole codegen class
    silently scored zero until this was caught.
    """
    m = re.search(r"```(?:python|py)?[ \t]*\r?\n([\s\S]*?)```", text)
    if m:
        code = m.group(1)
        # if the model indented the whole block, remove the common indent
        return textwrap.dedent(code) if code.strip() else ""
    # no fence: accept the reply if it looks like a script end to end
    if re.search(r"^\s*(def |import |from |print\()", text, re.M):
        return text
    return ""


def execute_code(task: dict, reply: str, timeout: int = 60) -> dict:
    """Run the generated script in a throwaway dir with the task's fixtures."""
    code = _extract_code(reply)
    if not code.strip():
        return {"ok": False, "error": "no code block found in reply"}
    workdir = tempfile.mkdtemp(prefix="showdown_code_")
    try:
        for name, content in (task.get("fixtures") or {}).items():
            (Path(workdir) / name).write_text(content)
        script = Path(workdir) / task.get("run_file", "gen.py")
        script.write_text(code)
        proc = subprocess.run(
            task.get("run_cmd", ["python3", "gen.py"]),
            cwd=workdir, capture_output=True, text=True, timeout=timeout,
        )
        artifacts = {}
        for f in sorted(Path(workdir).iterdir()):
            if f.is_file() and f.name not in (task.get("fixtures") or {}) and f.name != script.name:
                try:
                    artifacts[f.name] = f.read_text()
                except Exception:
                    artifacts[f.name] = "<binary>"
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr[-500:],
            "artifacts": artifacts,
            "error": "" if proc.returncode == 0 else f"exit {proc.returncode}: {proc.stderr[-200:]}",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Suite execution
# --------------------------------------------------------------------------- #

TEMPS = [0.2, 0.7, 1.0]

# A reasoning model emits its thinking FIRST and the thinking is billed as
# output. With a small budget the reply comes back EMPTY with
# finish_reason=length — that is a harness error, not a low score, so the
# budget is escalated and retried before anything is graded.
MIN_OUTPUT_BUDGET = 3000
MAX_OUTPUT_BUDGET = 32768


def call_with_budget_escalation(url: str, model: str, prompt: str, temperature: float,
                                budget: int, timeout: int) -> dict:
    """Call, escalating the output budget while the reply is empty-and-truncated."""
    asked = max(budget, MIN_OUTPUT_BUDGET)
    escalations = []
    res = call(url, model, prompt, temperature, asked, timeout=timeout)
    while res.get("ok") and res.get("empty_truncated") and asked < MAX_OUTPUT_BUDGET:
        asked = min(asked * 4, MAX_OUTPUT_BUDGET)
        escalations.append(asked)
        res = call(url, model, prompt, temperature, asked, timeout=timeout)
    res["budget_asked"] = asked
    res["budget_escalations"] = escalations
    return res


def run_suite(model: str, url: str = DEFAULT_URL, include_long: bool = True,
              long_words: int = 24000, trials: int | None = None,
              temps: list[float] | None = None, only: list[str] | None = None,
              outdir: str = ".", label: str | None = None,
              warmup: bool = True, timeout: int = 600) -> dict:
    """Run every task `trials` times across the temperature ladder. Returns summary."""
    tasks = taskmod.all_tasks(include_long=include_long, long_words=long_words)
    if only:
        wanted = set(only)
        tasks = [t for t in tasks if t["class"] in wanted or t["id"] in wanted]
    temps = temps or TEMPS
    label = label or re.sub(r"\W+", "_", model.split("/")[-1])[:60]
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    raw_path = outdir / f"raw_{label}.jsonl"

    ok, served = health(url)
    if not ok:
        raise SystemExit(f"endpoint not healthy at {url}: {served}")

    if warmup:
        call(url, model, "Reply with the single word: ready", 0.0, 16, timeout=300)

    records = []
    per_task: dict[str, dict] = {}
    with raw_path.open("w", encoding="utf-8") as fh:
        for task in tasks:
            n = trials or task.get("trials", 3)
            results = []
            for i in range(n):
                temp = temps[i % len(temps)]
                res = call_with_budget_escalation(
                    url, model, task["prompt"], temp,
                    task.get("max_tokens", 2000), timeout=timeout)
                execinfo = None
                if res.get("ok") and task["grader"] == "codegen_output":
                    execinfo = execute_code(task, res["text"])
                    task["_exec"] = execinfo
                record = {
                    "task": task["id"], "class": task["class"], "trial": i, "temp": temp,
                    "grader": task["grader"], "model": model, "served": served,
                    "reply": res.get("text", ""), "ok": res.get("ok", False),
                    "error": res.get("error"),
                    "wall_s": res.get("wall_s"),
                    "completion_tokens": res.get("completion_tokens"),
                    "reasoning_tokens": res.get("reasoning_tokens"),
                    "prompt_tokens": res.get("prompt_tokens"),
                    "finish_reason": res.get("finish_reason"),
                    "empty_truncated": res.get("empty_truncated", False),
                    "budget_asked": res.get("budget_asked"),
                    "budget_escalations": res.get("budget_escalations"),
                    "predicted_per_second": res.get("predicted_per_second"),
                    "code_exec": execinfo,
                }
                if res.get("ok"):
                    passed, why = graders.grade(task, res["text"])
                else:
                    passed, why = False, f"transport: {res.get('error')}"
                record["passed"], record["reason"] = passed, why
                results.append(record)
                records.append(record)
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                flag = "PASS" if passed else "FAIL"
                print(f"  [{flag}] {task['id']} t{i} temp={temp} "
                      f"({record['wall_s']:.1f}s) {why[:90]}", flush=True)

            agg = per_task.setdefault(task["id"], {
                "id": task["id"], "class": task["class"], "trials": 0, "passed": 0,
                "wall_s": [], "completion_tokens": [], "reasoning_tokens": [],
                "reasons": [],
            })
            agg["trials"] += len(results)
            agg["passed"] += sum(1 for r in results if r["passed"])
            agg["wall_s"] += [r["wall_s"] for r in results if r.get("wall_s")]
            agg["completion_tokens"] += [r["completion_tokens"] or 0 for r in results]
            agg["reasoning_tokens"] += [r["reasoning_tokens"] or 0 for r in results]
            agg["reasons"] += [r["reason"] for r in results if not r["passed"]]

    # ---- aggregate by class ----
    by_class: dict[str, dict] = {}
    for t in per_task.values():
        c = by_class.setdefault(t["class"], {"class": t["class"], "trials": 0, "passed": 0,
                                             "wall_s": [], "completion_tokens": [],
                                             "reasoning_tokens": []})
        c["trials"] += t["trials"]
        c["passed"] += t["passed"]
        c["wall_s"] += t["wall_s"]
        c["completion_tokens"] += t["completion_tokens"]
        c["reasoning_tokens"] += t["reasoning_tokens"]

    def med(xs):
        xs = sorted(x for x in xs if x is not None)
        return xs[len(xs) // 2] if xs else 0.0

    for c in by_class.values():
        c["pass_rate"] = round(c["passed"] / c["trials"], 3) if c["trials"] else 0.0
        c["median_wall_s"] = round(med(c["wall_s"]), 2)
        c["median_completion_tokens"] = med(c["completion_tokens"])
        c["median_reasoning_tokens"] = med(c["reasoning_tokens"])

    total_trials = sum(t["trials"] for t in per_task.values())
    total_passed = sum(t["passed"] for t in per_task.values())
    all_wall = [r["wall_s"] for r in records if r.get("wall_s")]
    correct_tokens = [r["completion_tokens"] or 0 for r in records if r["passed"]]
    correct_wall = [r["wall_s"] for r in records if r["passed"] and r.get("wall_s")]
    correct_reasoning = [r["reasoning_tokens"] or 0 for r in records if r["passed"]]

    summary = {
        "label": label, "model": model, "served": served, "url": url,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "totals": {
            "trials": total_trials, "passed": total_passed,
            "pass_rate": round(total_passed / total_trials, 3) if total_trials else 0.0,
            "median_wall_s": round(med(all_wall), 2),
            "p90_wall_s": round(sorted(all_wall)[int(0.9 * len(all_wall))], 2) if all_wall else 0.0,
            "max_wall_s": round(max(all_wall), 2) if all_wall else 0.0,
            "median_completion_tokens": med([r["completion_tokens"] or 0 for r in records]),
            "median_reasoning_tokens": med([r["reasoning_tokens"] or 0 for r in records]),
            # the trade that matters for a local agent: cost of a CORRECT answer
            "median_completion_tokens_per_correct": med(correct_tokens),
            "median_reasoning_tokens_per_correct": med(correct_reasoning),
            "median_wall_s_per_correct": round(med(correct_wall), 2),
            "transport_failures": sum(1 for r in records if not r.get("ok")),
            "empty_truncated": sum(1 for r in records if r.get("empty_truncated")),
        },
        "by_class": sorted(by_class.values(), key=lambda c: c["class"]),
        "by_task": sorted(per_task.values(), key=lambda t: t["id"]),
        "raw_log": str(raw_path),
    }
    (outdir / f"summary_{label}.json").write_text(json.dumps(summary, indent=2))
    return summary


def print_summary(s: dict) -> None:
    t = s["totals"]
    print(f"\n=== {s['label']}  (served: {s['served']}) ===")
    print(f"trials {t['trials']}  passed {t['passed']}  pass-rate {t['pass_rate']:.1%}")
    print(f"wall median {t['median_wall_s']}s  p90 {t['p90_wall_s']}s  max {t['max_wall_s']}s")
    print(f"median tokens/correct answer: {t['median_completion_tokens_per_correct']} "
          f"(reasoning {t['median_reasoning_tokens_per_correct']})")
    if t["transport_failures"] or t["empty_truncated"]:
        print(f"!! transport failures {t['transport_failures']}  "
              f"empty-but-truncated {t['empty_truncated']}")
    print("\nper class:")
    for c in s["by_class"]:
        print(f"  {c['class']:<11} {c['passed']}/{c['trials']}  "
              f"({c['pass_rate']:.0%})  median {c['median_wall_s']}s  "
              f"tokens {c['median_completion_tokens']}")
