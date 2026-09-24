"""Speed rig: prefill, TTFT and decode measured properly.

Methodology carried over from hard-won practice:
  * time.monotonic() ONLY — wall-clock time drifts (notably on WSL) and produces
    negative TTFT.
  * warm the model before the first real measurement; the first request pays the
    weight-load penalty.
  * repeat every measurement and report the median, not a single run.
  * decode rate degrades as context grows — measure at depth, not at zero.

Two measurement paths:
  * streaming (default): real time-to-first-token from the SSE stream, decode
    rate from inter-token arrival times.
  * timings (fallback): llama.cpp reports prompt_per_second / predicted_per_second
    in the response body when streaming is unavailable.
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.request

UA = "llm-showdown/1.0 (speed rig)"


def _filler(words: int) -> str:
    block = ("Maintain the operations log, record weather downtime separately "
             "from standby, and confirm the dive spread is certified before "
             "each deployment. ")
    return (block * (words // 20 + 1))[: words * 6]


def make_prompt(depth_tokens: int) -> str:
    """A prompt of roughly `depth_tokens` tokens (4 chars/token heuristic)."""
    if depth_tokens <= 64:
        return "Reply with the single word: ready"
    # 0.75 words per token
    body = _filler(int(depth_tokens * 0.75))
    return (f"Here is a long operations document:\n\n{body}\n\n"
            "Answer with one short sentence: what must be confirmed before each "
            "deployment?")


def measure_once(url: str, model: str, depth_tokens: int, max_tokens: int = 192,
                 timeout: int = 900, stream: bool = True) -> dict:
    """One streaming measurement at a given context depth."""
    prompt = make_prompt(depth_tokens)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "stream": stream,
        "stream_options": {"include_usage": True},
    }
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA},
    )
    t0 = time.monotonic()
    ttft = None
    token_times: list[float] = []
    usage: dict = {}
    text = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not stream:
                payload = json.loads(resp.read().decode())
                total = time.monotonic() - t0
                usage = payload.get("usage") or {}
                timings = payload.get("timings") or {}
                txt = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                return {"ok": True, "depth": depth_tokens, "ttft_s": None,
                        "total_s": total, "decode_tps": timings.get("predicted_per_second"),
                        "prefill_tps": timings.get("prompt_per_second"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "prompt_tokens": usage.get("prompt_tokens"), "text": txt[:80]}
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if obj.get("usage"):
                    usage = obj["usage"]
                for ch in obj.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        now = time.monotonic()
                        if ttft is None:
                            ttft = now - t0
                        token_times.append(now)
                        text += piece
    except urllib.error.HTTPError as exc:
        return {"ok": False, "depth": depth_tokens,
                "error": f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}"}
    except Exception as exc:
        return {"ok": False, "depth": depth_tokens, "error": f"{type(exc).__name__}: {exc}"}

    total = time.monotonic() - t0
    decode_tps = None
    if len(token_times) > 2:
        span = token_times[-1] - token_times[0]
        decode_tps = round((len(token_times) - 1) / span, 2) if span > 0 else None
    prompt_tokens = usage.get("prompt_tokens")
    prefill_tps = None
    if prompt_tokens and ttft and ttft > 0:
        prefill_tps = round(prompt_tokens / ttft, 1)
    return {"ok": True, "depth": depth_tokens, "ttft_s": round(ttft, 3) if ttft else None,
            "total_s": round(total, 2), "decode_tps": decode_tps,
            "prefill_tps": prefill_tps, "streamed_tokens": len(token_times),
            "completion_tokens": usage.get("completion_tokens"),
            "prompt_tokens": prompt_tokens, "text": text[:80]}


def run_speed(url: str, model: str, depths: list[int] | None = None,
              repeats: int = 3, max_tokens: int = 192) -> dict:
    depths = depths or [512, 8192, 32768, 65536]
    print(f"[speed] {model} @ {url}  depths={depths} repeats={repeats}")
    # warm-up: pay the weight-load penalty once, outside the measurements
    warm = measure_once(url, model, 64, max_tokens=32)
    print(f"  warm-up: {'ok' if warm.get('ok') else warm.get('error')}")

    rows = []
    for depth in depths:
        runs = []
        for i in range(repeats):
            r = measure_once(url, model, depth, max_tokens=max_tokens)
            if r.get("ok"):
                runs.append(r)
                print(f"  depth {depth:>6}  run {i+1}/{repeats}  "
                      f"ttft={r['ttft_s']}s  decode={r['decode_tps']} tok/s  "
                      f"prompt_tokens={r['prompt_tokens']}")
            else:
                print(f"  depth {depth:>6}  run {i+1}/{repeats}  FAILED: {r.get('error')}")
            time.sleep(1)  # let the server settle between runs
        if runs:
            rows.append({
                "depth_requested": depth,
                "prompt_tokens": statistics.median(
                    [r["prompt_tokens"] for r in runs if r.get("prompt_tokens")] or [0]),
                "ttft_s": round(statistics.median([r["ttft_s"] for r in runs
                                                   if r.get("ttft_s")]), 3),
                "decode_tps": round(statistics.median([r["decode_tps"] for r in runs
                                                      if r.get("decode_tps")]), 2),
                "prefill_tps": round(statistics.median([r["prefill_tps"] for r in runs
                                                       if r.get("prefill_tps")]), 1),
                "runs": len(runs),
            })
    return {"model": model, "url": url, "generated_utc":
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": rows}
