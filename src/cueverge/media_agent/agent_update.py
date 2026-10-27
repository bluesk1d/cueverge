"""Agent side of controller-managed updates: report the running release and install bundles."""

import importlib.metadata
import logging
import os
import shutil
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

from cueverge.agent_bundle import (
    AgentBundleError,
    current_platform,
    current_python,
    extract_bundle,
    sha256_file,
)
from cueverge.media_agent import launcher
from cueverge.protocol.device_v2 import AgentRelease, AgentUpdateResult, DeviceAgentUpdateCommand

LOGGER = logging.getLogger(__name__)
SELF_CHECK_SECONDS = 120.0


class AgentUpdateError(RuntimeError):
    pass


class AgentRestartRequested(Exception):
    """Raised after a verified install so the agent exits into its launcher."""


def running_version() -> str:
    try:
        return importlib.metadata.version("cueverge")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


class AgentInstallation:
    """The launcher-managed installation this agent runs from, if any."""

    def __init__(self, home: Path | None, bundle_digest: str | None) -> None:
        self.home = home
        self.bundle_digest = bundle_digest

    @classmethod
    def from_environment(cls) -> "AgentInstallation":
        digest = os.environ.get("CUEVERGE_AGENT_BUNDLE_DIGEST")
        home = os.environ.get("CUEVERGE_AGENT_HOME")
        if digest and home:
            return cls(Path(home), digest)
        return cls(None, None)

    @property
    def managed(self) -> bool:
        return self.home is not None and self.bundle_digest is not None

    def release(self) -> AgentRelease:
        return AgentRelease(
            version=running_version(),
            platform=current_platform(),
            bundle_digest=self.bundle_digest,
            managed=self.managed,
            last_update=self._last_update(),
        )

    def confirm(self) -> None:
        """Tell the launcher this version reached the controller; promotes a trial version."""
        if self.home is None or self.bundle_digest is None:
            return
        state = launcher.load_state(self.home)
        if state.get("trial") == self.bundle_digest:
            marker = self.home / f"{launcher.CONFIRMED_PREFIX}{self.bundle_digest}"
            marker.write_text("confirmed\n", encoding="utf-8")
            LOGGER.info("confirmed trial agent bundle %s", self.bundle_digest[:12])
        elif state.get("current") == self.bundle_digest:
            self._refresh_launcher()

    def install(self, command: DeviceAgentUpdateCommand, ssl_context: ssl.SSLContext) -> None:
        """Download, verify, extract and self-check a bundle, then mark it as the trial."""
        if self.home is None or self.bundle_digest is None:
            raise AgentUpdateError("this agent wasn't installed with cueverge-agent-install")
        if command.platform != current_platform():
            raise AgentUpdateError(
                f"bundle is for {command.platform}, this host is {current_platform()}"
            )
        if command.digest_sha256 == self.bundle_digest:
            raise AgentUpdateError("this agent already runs that bundle")
        downloads = self.home / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        bundle = downloads / f"{command.digest_sha256}.zip"
        try:
            download_bundle(
                str(command.download_url),
                bundle,
                size_bytes=command.size_bytes,
                digest_sha256=command.digest_sha256,
                ssl_context=ssl_context,
            )
            install_bundle(self.home, bundle, command.digest_sha256, expected_version=None)
        finally:
            bundle.unlink(missing_ok=True)
        state = launcher.load_state(self.home)
        state["trial"] = command.digest_sha256
        launcher.write_state(self.home, state)
        LOGGER.info("installed agent bundle %s as a trial", command.digest_sha256[:12])

    def _last_update(self) -> AgentUpdateResult | None:
        if self.home is None:
            return None
        try:
            value = launcher.load_state(self.home).get("last_update")
            return AgentUpdateResult.model_validate(value) if value else None
        except (OSError, ValueError) as error:
            LOGGER.warning("unreadable agent update state: %s", error)
            return None

    def _refresh_launcher(self) -> None:
        """Adopt the launcher shipped with a confirmed version on the launcher's next start."""
        assert self.home is not None
        shipped = Path(launcher.__file__)
        installed = self.home / "launcher.py"
        if installed.exists() and installed.read_bytes() == shipped.read_bytes():
            return
        temporary = self.home / ".launcher.py.tmp"
        shutil.copyfile(shipped, temporary)
        os.replace(temporary, installed)
        LOGGER.info("updated the agent launcher; it takes effect when the service restarts")


def download_bundle(
    url: str,
    target: Path,
    *,
    size_bytes: int,
    digest_sha256: str,
    ssl_context: ssl.SSLContext,
) -> None:
    partial = target.with_suffix(".part")
    with (
        urllib.request.urlopen(url, context=ssl_context, timeout=60) as response,
        partial.open("wb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            if output.tell() > size_bytes:
                break
    if partial.stat().st_size != size_bytes:
        partial.unlink(missing_ok=True)
        raise AgentUpdateError("downloaded agent bundle size doesn't match the controller")
    if sha256_file(partial) != digest_sha256:
        partial.unlink(missing_ok=True)
        raise AgentUpdateError("downloaded agent bundle checksum doesn't match the controller")
    os.replace(partial, target)


def install_bundle(
    home: Path, bundle: Path, digest_sha256: str, *, expected_version: str | None
) -> None:
    """Extract a verified bundle into versions/<digest> and prove it imports on this host."""
    destination = launcher.version_dir(home, digest_sha256)
    if destination.is_dir():
        return
    staging = home / launcher.VERSIONS / f".{digest_sha256}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        manifest = extract_bundle(bundle, staging)
        if manifest.platform != current_platform():
            raise AgentUpdateError(f"bundle is for {manifest.platform}")
        if manifest.python != current_python():
            raise AgentUpdateError(
                f"bundle needs Python {manifest.python}; the launcher runs {current_python()}"
            )
        if expected_version is not None and manifest.version != expected_version:
            raise AgentUpdateError("bundle version doesn't match the controller")
        _self_check(staging, manifest.version)
        os.replace(staging, destination)
    except AgentBundleError as error:
        raise AgentUpdateError(str(error)) from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _self_check(tree: Path, version: str) -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tree / "site-packages")
    environment["PYTHONNOUSERSITE"] = "1"
    probe = (
        "import importlib.metadata, cueverge.media_agent.app, cueverge.media_agent.launcher; "
        "print(importlib.metadata.version('cueverge'))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-s", "-c", probe],
            env=environment,
            capture_output=True,
            text=True,
            timeout=SELF_CHECK_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise AgentUpdateError("the new version's self-check timed out") from error
    if result.returncode != 0 or result.stdout.strip() != version:
        detail = (result.stderr.strip() or result.stdout.strip())[-300:]
        raise AgentUpdateError(f"the new version failed its self-check: {detail}")
