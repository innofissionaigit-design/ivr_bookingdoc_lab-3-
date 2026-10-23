#!/usr/bin/env python3
"""Pod side of podlink: make a pushed edit take effect, restarting only what it touches.

podlink.py push puts new files on the pod's disk, but every service here runs
under a plain `uvicorn` with no --reload, so the old code stays in memory until
that process is restarted. This script closes that gap and does it per service:

  * It fingerprints each service's source (a sha256 over the files it imports)
    and records the fingerprint at the moment the service is launched, in
    /workspace/.svc_code_hash/<service>. A service is STALE when its files no
    longer hash to what it was launched with. The decision is made from file
    CONTENT, so it is right after a failed restart, a pod-side edit, a re-push
    of unchanged files, or a first run on a pod nobody told it about.
  * An edit to clinic-api/ restarts clinic-api alone. An edit to tts_server.py
    restarts TTS alone. An edit under agent/ restarts the two voice
    entrypoints (and clinic-api for the one module it lazy-imports) but NOT
    the TTS or English-ASR processes, unless a module they import changed --
    that is read from the AST, not guessed.
  * static/ is not hashed at all: both apps mount it as StaticFiles, served
    from disk on every request, so a pushed edit is live already.

No model is ever downloaded here. A restart re-reads the checkpoints that
provision.sh already put under /workspace (HF_HOME, tts_checkpoints, lid_model,
.ollama); the cost is load time (~4 min for a voice entrypoint), not bandwidth.
Ollama and its qwen model are never touched -- they are not our code.

    python3 svc_sync.py status
    python3 svc_sync.py reload                 # restart the STALE ones, wait for health
    python3 svc_sync.py reload voice-pcm       # named service(s), stale or not
    python3 svc_sync.py reload --force         # all of them
    python3 svc_sync.py adopt                  # record the running services as current

`adopt` is for the one-off case of services that were started before this file
existed: it asserts "what is running is what is on disk now". Only say so when
that is true (podlink.py checks the push was clean before it ever does).

Run it with deploy/env.sh sourced (podlink.py does) so the ports match what
the launchers bound; the defaults below are env.sh's own.
"""

from __future__ import annotations

import ast
import hashlib
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(os.environ.get("VAST_REMOTE_ROOT", "/workspace/kolkata-care-voice-agent"))
STATE = Path("/workspace/.svc_code_hash")
BIN = Path("/workspace/bin")
LOGS = Path("/workspace/logs")

# Files that change how EVERY service starts: env.sh, and the two persistent
# files it sources. Editing a port or the language list needs a restart.
COMMON = [REPO / "deploy" / "env.sh", Path("/workspace/.host_env"),
          Path("/workspace/.voice_agent_languages")]

READY_TIMEOUT_S = 480       # a voice entrypoint measured 245 s from process start


def _port(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _agent_closure(entry: Path) -> list[Path]:
    """Every file under agent/ that `entry` imports, transitively.

    Read from the AST so a later `import agent.foo` is followed without anyone
    updating a list. It sees function-level imports too (ast.walk does not
    care about nesting); it cannot see an importlib string, so the two voice
    entrypoints use the whole agent/ directory instead of this.
    """
    seen: dict[str, Path] = {}
    todo = [entry]
    agent_dir = REPO / "agent"
    while todo:
        path = todo.pop()
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".", 1)[1] for a in node.names
                          if a.name.startswith("agent.")}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "agent":
                    names |= {a.name for a in node.names}
                elif node.module.startswith("agent."):
                    names.add(node.module.split(".", 1)[1].split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 1 and path.parent == agent_dir:
                if node.module:
                    names.add(node.module.split(".")[0])
                else:
                    names |= {a.name for a in node.names}
        for name in sorted(names):
            candidate = agent_dir / (name + ".py")
            if candidate.exists() and name not in seen:
                seen[name] = candidate
                todo.append(candidate)
    return [agent_dir / "__init__.py"] + sorted(seen.values())


def _tree(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts
                  and p.suffix not in (".pyc", ".pyo"))


def services() -> dict[str, dict]:
    """The service table. Sources are lazy so a deleted file cannot crash import."""
    agent_all = lambda: _tree(REPO / "agent")                       # noqa: E731
    languages = os.environ.get("VOICE_AGENT_LANGUAGES", "bn,hi,en").split(",")
    table = {
        "tts": dict(
            port=_port("TTS_PORT", 8002), health="/health",
            launcher="_run_tts.sh", log="tts_server.log",
            sources=lambda: [REPO / "tts_server.py"] + _agent_closure(REPO / "tts_server.py")),
        "clinic-api": dict(
            port=_port("CLINIC_API_PORT", 8080), health="/api/health",
            launcher="_run_clinic.sh", log="clinic_api.log",
            # gazetteer.py lazy-imports agent.phonetic_match inside a function.
            sources=lambda: _tree(REPO / "clinic-api") + [REPO / "agent" / "phonetic_match.py"]),
        "voice-agent": dict(
            port=_port("VOICE_AGENT_PORT", 8100), health="/api/health",
            launcher="_run_main.sh", log="main_app.log",
            sources=lambda: [REPO / "main.py"] + agent_all()),
        "voice-pcm": dict(
            port=_port("VOICE_PCM_PORT", 8101), health="/api/health",
            launcher="_run_pcm.sh", log="pcm_app.log",
            sources=lambda: [REPO / "main_pcm.py"] + agent_all()),
    }
    if "en" in [lang.strip() for lang in languages]:
        table["english-asr"] = dict(
            port=8003, health="/health",
            launcher="_run_english_asr.sh", log="english_asr.log",
            sources=lambda: [REPO / "english_asr_server.py"])
    return table


def fingerprint(spec: dict) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(spec["sources"]()) | set(COMMON)):
        try:
            blob = path.read_bytes()
        except OSError:
            blob = b"<missing>"
        digest.update(str(path).encode() + b"\0" + hashlib.sha256(blob).digest())
    return digest.hexdigest()


def recorded(name: str) -> str | None:
    try:
        return (STATE / name).read_text().strip()
    except OSError:
        return None


def healthy(spec: dict) -> bool:
    try:
        url = "http://127.0.0.1:{}{}".format(spec["port"], spec["health"])
        with urllib.request.urlopen(url, timeout=3) as reply:
            return reply.status == 200
    except Exception:
        return False


def _listener_pids(port: int) -> list[int]:
    out = subprocess.run(["fuser", "{}/tcp".format(port)], capture_output=True, text=True)
    return [int(tok) for tok in out.stdout.split() if tok.isdigit()]


def stop(spec: dict) -> None:
    """Kill by PORT, never by command-line pattern (deploy/start_all.sh says why)."""
    pids = _listener_pids(spec["port"])
    subprocess.run(["fuser", "-k", "{}/tcp".format(spec["port"])],
                   capture_output=True, text=True)
    deadline = time.time() + 30
    while time.time() < deadline and any(Path("/proc/{}".format(p)).exists() for p in pids):
        time.sleep(0.5)
    for pid in pids:                       # still alive after 30 s: it will not let go of the GPU
        if Path("/proc/{}".format(pid)).exists():
            os.kill(pid, signal.SIGKILL)
    time.sleep(1)


def launch(name: str, spec: dict) -> None:
    launcher = BIN / spec["launcher"]
    if not launcher.exists():
        sys.exit("!! {} missing -- run deploy/vast/start_vast.sh once to create the "
                 "launchers".format(launcher))
    STATE.mkdir(parents=True, exist_ok=True)
    # Fingerprint BEFORE launching: if a push lands while the process is still
    # importing, the record is older than the loaded code, so the worst case is
    # one needless restart, never a stale service reported as current.
    (STATE / name).write_text(fingerprint(spec))
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / spec["log"], "wb") as log:
        subprocess.Popen(["setsid", str(launcher)], stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)


def cmd_status(table: dict) -> int:
    for name, spec in table.items():
        now, then = fingerprint(spec), recorded(name)
        state = ("current" if now == then else
                 "UNRECORDED" if then is None else "STALE")
        print("  {:<12} {:<5} {:<10} :{}".format(
            name, "UP" if healthy(spec) else "down", state, spec["port"]))
    return 0


def cmd_adopt(table: dict) -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    for name, spec in table.items():
        if healthy(spec):
            (STATE / name).write_text(fingerprint(spec))
            print("  adopted {}".format(name))
        else:
            print("  skipped {} (not running)".format(name))
    return 0


def cmd_reload(table: dict, wanted: list[str], force: bool) -> int:
    unknown = [w for w in wanted if w not in table]
    if unknown:
        sys.exit("unknown service(s) {}; known: {}".format(unknown, ", ".join(table)))
    if wanted:
        targets = wanted
    elif force:
        targets = list(table)
    else:
        targets = [n for n, s in table.items()
                   if fingerprint(s) != recorded(n) or not healthy(s)]
    if not targets:
        print("  nothing to restart -- every service already runs the code on disk")
        return 0

    print("  restarting: {}".format(", ".join(targets)), flush=True)
    started = time.time()
    for name in targets:
        stop(table[name])
    for name in targets:
        launch(name, table[name])
    pending = set(targets)
    while pending and time.time() - started < READY_TIMEOUT_S:
        for name in sorted(pending):
            if healthy(table[name]):
                pending.discard(name)
                print("  {:<12} ready after {:.0f}s".format(name, time.time() - started),
                      flush=True)
        if pending:
            time.sleep(2)
    for name in sorted(pending):
        print("  !! {} not healthy after {}s -- tail /workspace/logs/{}".format(
            name, READY_TIMEOUT_S, table[name]["log"]))
    return 1 if pending else 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("status", "reload", "adopt"):
        print(__doc__)
        return 2
    table = services()
    if args[0] == "status":
        return cmd_status(table)
    if args[0] == "adopt":
        return cmd_adopt(table)
    names = [a for a in args[1:] if not a.startswith("--")]
    return cmd_reload(table, names, force="--force" in args)


if __name__ == "__main__":
    raise SystemExit(main())
