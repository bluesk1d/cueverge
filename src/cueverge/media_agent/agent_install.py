"""One-time setup of a launcher-managed media agent so later updates come from the controller."""

import argparse
import json
import os
import shutil
import ssl
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from cueverge.agent_bundle import current_platform, read_manifest, sha256_file
from cueverge.media_agent import launcher
from cueverge.media_agent.agent_update import AgentUpdateError, download_bundle, install_bundle


def install(home: Path, bundle: Path) -> str:
    """Install a bundle as the current version and place its launcher in home."""
    digest = sha256_file(bundle)
    manifest = read_manifest(bundle)
    home.mkdir(parents=True, exist_ok=True)
    (home / launcher.VERSIONS).mkdir(exist_ok=True)
    install_bundle(home, bundle, digest, expected_version=manifest.version)
    shipped = (
        launcher.version_dir(home, digest) / "site-packages" / "cueverge" / "media_agent"
    ) / "launcher.py"
    temporary = home / ".launcher.py.tmp"
    shutil.copyfile(shipped, temporary)
    os.replace(temporary, home / "launcher.py")
    state = launcher.load_state(home)
    previous = state.get("current")
    state.update(
        current=digest,
        previous=previous if previous != digest else state.get("previous"),
        trial=None,
    )
    launcher.write_state(home, state)
    return digest


def fetch_controller_bundle(transport_url: str, credential_root: Path, target_dir: Path) -> Path:
    platform = current_platform()
    if platform is None:
        raise AgentUpdateError("Cueverge has no agent bundles for this operating system/CPU")
    context = ssl.create_default_context(cafile=str(credential_root / "ca-certificates.pem"))
    context.load_cert_chain(
        credential_root / "certificate.pem", credential_root / "private-key.pem"
    )
    parsed = urllib.parse.urlsplit(transport_url)
    base = urllib.parse.urlunsplit(("https", parsed.netloc, "", "", ""))
    query = urllib.parse.urlencode({"platform": platform})
    with urllib.request.urlopen(
        f"{base}/api/device/v2/agent-bundles?{query}", context=context, timeout=30
    ) as response:
        metadata = json.loads(response.read())
    target = target_dir / f"{metadata['digest_sha256']}.zip"
    download_bundle(
        base + metadata["download_path"],
        target,
        size_bytes=int(metadata["size_bytes"]),
        digest_sha256=str(metadata["digest_sha256"]),
        ssl_context=context,
    )
    return target


def service_hint(home: Path) -> str:
    script = home / "launcher.py"
    if sys.platform == "win32":
        return (
            "Point the scheduled task's run-agent.ps1 at:\n"
            f'  uv run --no-project --python 3.12 "{script}"'
        )
    return (
        "Set ExecStart in ~/.config/systemd/user/cueverge-media-agent.service to:\n"
        f"  %h/.local/bin/uv run --no-project --python 3.12 {script}\n"
        "then run: systemctl --user daemon-reload && "
        "systemctl --user restart cueverge-media-agent.service"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Install the controller's media agent bundle under a launcher so the controller "
            "can update this host. Run once on an enrolled host."
        )
    )
    parser.add_argument("--transport-url", default=os.environ.get("CUEVERGE_DEVICE_TRANSPORT_URL"))
    parser.add_argument(
        "--credential-root",
        type=Path,
        default=(
            Path(value) if (value := os.environ.get("CUEVERGE_DEVICE_CREDENTIAL_ROOT")) else None
        ),
    )
    parser.add_argument("--home", type=Path, default=launcher.agent_home())
    parser.add_argument(
        "--bundle",
        type=Path,
        help="Install this bundle file instead of downloading the controller's bundle.",
    )
    arguments = parser.parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix="cueverge-agent-install-") as scratch:
            bundle = arguments.bundle
            if bundle is None:
                if not arguments.transport_url or arguments.credential_root is None:
                    parser.error(
                        "--transport-url and --credential-root (or their CUEVERGE_DEVICE_* "
                        "environment variables) are required to download from the controller"
                    )
                bundle = fetch_controller_bundle(
                    arguments.transport_url, arguments.credential_root, Path(scratch)
                )
            digest = install(arguments.home, bundle)
    except (AgentUpdateError, OSError, ValueError, KeyError) as error:
        print(f"cueverge-agent-install: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"Installed agent bundle {digest[:12]} in {arguments.home}")
    print(service_hint(arguments.home))


if __name__ == "__main__":
    main()
