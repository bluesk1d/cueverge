import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cueverge.core.loader import ShowPackageError, load_show_package
from cueverge.gui.contracts import DraftProject


class CompilationError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledPackage:
    digest: str
    files: tuple[str, ...]
    scene_ids: tuple[str, ...]


class DraftCompiler:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def compile(
        self,
        *,
        project_id: str,
        revision: int,
        draft: DraftProject,
    ) -> CompiledPackage:
        project_root = self.output_root / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(tempfile.mkdtemp(prefix=".compile-", dir=project_root))
        try:
            scenes_root = temporary_root / "scenes"
            scenes_root.mkdir()
            _write_json(
                temporary_root / "installation.json",
                draft.installation.model_dump(mode="json"),
            )
            for index, scene in enumerate(draft.scenes):
                _write_json(
                    scenes_root / f"scene-{index + 1:04d}.json",
                    scene.model_dump(mode="json"),
                )
            try:
                catalog = load_show_package(temporary_root)
            except ShowPackageError as error:
                raise CompilationError(str(error)) from error
            files = tuple(
                str(path.relative_to(temporary_root))
                for path in sorted(path for path in temporary_root.rglob("*") if path.is_file())
            )
            digest = _package_digest(temporary_root, files)
            destination = project_root / f"r{revision}-{digest[:16]}"
            if destination.exists():
                shutil.rmtree(temporary_root)
            else:
                os.replace(temporary_root, destination)
            return CompiledPackage(
                digest=digest,
                files=files,
                scene_ids=tuple(sorted(catalog.scenes)),
            )
        except BaseException:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)
            raise


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _package_digest(root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in files:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
