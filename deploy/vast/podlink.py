"""One channel to a Vast.ai instance: run a command, mirror the working tree.

Vast.ai gives every instance a Jupyter server on a mapped TCP port with a
token. On a fresh instance that is the *only* thing reachable -- SSH needs a
key in the container's authorized_keys, which a new container does not have
-- so this module speaks Jupyter's own protocol rather than depending on SSH
being set up first:

  * ``run()`` drives a Python kernel over its websocket, because Jupyter has
    no REST endpoint that executes anything. The kernel is created once and
    its id cached in ``.podlink-kernel``, so repeated calls are cheap.
  * ``push()`` ships the working tree through the REST contents API as one
    tarball and untars it on the far side. One request per push, not one per
    file: the repo is ~5 MB across 300+ files, and a TLS round trip each
    took longer than the whole tarball does.

Deletions are why push() also sends a manifest. Untarring only adds and
overwrites, so a file deleted locally would live on forever on the pod and
keep getting imported -- exactly the drift that makes "works on my machine"
unfalsifiable. The remote side removes tracked paths that have disappeared
from the manifest and touches nothing it was not given.

Connection settings come from deploy/vast/pod.env (git-ignored) or the
environment: VAST_HOST, VAST_PORT (the public port mapped to container
8080), VAST_TOKEN.

    python deploy/vast/podlink.py run 'nvidia-smi'
    python deploy/vast/podlink.py push
    python deploy/vast/podlink.py watch             # push on save, then make it take effect
    python deploy/vast/podlink.py watch --no-reload # push only
    python deploy/vast/podlink.py reload            # restart the services whose code changed
    python deploy/vast/podlink.py reload voice-pcm  # ... or named ones; --force for all
    python deploy/vast/podlink.py status            # which services run stale code
    python deploy/vast/podlink.py verify            # is the pod's tree byte-identical to this one

A push alone changes nothing a running service can see: every service is a
plain `uvicorn` (no --reload), so the old modules stay in memory. `reload`
(deploy/vast/svc_sync.py, on the pod) restarts only the services whose source
changed and re-reads models from disk -- nothing is downloaded.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import ssl
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

import requests
import urllib3
import websocket

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Everything this tool prints is remote output, and in THIS project that
# output is routinely Bengali, Hindi and Devanagari. A Windows console
# defaults to cp1252, which cannot encode any of it, so an otherwise
# successful run died with UnicodeEncodeError while relaying a transcript --
# losing the result of a two-minute call test to a console encoding. Replace
# rather than raise: a console that cannot render a glyph should print a
# placeholder, never discard the whole line.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a reconfigurable text stream
        pass

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
DEFAULT_REMOTE_ROOT = "/workspace/kolkata-care-voice-agent"
KERNEL_CACHE = HERE / ".podlink-kernel"

# Never shipped: local virtualenvs, caches, build junk, and the pod's own
# runtime state. .git is excluded because the pod does not need history --
# push() mirrors a working tree, it is not a second git remote.
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".venv", "venv", "node_modules", ".idea", ".vscode", "logs", ".hypothesis",
}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".so", ".log", ".sqlite", ".db")
# The secret-bearing names repeat .gitignore on purpose: when git cannot be
# found, tracked() falls back to a plain walk that knows nothing of
# .gitignore, and that walk once shipped the Twilio credentials file.
EXCLUDE_NAMES = {
    ".podlink-kernel", "pod.env", ".DS_Store", "repo.tgz",
    ".env", "telephony-twilio.env.sh", "gate-report.json", "gate-report.md",
}

# A shell opened without Git on PATH (the desktop app's terminal panel, a
# fresh PowerShell) cannot see git at all; try the standard install first.
_GIT_CANDIDATES = (
    "git",
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
)

RC_MARKER = "__PODLINK_RC__="


def _load_env() -> dict[str, str]:
    """pod.env first, then the real environment (which wins)."""
    cfg: dict[str, str] = {}
    env_file = HERE / "pod.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip().strip("'\"")
    for key in ("VAST_HOST", "VAST_PORT", "VAST_TOKEN", "VAST_REMOTE_ROOT"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    missing = [k for k in ("VAST_HOST", "VAST_PORT", "VAST_TOKEN") if not cfg.get(k)]
    if missing:
        sys.exit(
            "missing " + ", ".join(missing) + " -- set them in " + str(env_file)
            + " or in the environment.\nVAST_PORT is the public port mapped to"
            " container port 8080 (Jupyter)."
        )
    return cfg


class Pod:
    def __init__(self) -> None:
        cfg = _load_env()
        self.host = cfg["VAST_HOST"]
        self.port = cfg["VAST_PORT"]
        self.token = cfg["VAST_TOKEN"]
        self.remote_root = cfg.get("VAST_REMOTE_ROOT", DEFAULT_REMOTE_ROOT)
        self.http = requests.Session()
        self.http.verify = False          # Vast serves a self-signed instance cert

    def _rest(self, path: str) -> str:
        sep = "&" if "?" in path else "?"
        return "https://{}:{}{}{}token={}".format(
            self.host, self.port, path, sep, self.token)

    def _ws(self, path: str) -> str:
        return "wss://{}:{}{}?token={}".format(self.host, self.port, path, self.token)

    # ---- exec ------------------------------------------------------------

    def _kernel(self) -> str:
        if KERNEL_CACHE.exists():
            kid = KERNEL_CACHE.read_text().strip()
            reply = self.http.get(self._rest("/api/kernels/" + kid), timeout=30)
            if reply.ok and reply.json().get("execution_state") != "dead":
                return kid
        reply = self.http.post(
            self._rest("/api/kernels"), json={"name": "python3"}, timeout=180)
        reply.raise_for_status()
        kid = reply.json()["id"]
        KERNEL_CACHE.write_text(kid)
        return kid

    def run(self, command: str, timeout: int = 3600,
            quiet: bool = False) -> tuple[int, str]:
        """Run a bash command on the pod; return (exit code, combined output).

        Streams output as it arrives: a pip install or a model download is
        minutes long, and a silent wait is indistinguishable from a hang.
        """
        wrapper = "\n".join([
            "import subprocess, sys",
            "_p = subprocess.Popen(['bash', '-lc', " + repr(command) + "],",
            "    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,",
            "    text=True, bufsize=1, errors='replace')",
            "for _line in _p.stdout:",
            "    sys.stdout.write(_line)",
            "    sys.stdout.flush()",
            "_p.wait()",
            "print()",
            "print(" + repr(RC_MARKER) + " + str(_p.returncode))",
        ])
        conn = websocket.create_connection(
            self._ws("/api/kernels/" + self._kernel() + "/channels"),
            sslopt={"cert_reqs": ssl.CERT_NONE},
            timeout=timeout,
            suppress_origin=True,
        )
        msg_id = uuid.uuid4().hex
        conn.send(json.dumps({
            "header": {"msg_id": msg_id, "username": "podlink",
                       "session": uuid.uuid4().hex,
                       "msg_type": "execute_request", "version": "5.3"},
            "parent_header": {}, "metadata": {},
            "content": {"code": wrapper, "silent": False, "store_history": False,
                        "user_expressions": {}, "allow_stdin": False,
                        "stop_on_error": True},
            "channel": "shell",
        }))
        chunks: list[str] = []
        rc = 0
        while True:
            message = json.loads(conn.recv())
            if message.get("parent_header", {}).get("msg_id") != msg_id:
                continue
            kind = message["msg_type"]
            content = message.get("content", {})
            if kind == "stream":
                text = content.get("text", "")
                chunks.append(text)
                if not quiet:
                    sys.stdout.write(text)
                    sys.stdout.flush()
            elif kind == "error":
                text = "\n".join(content.get("traceback", []))
                chunks.append(text)
                if not quiet:
                    sys.stdout.write(text + "\n")
                rc = 1
            elif kind == "status" and content.get("execution_state") == "idle":
                break
        conn.close()
        output = "".join(chunks)
        for line in output.splitlines():
            if line.startswith(RC_MARKER):
                rc = int(line[len(RC_MARKER):].strip())
        return rc, output

    # ---- push ------------------------------------------------------------

    def tracked(self) -> list[Path]:
        """Every path push() is responsible for, relative to the repo root.

        Prefers git's own view of the tree, so .gitignore is honoured for
        free and an untracked scratch file is not deployed by accident;
        falls back to a walk when git is unavailable.
        """
        candidates: list[Path] | None = None
        for git in _GIT_CANDIDATES:
            try:
                listing = subprocess.run(
                    [git, "ls-files", "--cached", "--others", "--exclude-standard"],
                    cwd=REPO_ROOT, capture_output=True, text=True, check=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                continue
            candidates = [REPO_ROOT / line for line in listing.stdout.splitlines() if line]
            break
        if candidates is None:
            candidates = list(REPO_ROOT.rglob("*"))

        keep: list[Path] = []
        for path in candidates:
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT)
            if EXCLUDE_DIRS & set(rel.parts):
                continue
            if rel.name in EXCLUDE_NAMES or rel.suffix in EXCLUDE_SUFFIXES:
                continue
            keep.append(rel)
        return sorted(keep)

    def _tarball(self, files: list[Path]) -> bytes:
        buf = io.BytesIO()
        # uid/gid/mode are normalised: the pod runs as root and a Windows
        # checkout's numeric owner means nothing there (tar otherwise warns
        # on every single entry), while the executable bit does not survive
        # a Windows checkout at all and has to be reasserted for .sh/.py.
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for rel in files:
                info = tar.gettarinfo(str(REPO_ROOT / rel), arcname=rel.as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mode = 0o755 if rel.suffix in (".sh", ".py") else 0o644
                with open(REPO_ROOT / rel, "rb") as handle:
                    tar.addfile(info, handle)
            manifest = ("\n".join(r.as_posix() for r in files) + "\n").encode()
            info = tarfile.TarInfo(".podlink-manifest")
            info.size = len(manifest)
            info.uid = info.gid = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(manifest))
        return buf.getvalue()

    def push(self, prune: bool = True, quiet: bool = False) -> int:
        files = self.tracked()
        payload = self._tarball(files)
        reply = self.http.put(
            self._rest("/api/contents/tmp/podlink-push.tgz"),
            json={"type": "file", "format": "base64",
                  "content": base64.b64encode(payload).decode()},
            timeout=900,
        )
        reply.raise_for_status()

        # Untar, then prune. comm -23 lists paths in the PREVIOUS manifest
        # that are absent from this one: those are the local deletions.
        script = "\n".join([
            "set -u",
            "ROOT=" + repr(self.remote_root).replace("'", '"'),
            "mkdir -p \"$ROOT\"",
            "tar xzf /tmp/podlink-push.tgz -C \"$ROOT\" "
            "--no-same-owner --no-same-permissions",
            "rm -f /tmp/podlink-push.tgz",
            "cd \"$ROOT\"",
            "if [ -f .podlink-manifest.prev ] && [ " + ("1" if prune else "0") + " = 1 ]; then",
            "  comm -23 <(sort .podlink-manifest.prev) <(sort .podlink-manifest) |",
            "  while IFS= read -r gone; do",
            "    if [ -n \"$gone\" ] && [ -f \"$gone\" ]; then",
            "      rm -f -- \"$gone\" && echo \"  pruned $gone\"",
            "    fi",
            "  done",
            "fi",
            "cp .podlink-manifest .podlink-manifest.prev",
            "echo \"pushed $(wc -l < .podlink-manifest) files to $ROOT\"",
        ])
        rc, output = self.run(script, quiet=True)
        if not quiet:
            for line in output.splitlines():
                if line.startswith("pushed ") or line.startswith("  pruned "):
                    print(line)
        if rc != 0 and not quiet:
            sys.stderr.write(output)
        return rc

    # ---- make it take effect ----------------------------------------------

    def svc(self, *args: str) -> int:
        """Run deploy/vast/svc_sync.py on the pod, with env.sh's ports in scope."""
        command = "cd {root} && . deploy/env.sh && VAST_REMOTE_ROOT={root} " \
                  "python3 deploy/vast/svc_sync.py {args}".format(
                      root=self.remote_root, args=" ".join(args))
        return self.run(command)[0]

    def reload(self, services: tuple[str, ...] = (), force: bool = False) -> int:
        """Restart the services whose code differs from what they were launched with."""
        return self.svc("reload", *services, *(["--force"] if force else []))

    def verify(self) -> int:
        """Compare every tracked file's sha256 here and on the pod; list the differences."""
        files = self.tracked()
        local = {f.as_posix(): hashlib.sha256((REPO_ROOT / f).read_bytes()).hexdigest()
                 for f in files}
        _, output = self.run(
            "cd {} && sha256sum $(cat .podlink-manifest)".format(self.remote_root),
            quiet=True)
        remote: dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and len(parts[0]) == 64:
                remote[parts[1].strip()] = parts[0]
        differ = sorted(k for k in local if remote.get(k) != local[k])
        print("{} files checked, {} differ".format(len(local), len(differ)))
        for name in differ[:20]:
            print("  differs: " + name)
        return 1 if differ else 0


def regenerate_pcm() -> bool:
    """main_pcm.py is GENERATED from main.py (tools/make_pcm_variant.py).

    Editing main.py alone leaves the PCM entrypoint -- the telephony path, the
    one on public port 10100 -- running the old reasoning half, with nothing
    to say so. The generator refuses to write if the two halves have drifted,
    so a failure here is reported rather than papered over.
    """
    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "make_pcm_variant.py")],
        cwd=REPO_ROOT, capture_output=True, text=True)
    detail = (done.stdout + done.stderr).strip().splitlines()
    print("  main.py changed -> {}{}".format(
        "main_pcm.py regenerated" if done.returncode == 0
        else "!! main_pcm.py NOT regenerated (pod PCM keeps the old logic)",
        "" if done.returncode == 0 or not detail else ": " + detail[-1]))
    return done.returncode == 0


def watch(pod: Pod, interval: float = 2.0, reload: bool = True) -> int:
    """Push whenever the working tree changes, then reload what it affects.

    Polls mtimes instead of using a filesystem-event library: no extra
    dependency, and stat-ing 300 files takes ~10 ms, well under the poll
    interval. Debounced by the interval, so a multi-file editor save or a
    branch switch is one push rather than one per file.

    A dropped connection (a laptop sleeping, a pod restarting) is retried on
    the next sweep instead of ending the watch -- `previous` is only advanced
    after a push that actually landed, so nothing is skipped.
    """
    print("watching {} -> {}:{}".format(REPO_ROOT, pod.host, pod.remote_root))
    print("reload after push: {}".format(
        "on (only services whose code changed restart; models re-read from disk)"
        if reload else "off"))
    print("Ctrl-C to stop.")

    def sweep() -> dict[str, float]:
        return {rel.as_posix(): (REPO_ROOT / rel).stat().st_mtime for rel in pod.tracked()}

    previous: dict[str, float] = {}
    first = True
    while True:
        try:
            snapshot = sweep()
        except OSError:
            time.sleep(interval)          # a file vanished mid-sweep; retry
            continue
        if snapshot != previous:
            changed = [k for k, v in snapshot.items() if previous.get(k) != v]
            removed = [k for k in previous if k not in snapshot]
            if first:
                print("initial sync...")
            else:
                for name in changed[:8]:
                    print("  changed: " + name)
                for name in removed[:8]:
                    print("  deleted: " + name)
                extra = len(changed) + len(removed) - 8
                if extra > 0:
                    print("  ... and {} more".format(extra))
            # Only when main.py moved on its own: if main_pcm.py changed in the
            # same sweep it was regenerated (or edited) by hand already.
            if not first and "main.py" in changed and "main_pcm.py" not in changed:
                regenerate_pcm()
                try:
                    snapshot = sweep()    # the regeneration touched main_pcm.py
                except OSError:
                    continue
            try:
                rc = pod.push()
                if rc == 0:
                    print("  [{}] synced".format(time.strftime("%H:%M:%S")))
                    if reload:
                        pod.reload()
                else:
                    print("  push failed (rc={}); will retry".format(rc))
                    time.sleep(interval)
                    continue
            except (requests.RequestException, websocket.WebSocketException,
                    OSError) as exc:
                print("  connection problem ({}); will retry".format(exc))
                time.sleep(interval)
                continue
            previous, first = snapshot, False
        time.sleep(interval)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    command, rest = args[0], args[1:]
    pod = Pod()
    if command == "run":
        return pod.run(" ".join(rest) if rest else sys.stdin.read())[0]
    if command == "push":
        rc = pod.push(prune="--no-prune" not in rest)
        return pod.reload() if rc == 0 and "--reload" in rest else rc
    if command == "watch":
        return watch(pod, reload="--no-reload" not in rest)
    if command == "reload":
        names = tuple(a for a in rest if not a.startswith("--"))
        return pod.reload(names, force="--force" in rest)
    if command == "status":
        return pod.svc("status")
    if command == "verify":
        return pod.verify()
    print("unknown command {!r}; expected run, push, watch, reload, status or verify"
          .format(command))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
