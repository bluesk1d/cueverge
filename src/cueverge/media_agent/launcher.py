"""Supervise a controller-managed media agent installation; standard library only.

This file is copied into the agent home and started by the service manager, so it never
imports the agent versions it supervises. Layout under CUEVERGE_AGENT_HOME:

    launcher.py             this file
    state.json              {"current", "previous", "trial", "last_update"}
    versions/<digest>/      an extracted agent bundle (manifest and site-packages)
    confirmed-<digest>      written by a trial version once the controller welcomed it

A trial version that exits or doesn't confirm within the trial period is stopped, and the
previous current version runs again with a rolled_back result for the controller to show.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

RESTART_EXIT_CODE = 75
STATE_FILE = "state.json"
VERSIONS = "versions"
CONFIRMED_PREFIX = "confirmed-"
TRIAL_SECONDS = 180.0
LOGGER = logging.getLogger("cueverge.agent_launcher")


def default_home() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Cueverge" / "agent"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "cueverge" / "agent"


def agent_home() -> Path:
    configured = os.environ.get("CUEVERGE_AGENT_HOME")
    return Path(configured) if configured else default_home()


def load_state(home: Path) -> dict[str, Any]:
    path = home / STATE_FILE
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def write_state(home: Path, state: dict[str, Any]) -> None:
    temporary = home / f".{STATE_FILE}.tmp"
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, home / STATE_FILE)


def version_dir(home: Path, digest: str) -> Path:
    return home / VERSIONS / digest


def child_command(home: Path, digest: str) -> tuple[list[str], dict[str, str]]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(version_dir(home, digest) / "site-packages")
    environment["PYTHONNOUSERSITE"] = "1"
    environment["CUEVERGE_AGENT_HOME"] = str(home)
    environment["CUEVERGE_AGENT_BUNDLE_DIGEST"] = digest
    return [sys.executable, "-s", "-m", "cueverge.media_agent.app"], environment


class Launcher:
    def __init__(self, home: Path, *, trial_seconds: float = TRIAL_SECONDS) -> None:
        self.home = home
        self.trial_seconds = trial_seconds
        self._child: subprocess.Popen[bytes] | None = None
        self._stopping = False
        self._job = _KillOnCloseJob() if sys.platform == "win32" else None

    def run(self) -> int:
        while not self._stopping:
            state = load_state(self.home)
            trial = state.get("trial")
            digest = trial or state.get("current")
            if not isinstance(digest, str) or not version_dir(self.home, digest).is_dir():
                LOGGER.error(
                    "no installed agent version in %s; run cueverge-agent-install", self.home
                )
                return 1
            code = self._run_trial(digest) if trial else self._run_version(digest)
            if code == RESTART_EXIT_CODE:
                LOGGER.info("agent requested a restart into an updated version")
                continue
            if code is None:
                continue
            return code
        return 0

    def stop(self, *_: object) -> None:
        self._stopping = True
        if self._child is not None and self._child.poll() is None:
            self._child.terminate()

    def _start(self, digest: str) -> subprocess.Popen[bytes]:
        command, environment = child_command(self.home, digest)
        LOGGER.info("starting agent bundle %s", digest[:12])
        child = subprocess.Popen(command, env=environment)
        if self._job is not None:
            self._job.assign(child)
        self._child = child
        return child

    def _run_version(self, digest: str) -> int:
        return self._start(digest).wait()

    def _run_trial(self, digest: str) -> int | None:
        marker = self.home / f"{CONFIRMED_PREFIX}{digest}"
        marker.unlink(missing_ok=True)
        child = self._start(digest)
        deadline = time.monotonic() + self.trial_seconds
        while True:
            code = child.poll()
            if marker.exists():
                self._promote(digest)
                return child.wait()
            if self._stopping:
                return child.wait()
            if code is not None:
                self._roll_back(
                    digest, f"The new version exited with code {code} before connecting."
                )
                return None
            if time.monotonic() >= deadline:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                self._roll_back(
                    digest,
                    f"The new version didn't connect to the controller within "
                    f"{round(self.trial_seconds)} seconds.",
                )
                return None
            time.sleep(0.5)

    def _promote(self, digest: str) -> None:
        state = load_state(self.home)
        previous = state.get("current")
        state.update(
            current=digest,
            previous=previous if previous != digest else state.get("previous"),
            trial=None,
            last_update=_result(digest, "installed", None),
        )
        write_state(self.home, state)
        (self.home / f"{CONFIRMED_PREFIX}{digest}").unlink(missing_ok=True)
        keep = {digest, state.get("previous")}
        for path in (self.home / VERSIONS).iterdir():
            if path.is_dir() and path.name not in keep and not path.name.startswith("."):
                shutil.rmtree(path, ignore_errors=True)
        LOGGER.info("agent bundle %s confirmed and promoted", digest[:12])

    def _roll_back(self, digest: str, detail: str) -> None:
        state = load_state(self.home)
        state.update(trial=None, last_update=_result(digest, "rolled_back", detail))
        write_state(self.home, state)
        shutil.rmtree(version_dir(self.home, digest), ignore_errors=True)
        LOGGER.error("rolled back agent bundle %s: %s", digest[:12], detail)


def _result(digest: str, outcome: str, detail: str | None) -> dict[str, Any]:
    return {
        "bundle_digest": digest,
        "outcome": outcome,
        "detail": detail,
        "finished_at_unix_ms": time.time_ns() // 1_000_000,
    }


class _KillOnCloseJob:
    """A Windows job object that ends the agent when the launcher ends, like a systemd cgroup."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
        self._handle = self._kernel32.CreateJobObjectW(None, None)

        class _Basic(ctypes.Structure):
            _fields_ = (
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            )

        class _Extended(ctypes.Structure):
            _fields_ = (
                ("BasicLimitInformation", _Basic),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            )

        info = _Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self._handle or not self._kernel32.SetInformationJobObject(
            self._handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            LOGGER.warning("could not create a kill-on-close job; stop the agent with its task")
            self._handle = None

    def assign(self, child: subprocess.Popen[bytes]) -> None:
        handle = getattr(child, "_handle", None)
        if self._handle and handle is not None:
            self._kernel32.AssignProcessToJobObject(self._handle, int(handle))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    launcher = Launcher(agent_home())
    signal.signal(signal.SIGTERM, launcher.stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, launcher.stop)
    try:
        sys.exit(launcher.run())
    except KeyboardInterrupt:
        launcher.stop()
        sys.exit(0)


if __name__ == "__main__":
    main()
