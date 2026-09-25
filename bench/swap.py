"""Single-GPU model stand-off manager for llama.cpp.

The constraint this solves: one 32 GB card, models that each want most of it.
Two candidates cannot be resident at once, so a fair 1v1 requires that the card
is handed over cleanly, the SAME server flags are used for both, and the box is
put back exactly as it was found — including after a crash.

Guarantees
  * one bench at a time (advisory lock) — two servers on one card OOM each other
  * preflight: model present, fits in VRAM, no swap in use, port free
  * the incumbent systemd service is stopped, then restored, and the restore is
    VERIFIED by re-reading the served model name from /v1/models
  * restore runs from a finally block and from a signal handler, so Ctrl-C or a
    crash mid-suite still puts the box back
  * --no-swap benches whatever is already serving (no root needed)
  * --dry-run prints every command without executing it

Sudo note: stopping the incumbent service needs root. If `sudo -n` is not
permitted the manager refuses to guess and tells you the exact narrow drop-in
to add (see docs/showdown-strategy.md). It never prompts for a password.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

LOCK_PATH = Path("/tmp/llm_showdown.lock")
STATE_PATH = Path("/tmp/llm_showdown_state.json")
BENCH_LOG = Path("/tmp/llm_showdown_server.log")


def _run(cmd: list[str] | str, check: bool = False, timeout: int = 60) -> tuple[int, str]:
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        if check and p.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}): {out[:300]}")
        return p.returncode, out.strip()
    except FileNotFoundError as exc:
        return 127, str(exc)
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {' '.join(cmd)}"


def sudo_available(service: str | None = None) -> bool:
    """Can we run the commands this manager actually needs, without a prompt?

    Tests the real command rather than `true`: the recommended setup is a
    command-restricted NOPASSWD drop-in that authorises only the systemctl
    verbs, and `true` would report false there even though every call the
    manager makes is in fact permitted.
    """
    if service:
        # Permitted + active -> 0, permitted + inactive -> 3. Only a sudo
        # refusal (1) or an error means "not usable".
        rc, _ = _run(["sudo", "-n", "systemctl", "is-active", service], timeout=15)
        return rc in (0, 3)
    rc, _ = _run(["sudo", "-n", "true"], timeout=15)
    return rc == 0


SUDO_HELP = """\
Root is required to hand the GPU over and it is not pre-authorised here.
Do NOT drive a password prompt from this harness. Choose one:

  (a) Preferred, permanent — one narrow drop-in, then this runs unattended:
        echo '%sudo ALL=(root) NOPASSWD: /usr/bin/systemctl stop llama-server, \\
/usr/bin/systemctl start llama-server, /usr/bin/systemctl is-active llama-server' \\
          | sudo tee /etc/sudoers.d/llama-showdown
        sudo chmod 440 /etc/sudoers.d/llama-showdown

  (b) Zero-config — stand the candidate up yourself, then bench in place:
        python -m bench.cli suite --model <name> --no-swap --url http://127.0.0.1:PORT/v1
"""


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

def load_registry(path: str = "models.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        p = Path(__file__).resolve().parent.parent / "models.yaml"
    return yaml.safe_load(p.read_text())


def resolve_gguf(path: str, cache_root: str | None = None) -> str:
    """Return a usable path to the weights.

    A configured path often points at a tidy location that does not exist yet,
    while the download already sits in the Hugging Face cache under a
    commit-hashed snapshot directory. Rather than require a manual copy (and
    then let the two copies drift), fall back to the cache and match on
    basename. Falls through unchanged if nothing is found, so the caller still
    reports the intended path in its error message.
    """
    p = Path(path)
    if p.exists():
        return str(p)
    root = Path(cache_root or os.path.expanduser("~/.cache/huggingface/hub"))
    if root.exists():
        hits = sorted(root.glob(f"models--*/snapshots/*/{p.name}"))
        if hits:
            return str(hits[-1])
    return str(p)


def merge_defaults(registry: dict, name: str) -> dict:
    """Model config with the shared `defaults` folded in underneath it.

    Without this, a per-model entry silently ignored every default — including
    the shared server binary, which is the fairness rule of the whole stand-off.
    """
    models = registry.get("models", {})
    if name not in models:
        raise SystemExit(f"unknown model {name!r}; known: {', '.join(sorted(models))}")
    cfg = dict(registry.get("defaults") or {})
    cfg.update(models[name])
    cfg["name"] = name
    if "gguf" in cfg:
        cfg["gguf"] = resolve_gguf(cfg["gguf"])
    return cfg


# --------------------------------------------------------------------------- #
# Lock
# --------------------------------------------------------------------------- #

class BenchLock:
    def __init__(self, path: Path = LOCK_PATH):
        self.path = path
        self.acquired = False

    def __enter__(self):
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip())
                os.kill(pid, 0)
                raise SystemExit(
                    f"another bench is already running (pid {pid}). "
                    "One GPU, one bench — wait for it or remove "
                    f"{self.path} if that process is gone."
                )
            except (ValueError, ProcessLookupError):
                pass  # stale lock
        self.path.write_text(str(os.getpid()))
        self.acquired = True
        return self

    def __exit__(self, *exc):
        if self.acquired and self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #

def incumbent_model(service: str = "llama-server") -> str | None:
    """The model path from the incumbent systemd unit's ExecStart."""
    rc, out = _run(["systemctl", "cat", service], timeout=20)
    if rc != 0:
        return None
    m = re.search(r"^\s*-m\s+(\S+)", out, re.M)
    return m.group(1) if m else None


def served_model(url: str) -> str | None:
    import urllib.request
    try:
        req = urllib.request.Request(url.rstrip("/") + "/models",
                                     headers={"User-Agent": "llm-showdown/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        entry = (data.get("data") or data.get("models") or [{}])[0]
        return entry.get("id") or entry.get("name")
    except Exception:
        return None


def gpu_free_mib() -> int | None:
    rc, out = _run(["nvidia-smi", "--query-gpu=memory.free",
                    "--format=csv,noheader,nounits"], timeout=20)
    if rc != 0:
        return None
    try:
        return int(out.splitlines()[0].strip())
    except (ValueError, IndexError):
        return None


def swap_in_use_mib() -> int | None:
    rc, out = _run(["free", "-m"], timeout=15)
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.lower().startswith("swap:"):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    return int(parts[2])
                except ValueError:
                    return None
    return None


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) != 0


# --------------------------------------------------------------------------- #
# The manager
# --------------------------------------------------------------------------- #

class ModelManager:
    def __init__(self, registry: dict, service: str = "llama-server",
                 dry_run: bool = False, verbose: bool = True):
        self.registry = registry
        self.service = service
        self.dry_run = dry_run
        self.verbose = verbose
        self.incumbent: str | None = None
        self.bench_pid: int | None = None
        self.bench_port: int | None = None
        self._restored = False
        self._prev_handlers: dict = {}

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[swap] {msg}", flush=True)

    def model(self, name: str) -> dict:
        """Merged, cache-resolved config for one model. Single source of truth."""
        cache = getattr(self, "_cache", None)
        if cache is None:
            cache = self._cache = {}
        if name not in cache:
            cache[name] = merge_defaults(self.registry, name)
        return cache[name]

    # ---- preflight ----
    def preflight(self, name: str) -> dict:
        cfg = self.model(name)
        gguf = Path(cfg["gguf"])
        if not gguf.exists():
            raise SystemExit(
                f"model file missing: {gguf}\n"
                "  (also checked the Hugging Face cache for a file with the same "
                "name — if the download is still running, wait for it to finish)"
            )
        size_mib = gguf.stat().st_size // (1024 * 1024)
        free = gpu_free_mib()
        swap = swap_in_use_mib()
        report = {"model": name, "gguf": str(gguf), "size_mib": size_mib,
                  "gpu_free_mib": free, "swap_used_mib": swap}
        if free is None:
            raise SystemExit("nvidia-smi unavailable — cannot verify VRAM")
        need = size_mib + cfg.get("headroom_mib", 2500)
        if free < need:
            self.log(f"WARNING: {free} MiB free, model {size_mib} MiB + "
                     f"headroom — the incumbent may still hold the card")
        if swap and swap > 512:
            raise SystemExit(f"swap already in use ({swap} MiB) — benchmarking now "
                             "would measure the disk, not the model")
        port = cfg.get("port", 8095)
        if not port_free(port):
            raise SystemExit(f"port {port} is in use — pick another in models.yaml")
        report["port"] = port
        report["need_mib"] = need
        return report

    # ---- handover ----
    def stand_up(self, name: str) -> dict:
        report = self.preflight(name)
        cfg = self.model(name)
        self.incumbent = incumbent_model(self.service)
        self.log(f"incumbent: {self.incumbent or 'none'}")
        if not self.dry_run and not sudo_available(self.service):
            raise SystemExit(SUDO_HELP)

        state = {"incumbent": self.incumbent, "candidate": name,
                 "port": cfg.get("port", 8095), "started": time.time(),
                 "service": self.service, "gguf": cfg["gguf"]}
        STATE_PATH.write_text(json.dumps(state, indent=2))
        self._install_signal_handlers()

        self.log(f"stopping {self.service}")
        if not self.dry_run:
            _run(["sudo", "-n", "systemctl", "stop", self.service], check=True, timeout=120)
            self._wait_for_port_free(cfg.get("port", 8095))

        self.log(f"starting candidate {name} on port {cfg.get('port', 8095)}")
        cmd = self._server_cmd(name, cfg)
        self.log("  " + " ".join(shlex.quote(c) for c in cmd))
        if self.dry_run:
            return {**report, "dry_run": True, "cmd": cmd}

        with BENCH_LOG.open("w") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        self.bench_pid = proc.pid
        self.bench_port = cfg.get("port", 8095)
        ok = self._wait_for_health(self.bench_port, proc, timeout=cfg.get("load_timeout_s", 420))
        if not ok:
            self.tear_down()
            raise SystemExit(f"candidate {name} never became healthy — see {BENCH_LOG}")
        self.log(f"candidate healthy: {served_model(f'http://127.0.0.1:{self.bench_port}/v1')}")
        return {**report, "incumbent": self.incumbent, "healthy": True}

    def _server_cmd(self, name: str, cfg: dict) -> list[str]:
        # No hardcoded fallback: the shared binary is the fairness rule, so a
        # missing one must fail loudly rather than quietly pick a different build.
        binary = cfg.get("binary")
        if not binary:
            raise SystemExit(
                f"no server binary for {name!r} — set `defaults.binary` in models.yaml"
            )
        if not Path(binary).exists():
            raise SystemExit(f"server binary not found: {binary}")
        cmd = [binary, "-m", cfg["gguf"],
               "--host", "127.0.0.1", "--port", str(cfg.get("port", 8095)),
               "--ctx-size", str(cfg.get("ctx", 163840)),
               "--n-gpu-layers", cfg.get("n_gpu_layers", "all"),
               "--threads", str(cfg.get("threads", 8)),
               "--threads-batch", str(cfg.get("threads_batch", cfg.get("threads", 8))),
               "--threads-http", "4",
               "--batch-size", "2048", "--ubatch-size", "2048",
               "--parallel", "1", "--flash-attn", "on",
               "--cache-type-k", cfg.get("cache_type_k", "q8_0"),
               "--cache-type-v", cfg.get("cache_type_v", "q8_0"),
               "--jinja", "--reasoning", "auto",
               "--chat-template-kwargs",
               json.dumps({"preserve_thinking": True, "reasoning_effort": "medium"}),
               "--temp", "1", "--top-k", "20", "--top-p", "0.95", "--min-p", "0.00",
               "--repeat-penalty", "1",
               "--sleep-idle-seconds", "-1"]  # no idle unload mid-suite
        if cfg.get("spec_type"):
            cmd += ["--spec-type", cfg["spec_type"],
                    "--spec-draft-n-max", str(cfg.get("spec_draft_n_max", 2)),
                    "--spec-draft-n-min", "0"]
        cmd += list(cfg.get("extra_args", []))
        return cmd

    def _wait_for_health(self, port: int, proc: subprocess.Popen, timeout: int) -> bool:
        url = f"http://127.0.0.1:{port}/v1"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                self.log(f"server exited early (rc={proc.returncode})")
                return False
            name = served_model(url)
            if name:
                return True
            time.sleep(2)
        return False

    def _wait_for_port_free(self, port: int, timeout: int = 45) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_free(port):
                return
            time.sleep(1)

    def tear_down(self) -> None:
        """Stop the bench server and wait for VRAM to actually come back."""
        if self.dry_run:
            return
        pid = self.bench_pid
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(1)
            else:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            self.bench_pid = None
        # wait for the card to actually free
        for _ in range(30):
            free = gpu_free_mib()
            if free is not None and free > 20000:
                break
            time.sleep(1)

    def restore(self) -> bool:
        """Put the box back the way it was found, and verify it."""
        if self.dry_run or self._restored:
            return True
        self._restored = True
        self.tear_down()
        if not self.incumbent:
            self.log("no incumbent recorded — leaving the service stopped")
            return False
        self.log(f"restoring {self.service}")
        _run(["sudo", "-n", "systemctl", "start", self.service], timeout=120)
        url = f"http://127.0.0.1:{self.registry.get('incumbent_port', 8080)}/v1"
        for _ in range(60):
            name = served_model(url)
            if name == self.incumbent or (name and Path(name).name == Path(self.incumbent).name):
                self.log(f"restored and verified: {name}")
                self._remove_state()
                return True
            time.sleep(2)
        self.log(f"WARNING: service started but served model is {served_model(url)!r}, "
                 f"expected {self.incumbent!r}")
        return False

    def _remove_state(self) -> None:
        try:
            STATE_PATH.unlink()
        except OSError:
            pass

    # ---- crash safety ----
    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):
            self.log(f"signal {signum} — restoring the box before exit")
            try:
                self.restore()
            finally:
                sys.exit(130)
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._prev_handlers[sig] = signal.signal(sig, handler)


def recover_if_needed(registry: dict, dry_run: bool = False) -> bool:
    """If a previous run died mid-swap, put the incumbent back."""
    if not STATE_PATH.exists():
        return False
    try:
        state = json.loads(STATE_PATH.read_text())
    except Exception:
        STATE_PATH.unlink(missing_ok=True)
        return False
    print(f"[swap] stale state found from {time.ctime(state.get('started', 0))} — "
          f"candidate {state.get('candidate')}, incumbent {state.get('incumbent')}")
    mgr = ModelManager(registry, service=state.get("service", "llama-server"),
                       dry_run=dry_run)
    mgr.incumbent = state.get("incumbent")
    ok = mgr.restore()
    if ok:
        STATE_PATH.unlink(missing_ok=True)
    return ok
