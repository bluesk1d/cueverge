import argparse
import importlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def load_inventory(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def apt_packages(
    inventory: dict[str, Any],
    *,
    include_optional: bool,
) -> tuple[str, ...]:
    packages: list[str] = []
    for package in inventory["runtime"]["system_packages"]:
        if not package["required"] and not include_optional:
            continue
        version = package.get("version")
        packages.append(f"{package['name']}={version}" if version is not None else package["name"])
    return tuple(packages)


def validate_inventory(
    inventory: dict[str, Any],
    *,
    require_optional: bool,
) -> tuple[str, ...]:
    diagnostics: list[str] = []
    packages = inventory["runtime"]["system_packages"]
    for package in packages:
        if not package["required"] and not require_optional:
            continue
        for executable in package.get("provides", []):
            if shutil.which(executable) is None:
                diagnostics.append(f"required deployment tool is unavailable: {executable}")

    diagnostics.extend(_media_diagnostics(inventory))

    if require_optional and shutil.which("clamscan") is not None:
        version = _run(["clamscan", "--version"])
        if re.search(r"/[0-9]+", version) is None:
            diagnostics.append("ClamAV is required but no virus definitions are installed")
    return tuple(diagnostics)


def version_report(inventory: dict[str, Any], *, include_optional: bool) -> tuple[str, ...]:
    from cueverge.gui import media_tools

    reports: list[str] = [media_tools.engine_identity()]
    seen: set[str] = set()
    for package in inventory["runtime"]["system_packages"]:
        if not package["required"] and not include_optional:
            continue
        for executable in package.get("provides", []):
            if executable in seen or shutil.which(executable) is None:
                continue
            seen.add(executable)
            version_argument = (
                "--version" if executable in {"clamscan", "freshclam"} else "-version"
            )
            reports.append(_run([executable, version_argument]).splitlines()[0])
    return tuple(reports)


def _media_diagnostics(inventory: dict[str, Any]) -> list[str]:
    diagnostics: list[str] = []
    for library in inventory["runtime"]["python_media_libraries"]:
        module = library.get("import", library["name"])
        try:
            importlib.import_module(module)
        except ImportError as error:
            diagnostics.append(f"required Python media library is unavailable: {module} ({error})")
    if diagnostics:
        return diagnostics
    from cueverge.gui import media_tools

    capabilities = inventory["runtime"]["media_capabilities"]
    return list(
        media_tools.capability_diagnostics(
            encoders=tuple(capabilities["encoders"]),
            decoders=tuple(capabilities["decoders"]),
            filters=tuple(capabilities["filters"]),
            container_formats=tuple(capabilities["container_formats"]),
        )
    )


def _run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return completed.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--apt-packages", action="store_true")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--require-optional", action="store_true")
    arguments = parser.parse_args()
    inventory = load_inventory(arguments.inventory)
    if arguments.apt_packages:
        print(" ".join(apt_packages(inventory, include_optional=arguments.include_optional)))
        return 0
    diagnostics = validate_inventory(
        inventory,
        require_optional=arguments.require_optional,
    )
    if diagnostics:
        for diagnostic in diagnostics:
            print(diagnostic, file=sys.stderr)
        return 1
    for report in version_report(
        inventory,
        include_optional=arguments.require_optional,
    ):
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
