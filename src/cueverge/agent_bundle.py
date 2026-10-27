"""Self-contained media agent bundles that the controller serves for managed agent updates.

A bundle is a zip holding a manifest and a site-packages tree resolved for one platform and
Python minor version. Hosts extract it and run it with their launcher's Python; they need no
package index, compiler or virtual environment.
"""

import argparse
import hashlib
import json
import platform as host_platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from cueverge.protocol.device_v2 import AgentPlatform

BUNDLE_MANIFEST = "cueverge-agent-bundle.json"
BUNDLE_PYTHON = "3.12"
SITE_PACKAGES = "site-packages"
MAX_BUNDLE_UNPACKED_BYTES = 2_000_000_000

# uv --python-platform targets for each bundle platform.
UV_TARGETS: dict[AgentPlatform, str] = {
    "linux_x86_64": "x86_64-manylinux_2_28",
    "linux_aarch64": "aarch64-manylinux_2_28",
    "windows_amd64": "x86_64-pc-windows-msvc",
}


class AgentBundleError(RuntimeError):
    pass


class AgentBundleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    version: str = Field(min_length=1, max_length=128)
    build: str = Field(min_length=1, max_length=128)
    platform: AgentPlatform
    python: str = Field(pattern=r"^3\.[0-9]+$")
    created_at: datetime


def current_platform() -> AgentPlatform | None:
    machine = host_platform.machine().lower()
    if sys.platform.startswith("linux"):
        if machine in {"x86_64", "amd64"}:
            return "linux_x86_64"
        if machine in {"aarch64", "arm64"}:
            return "linux_aarch64"
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "windows_amd64"
    return None


def current_python() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest(bundle: Path) -> AgentBundleManifest:
    try:
        with zipfile.ZipFile(bundle) as archive:
            return AgentBundleManifest.model_validate_json(archive.read(BUNDLE_MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as error:
        raise AgentBundleError(f"{bundle.name} is not a valid agent bundle: {error}") from error


def extract_bundle(bundle: Path, destination: Path) -> AgentBundleManifest:
    """Extract into a new destination, refusing paths that escape it or oversized archives."""
    manifest = read_manifest(bundle)
    if destination.exists():
        raise AgentBundleError(f"{destination} already exists")
    with zipfile.ZipFile(bundle) as archive:
        members = archive.infolist()
        if sum(member.file_size for member in members) > MAX_BUNDLE_UNPACKED_BYTES:
            raise AgentBundleError("agent bundle unpacks beyond the size limit")
        for member in members:
            name = PurePosixPath(member.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in member.filename
                or ":" in member.filename
                or name.parts[0] not in {SITE_PACKAGES, BUNDLE_MANIFEST}
            ):
                raise AgentBundleError(f"agent bundle contains an unsafe path: {member.filename}")
        destination.mkdir(parents=True)
        archive.extractall(destination)
    return manifest


def build_bundles(
    output: Path,
    *,
    project: Path,
    platforms: tuple[AgentPlatform, ...],
    build: str,
    uv: str = "uv",
) -> tuple[Path, ...]:
    """Build one bundle per platform from the locked runtime dependencies of project."""
    output.mkdir(parents=True, exist_ok=True)
    version = _project_version(project)
    created: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="cueverge-agent-bundle-") as scratch:
        work = Path(scratch)
        requirements = work / "requirements.txt"
        _run(
            uv,
            "export",
            "--frozen",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--project",
            str(project),
            "--output-file",
            str(requirements),
            "--quiet",
        )
        _run(uv, "build", "--wheel", "--out-dir", str(work / "dist"), "--quiet", str(project))
        [wheel] = sorted((work / "dist").glob("cueverge-*.whl"))
        for platform in platforms:
            tree = work / platform
            _run(
                uv,
                "pip",
                "install",
                "--quiet",
                "--python-version",
                BUNDLE_PYTHON,
                "--python-platform",
                UV_TARGETS[platform],
                "--only-binary",
                ":all:",
                "--no-deps",
                "--target",
                str(tree / SITE_PACKAGES),
                "--requirement",
                str(requirements),
                str(wheel),
            )
            shutil.rmtree(tree / SITE_PACKAGES / "bin", ignore_errors=True)
            manifest = AgentBundleManifest(
                version=version,
                build=build,
                platform=platform,
                python=BUNDLE_PYTHON,
                created_at=datetime.now(UTC),
            )
            target = output / f"cueverge-agent-{platform}.zip"
            partial = target.with_suffix(".zip.part")
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr(BUNDLE_MANIFEST, manifest.model_dump_json(indent=2))
                for path in sorted((tree / SITE_PACKAGES).rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts:
                        archive.write(path, path.relative_to(tree).as_posix())
            partial.replace(target)
            created.append(target)
    return tuple(created)


def _project_version(project: Path) -> str:
    import tomllib

    data = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _run(*command: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AgentBundleError(f"{' '.join(command[:3])} failed: {result.stderr.strip()[-2000:]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build controller-served media agent bundles.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument(
        "--platform",
        action="append",
        choices=get_args(AgentPlatform),
        help="Repeat for several platforms; defaults to all supported platforms.",
    )
    parser.add_argument(
        "--build",
        default=datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
        help="Build label shown to operators, such as a git revision.",
    )
    arguments = parser.parse_args()
    platforms = tuple(arguments.platform or get_args(AgentPlatform))
    for bundle in build_bundles(
        arguments.output, project=arguments.project, platforms=platforms, build=arguments.build
    ):
        print(json.dumps({"bundle": str(bundle), "sha256": sha256_file(bundle)}))


if __name__ == "__main__":
    main()
