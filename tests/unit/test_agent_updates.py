import json
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from cueverge.agent_bundle import (
    BUNDLE_MANIFEST,
    AgentBundleError,
    AgentBundleManifest,
    current_platform,
    current_python,
    extract_bundle,
    read_manifest,
    sha256_file,
)
from cueverge.media_agent import agent_install, launcher
from cueverge.media_agent.agent_update import (
    AgentInstallation,
    AgentUpdateError,
    download_bundle,
    install_bundle,
)
from cueverge.protocol.device_v2 import DeviceAgentUpdateCommand

LAUNCHER_SOURCE = Path(launcher.__file__).read_text(encoding="utf-8")


def make_bundle(
    path: Path,
    *,
    version: str = "9.9.9",
    platform: Any = None,
    python: str | None = None,
    importable: bool = True,
    extra: dict[str, str] | None = None,
) -> Path:
    """A tiny bundle whose cueverge package passes the agent self-check."""
    manifest = AgentBundleManifest(
        version=version,
        build="test",
        platform=platform or current_platform() or "linux_x86_64",
        python=python or current_python(),
        created_at=datetime.now(UTC),
    )
    files = {
        BUNDLE_MANIFEST: manifest.model_dump_json(),
        "site-packages/cueverge/__init__.py": "",
        "site-packages/cueverge/media_agent/__init__.py": "",
        "site-packages/cueverge/media_agent/launcher.py": LAUNCHER_SOURCE,
        f"site-packages/cueverge-{version}.dist-info/METADATA": (
            f"Metadata-Version: 2.1\nName: cueverge\nVersion: {version}\n"
        ),
        **(extra or {}),
    }
    if importable:
        files["site-packages/cueverge/media_agent/app.py"] = "def main():\n    pass\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_manifest_and_safe_extraction(tmp_path: Path) -> None:
    bundle = make_bundle(tmp_path / "bundle.zip")
    assert read_manifest(bundle).version == "9.9.9"
    manifest = extract_bundle(bundle, tmp_path / "out")
    assert manifest.build == "test"
    assert (tmp_path / "out" / "site-packages" / "cueverge" / "media_agent" / "app.py").is_file()
    with pytest.raises(AgentBundleError, match="already exists"):
        extract_bundle(bundle, tmp_path / "out")
    for unsafe in ("../escape.py", "site-packages/../../escape.py", "/abs.py", "other/file.py"):
        hostile = make_bundle(tmp_path / "hostile.zip", extra={unsafe: "x"})
        with pytest.raises(AgentBundleError, match="unsafe path"):
            extract_bundle(hostile, tmp_path / f"hostile-{abs(hash(unsafe))}")
    (tmp_path / "junk.zip").write_bytes(b"not a zip")
    with pytest.raises(AgentBundleError, match="not a valid agent bundle"):
        read_manifest(tmp_path / "junk.zip")


def test_install_self_checks_the_bundle_with_the_launcher_python(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bundle = make_bundle(tmp_path / "bundle.zip")
    digest = sha256_file(bundle)
    install_bundle(home, bundle, digest, expected_version="9.9.9")
    assert (launcher.version_dir(home, digest) / BUNDLE_MANIFEST).is_file()
    assert not list((home / launcher.VERSIONS).glob(".*staging"))

    broken = make_bundle(tmp_path / "broken.zip", version="9.9.8", importable=False)
    with pytest.raises(AgentUpdateError, match="self-check"):
        install_bundle(home, broken, sha256_file(broken), expected_version=None)
    assert not launcher.version_dir(home, sha256_file(broken)).exists()

    other_python = make_bundle(tmp_path / "py.zip", version="9.9.7", python="3.99")
    with pytest.raises(AgentUpdateError, match=r"needs Python 3\.99"):
        install_bundle(home, other_python, sha256_file(other_python), expected_version=None)
    other_platform = "windows_amd64" if current_platform() != "windows_amd64" else "linux_x86_64"
    foreign = make_bundle(tmp_path / "foreign.zip", version="9.9.6", platform=other_platform)
    with pytest.raises(AgentUpdateError, match=f"for {other_platform}"):
        install_bundle(home, foreign, sha256_file(foreign), expected_version=None)


def test_download_verifies_size_and_checksum(tmp_path: Path) -> None:
    source = make_bundle(tmp_path / "bundle.zip")
    url = source.as_uri()
    target = tmp_path / "downloaded.zip"
    download_bundle(
        url,
        target,
        size_bytes=source.stat().st_size,
        digest_sha256=sha256_file(source),
        ssl_context=None,  # type: ignore[arg-type]
    )
    assert sha256_file(target) == sha256_file(source)
    with pytest.raises(AgentUpdateError, match="checksum"):
        download_bundle(
            url,
            tmp_path / "bad.zip",
            size_bytes=source.stat().st_size,
            digest_sha256="0" * 64,
            ssl_context=None,  # type: ignore[arg-type]
        )
    with pytest.raises(AgentUpdateError, match="size"):
        download_bundle(
            url,
            tmp_path / "short.zip",
            size_bytes=1,
            digest_sha256=sha256_file(source),
            ssl_context=None,  # type: ignore[arg-type]
        )
    assert not list(tmp_path.glob("*.part"))


def _installed_home(tmp_path: Path) -> tuple[Path, str]:
    home = tmp_path / "home"
    digest = agent_install.install(home, make_bundle(tmp_path / "current.zip", version="1.0.0"))
    return home, digest


def test_installer_sets_up_launcher_and_current_version(tmp_path: Path) -> None:
    home, digest = _installed_home(tmp_path)
    assert (home / "launcher.py").read_text(encoding="utf-8") == LAUNCHER_SOURCE
    assert launcher.load_state(home) == {"current": digest, "previous": None, "trial": None}
    second = agent_install.install(home, make_bundle(tmp_path / "next.zip", version="1.0.1"))
    state = launcher.load_state(home)
    assert (state["current"], state["previous"]) == (second, digest)


def test_agent_reports_release_and_installs_a_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, digest = _installed_home(tmp_path)
    installation = AgentInstallation(home, digest)
    release = installation.release()
    assert release.managed and release.bundle_digest == digest
    assert release.platform == current_platform()
    assert AgentInstallation(None, None).release().managed is False

    update = make_bundle(tmp_path / "update.zip", version="1.1.0")

    def fake_download(url: str, target: Path, **_: object) -> None:
        target.write_bytes(update.read_bytes())

    monkeypatch.setattr("cueverge.media_agent.agent_update.download_bundle", fake_download)
    command = DeviceAgentUpdateCommand(
        request_id="00000000-0000-0000-0000-000000000001",
        device_session_id="00000000-0000-0000-0000-000000000002",
        controller_epoch="00000000-0000-0000-0000-000000000003",
        version="1.1.0",
        platform=current_platform() or "linux_x86_64",
        digest_sha256=sha256_file(update),
        size_bytes=update.stat().st_size,
        download_url="https://controller:7443/api/device/v2/agent-bundles/x",
    )
    installation.install(command, None)  # type: ignore[arg-type]
    state = launcher.load_state(home)
    assert state["trial"] == command.digest_sha256 and state["current"] == digest
    assert not list((home / "downloads").iterdir())

    with pytest.raises(AgentUpdateError, match="already runs"):
        AgentInstallation(home, command.digest_sha256).install(command, None)  # type: ignore[arg-type]
    with pytest.raises(AgentUpdateError, match="cueverge-agent-install"):
        AgentInstallation(None, None).install(command, None)  # type: ignore[arg-type]

    trial = AgentInstallation(home, command.digest_sha256)
    trial.confirm()
    assert (home / f"{launcher.CONFIRMED_PREFIX}{command.digest_sha256}").exists()


def _scripted_children(monkeypatch: pytest.MonkeyPatch, scripts: dict[str, str]) -> list[str]:
    """Run a script per digest instead of the agent; records the start order."""
    started: list[str] = []

    def command(home: Path, digest: str) -> tuple[list[str], dict[str, str]]:
        started.append(digest)
        script = scripts[digest].replace("HOME", str(home)).replace("DIGEST", digest)
        return [sys.executable, "-c", script], {}

    monkeypatch.setattr(launcher, "child_command", command)
    return started


CONFIRM = (
    "from pathlib import Path; import time; "
    "Path(r'HOME', 'confirmed-DIGEST').write_text('ok'); time.sleep(0.3)"
)


def _with_trial(tmp_path: Path) -> tuple[Path, str, str]:
    home, current = _installed_home(tmp_path)
    trial = agent_install.install(home, make_bundle(tmp_path / "trial.zip", version="2.0.0"))
    state = launcher.load_state(home)
    state.update(current=current, previous=None, trial=trial)
    launcher.write_state(home, state)
    return home, current, trial


def test_launcher_promotes_a_confirmed_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, current, trial = _with_trial(tmp_path)
    started = _scripted_children(monkeypatch, {trial: CONFIRM, current: "raise SystemExit(3)"})
    assert launcher.Launcher(home, trial_seconds=10).run() == 0
    state = launcher.load_state(home)
    assert (state["current"], state["previous"], state["trial"]) == (trial, current, None)
    assert state["last_update"]["outcome"] == "installed"
    assert started == [trial]
    assert not list(home.glob(f"{launcher.CONFIRMED_PREFIX}*"))


def test_launcher_rolls_back_a_trial_that_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, current, trial = _with_trial(tmp_path)
    started = _scripted_children(
        monkeypatch, {trial: "raise SystemExit(1)", current: "raise SystemExit(4)"}
    )
    assert launcher.Launcher(home, trial_seconds=10).run() == 4
    state = launcher.load_state(home)
    assert (state["current"], state["trial"]) == (current, None)
    assert state["last_update"]["outcome"] == "rolled_back"
    assert "exited with code 1" in state["last_update"]["detail"]
    assert started == [trial, current]
    assert not launcher.version_dir(home, trial).exists()
    rolled_back = AgentInstallation(home, current).release().last_update
    assert rolled_back is not None and rolled_back.bundle_digest == trial


def test_launcher_rolls_back_a_trial_that_never_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, current, trial = _with_trial(tmp_path)
    _scripted_children(
        monkeypatch, {trial: "import time; time.sleep(30)", current: "raise SystemExit(5)"}
    )
    assert launcher.Launcher(home, trial_seconds=0.5).run() == 5
    assert "didn't connect" in launcher.load_state(home)["last_update"]["detail"]


def test_launcher_restarts_into_an_update_and_reports_missing_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, current = _installed_home(tmp_path)
    marker = tmp_path / "ran-once"
    script = (
        f"from pathlib import Path; p = Path(r'{marker}'); "
        "raise SystemExit(6) if p.exists() else (p.write_text('x'), SystemExit(75))[1]"
    )
    started = _scripted_children(monkeypatch, {current: script})
    assert launcher.Launcher(home).run() == 6
    assert started == [current, current]
    assert launcher.Launcher(tmp_path / "empty").run() == 1


def test_installed_launcher_runs_standalone(tmp_path: Path) -> None:
    home, _ = _installed_home(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", str(home / "launcher.py")],
        env={"CUEVERGE_AGENT_HOME": str(tmp_path / "missing")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 1
    assert "run cueverge-agent-install" in result.stderr
    assert json.loads((home / "state.json").read_text())["trial"] is None
