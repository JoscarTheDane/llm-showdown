"""llm-showdown CLI.

    python -m bench.cli list
    python -m bench.cli suite   --model qwen38-27b-q4kxl            # swap + bench + restore
    python -m bench.cli suite   --model X --no-swap --url http://127.0.0.1:8095/v1
    python -m bench.cli speed   --model qwen38-27b-q4kxl
    python -m bench.cli showdown --a qwen38-27b-q4kxl --b swift-27b-nvfp4
    python -m bench.cli report  --a summary_A.json --b summary_B.json
    python -m bench.cli selftest                                    # graders only, no model
    python -m bench.cli recover                                     # undo a crashed swap
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report as reportmod
from . import runner, speed as speedmod, swap


def _registry(args) -> dict:
    return swap.load_registry(getattr(args, "registry", "models.yaml"))


def cmd_list(args) -> int:
    reg = _registry(args)
    print(f"service: {reg.get('service')}   incumbent port: {reg.get('incumbent_port')}")
    print(f"bench port: {reg.get('bench_port')}\n")
    for name, cfg in reg["models"].items():
        path = Path(cfg["gguf"])
        exists = "present" if path.exists() else "MISSING"
        size = f"{path.stat().st_size / 1e9:.2f} GB" if path.exists() else "-"
        print(f"  {name:<28} {exists:<8} {size:<10} {cfg.get('label', '')}")
    print("\nsudo -n available:", swap.sudo_available())
    free = swap.gpu_free_mib()
    print(f"gpu free: {free} MiB" if free is not None else "gpu: nvidia-smi unavailable")
    inc = swap.incumbent_model(reg.get("service", "llama-server"))
    print(f"incumbent (from unit): {inc}")
    return 0


def cmd_suite(args) -> int:
    reg = _registry(args)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    mgr = None
    try:
        if args.no_swap:
            url, model = args.url, args.model
            print(f"[suite] no-swap mode: benching whatever serves {url}")
        else:
            mgr = swap.ModelManager(reg, service=reg.get("service", "llama-server"),
                                    dry_run=args.dry_run)
            info = mgr.stand_up(args.model)
            print(f"[suite] stood up: {json.dumps(info, indent=2)}")
            if args.dry_run:
                return 0
            url = f"http://127.0.0.1:{info['port']}/v1"
            model = reg["models"][args.model]["gguf"]
        summary = runner.run_suite(
            model=model, url=url, include_long=not args.no_long,
            long_words=args.long_words, only=args.only or None,
            outdir=str(outdir), label=args.label or args.model,
            warmup=not args.no_warmup, timeout=args.timeout,
        )
        runner.print_summary(summary)
        print(f"\nsummary -> {outdir}/summary_{args.label or args.model}.json")
        print(f"raw     -> {summary['raw_log']}")
        return 0
    finally:
        if mgr:
            restored = mgr.restore()
            print(f"[suite] box restored: {restored}")


def cmd_speed(args) -> int:
    reg = _registry(args)
    mgr = None
    try:
        if args.no_swap:
            url, model = args.url, args.model
        else:
            mgr = swap.ModelManager(reg, service=reg.get("service", "llama-server"),
                                    dry_run=args.dry_run)
            info = mgr.stand_up(args.model)
            if args.dry_run:
                return 0
            url = f"http://127.0.0.1:{info['port']}/v1"
            model = reg["models"][args.model]["gguf"]
        res = speedmod.run_speed(url, model,
                                 depths=[int(d) for d in args.depths.split(",")],
                                 repeats=args.repeats)
        out = Path(args.outdir) / f"speed_{args.label or args.model}.json"
        out.write_text(json.dumps(res, indent=2))
        print(f"\nspeed -> {out}")
        return 0
    finally:
        if mgr:
            print(f"[speed] box restored: {mgr.restore()}")


def cmd_showdown(args) -> int:
    """Full stand-off: bench A, hand the card over, bench B, restore, compare."""
    reg = _registry(args)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    mgr = swap.ModelManager(reg, service=reg.get("service", "llama-server"),
                            dry_run=args.dry_run)
    summaries = {}
    try:
        for name in (args.a, args.b):
            info = mgr.stand_up(name)
            if args.dry_run:
                continue
            url = f"http://127.0.0.1:{info['port']}/v1"
            model = reg["models"][name]["gguf"]
            print(f"\n########## {name} ##########")
            summaries[name] = runner.run_suite(
                model=model, url=url, include_long=not args.no_long,
                long_words=args.long_words, outdir=str(outdir), label=name,
                warmup=not args.no_warmup, timeout=args.timeout)
            runner.print_summary(summaries[name])
            mgr.tear_down()
        if args.dry_run:
            return 0
        res = reportmod.head_to_head(
            summaries[args.a], summaries[args.b],
            out_md=str(outdir / f"showdown_{args.a}_vs_{args.b}.md"))
        print("\n" + "=" * 64)
        print(f"VERDICT: {res['verdict']}   "
              f"({args.a} {res['a']['score']['score']} vs "
              f"{args.b} {res['b']['score']['score']})")
        print("=" * 64)
        for row in res["classes"]:
            print(f"  {row['class']:<11} A {row['a_pass']:<7} B {row['b_pass']:<7} "
                  f"-> {row['winner']}")
        print(f"\nreport -> {outdir}/showdown_{args.a}_vs_{args.b}.md")
        return 0
    finally:
        print(f"[showdown] box restored: {mgr.restore()}")


def cmd_report(args) -> int:
    res = reportmod.head_to_head(args.a, args.b,
                                 out_md=args.out or "showdown.md",
                                 label_a=args.label_a, label_b=args.label_b)
    print(json.dumps({"verdict": res["verdict"],
                      "a_score": res["a"]["score"]["score"],
                      "b_score": res["b"]["score"]["score"]}, indent=2))
    return 0


def cmd_selftest(args) -> int:
    from . import selftest
    return selftest.main()


def cmd_recover(args) -> int:
    reg = _registry(args)
    ok = swap.recover_if_needed(reg, dry_run=args.dry_run)
    print("recovered" if ok else "nothing to recover")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--registry", default="models.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="show the registry and hardware state")
    s.set_defaults(func=cmd_list)

    def common(sp):
        sp.add_argument("--model", required=True)
        sp.add_argument("--url", default=runner.DEFAULT_URL)
        sp.add_argument("--no-swap", action="store_true",
                        help="bench an already-running endpoint (no root needed)")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--outdir", default="runs")
        sp.add_argument("--label", default=None)
        sp.add_argument("--timeout", type=int, default=900)

    s = sub.add_parser("suite", help="run the capability battery against one model")
    common(s)
    s.add_argument("--only", nargs="*", help="subset: class names or task ids")
    s.add_argument("--no-long", action="store_true", help="skip the long-context task")
    s.add_argument("--long-words", type=int, default=24000)
    s.add_argument("--no-warmup", action="store_true")
    s.set_defaults(func=cmd_suite)

    s = sub.add_parser("speed", help="prefill / TTFT / decode at context depth")
    common(s)
    s.add_argument("--depths", default="512,8192,32768,65536")
    s.add_argument("--repeats", type=int, default=3)
    s.set_defaults(func=cmd_speed)

    s = sub.add_parser("showdown", help="1v1: swap, bench both, restore, compare")
    s.add_argument("--a", required=True)
    s.add_argument("--b", required=True)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--outdir", default="runs")
    s.add_argument("--no-long", action="store_true")
    s.add_argument("--long-words", type=int, default=24000)
    s.add_argument("--no-warmup", action="store_true")
    s.add_argument("--timeout", type=int, default=900)
    s.set_defaults(func=cmd_showdown)

    s = sub.add_parser("report", help="compare two saved summaries (no GPU)")
    s.add_argument("--a", required=True)
    s.add_argument("--b", required=True)
    s.add_argument("--label-a", default=None)
    s.add_argument("--label-b", default=None)
    s.add_argument("--out", default="showdown.md")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("selftest", help="validate every grader offline")
    s.set_defaults(func=cmd_selftest)

    s = sub.add_parser("recover", help="restore the box after a crashed swap")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_recover)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
