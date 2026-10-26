import hashlib
import json
import shutil
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from cueverge.gui.asset_upload import AssetUploadService
from cueverge.gui.lighting_bake import render_lighting_composition
from cueverge.gui.mapper_export import MapperExportService
from cueverge.gui.projector_overlap import (
    ProjectorOverlapService,
    StoredProjectorOverlap,
)
from cueverge.gui.projector_renderer import ProjectorRenderError, render_projector_composition
from cueverge.gui.repository import GuiRepository
from cueverge.gui.stage_coverage import StageCoverage, StageCoverageService
from cueverge.gui.stage_renderer import (
    is_lights_only_stage,
    leds_show_ambient,
    render_stage_composition,
    selected_stage_lighting_surfaces,
    selected_stage_projectors,
    stage_raster_size,
)
from cueverge.gui.sync_rehearsal import SyncAdmission, SyncRehearsalService

MAX_RENDER_RESERVATION_BYTES = 10 * 1024 * 1024 * 1024
RenderJobState = Literal["queued", "running", "completed", "failed", "canceled"]


class RenderJobError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredRenderJob:
    id: str
    project_id: str
    source_revision: int
    composition_id: str
    cache_key: str
    state: RenderJobState
    progress: float
    reserved_bytes: int
    error: str | None
    artifact_id: str | None
    cancel_requested: bool
    created_by: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str


@dataclass(frozen=True)
class RenderInputs:
    composition: dict[str, object]
    source_checksums: dict[str, str]
    environment: dict[str, object]


class RenderJobService:
    def __init__(
        self,
        repository: GuiRepository,
        assets: AssetUploadService,
        output_root: Path,
        mapper_export: MapperExportService | None = None,
        stage_coverage: StageCoverageService | None = None,
        projector_overlap: ProjectorOverlapService | None = None,
        sync_rehearsal: SyncRehearsalService | None = None,
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.output_root = output_root
        self.mapper_export = mapper_export
        self.stage_coverage = stage_coverage
        self.projector_overlap = projector_overlap
        self.sync_rehearsal = sync_rehearsal
        self.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE render_jobs
                SET state = 'queued', progress = 0, started_at = NULL,
                    updated_at = ?
                WHERE state = 'running'
                """,
                (_now(),),
            )
            pending = connection.execute(
                "SELECT 1 FROM render_jobs WHERE state = 'queued' LIMIT 1"
            ).fetchone()
        if pending is not None:
            self._ensure_worker()

    def create(
        self,
        *,
        project_id: str,
        composition_id: str,
        actor: str,
        expected_revision: int | None = None,
    ) -> StoredRenderJob:
        revision = self.repository.get_revision(project_id)
        if expected_revision is not None and revision.revision != expected_revision:
            raise RenderJobError("working revision changed; fetch project context before baking")
        inputs = self._render_inputs(
            revision.draft.editor_metadata,
            composition_id,
            project_id,
        )
        reserved_bytes = _estimate_reservation(inputs.composition)
        if reserved_bytes > MAX_RENDER_RESERVATION_BYTES:
            raise RenderJobError("render exceeds the 10 GiB temporary reservation cap")
        available = shutil.disk_usage(self.output_root).free
        if reserved_bytes > available:
            raise RenderJobError("controller has insufficient free storage for render reservation")
        cache_key = _cache_key(
            composition=inputs.composition,
            source_checksums=inputs.source_checksums,
            source_revision=revision.revision,
            environment=inputs.environment,
        )
        occurred_at = _now()
        job_id = str(uuid4())
        with self._connect() as connection:
            artifact = connection.execute(
                """
                SELECT id, output_path
                FROM render_artifacts
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()
            cache_hit = artifact is not None and Path(str(artifact["output_path"])).is_dir()
            connection.execute(
                """
                INSERT INTO render_jobs(
                    id, project_id, source_revision, composition_id, cache_key,
                    state, progress, reserved_bytes, artifact_id,
                    created_by, created_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    project_id,
                    revision.revision,
                    composition_id,
                    cache_key,
                    "completed" if cache_hit else "queued",
                    1 if cache_hit else 0,
                    reserved_bytes,
                    str(artifact["id"]) if cache_hit else None,
                    actor,
                    occurred_at,
                    occurred_at if cache_hit else None,
                    occurred_at,
                ),
            )
            if cache_hit:
                connection.execute(
                    """
                    INSERT INTO active_render_artifacts(
                        project_id, composition_id, artifact_id, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(project_id, composition_id) DO UPDATE SET
                        artifact_id = excluded.artifact_id,
                        updated_at = excluded.updated_at
                    """,
                    (project_id, composition_id, str(artifact["id"]), occurred_at),
                )
        if not cache_hit:
            self._ensure_worker()
            with self._condition:
                self._condition.notify()
        return self.get(job_id)

    def list(self, project_id: str) -> tuple[StoredRenderJob, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM render_jobs
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return tuple(_stored_job(row) for row in rows)

    def get(self, job_id: str) -> StoredRenderJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM render_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise RenderJobError("render job does not exist")
        return _stored_job(row)

    def cancel(self, job_id: str) -> StoredRenderJob:
        occurred_at = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM render_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise RenderJobError("render job does not exist")
            state = str(row["state"])
            if state == "queued":
                connection.execute(
                    """
                    UPDATE render_jobs
                    SET state = 'canceled', cancel_requested = 1,
                        completed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (occurred_at, occurred_at, job_id),
                )
            elif state == "running":
                connection.execute(
                    """
                    UPDATE render_jobs
                    SET cancel_requested = 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (occurred_at, job_id),
                )
            else:
                raise RenderJobError("only queued or running render jobs can be canceled")
        return self.get(job_id)

    def active_artifact_id(
        self,
        project_id: str,
        composition_id: str,
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT artifact_id
                FROM active_render_artifacts
                WHERE project_id = ? AND composition_id = ?
                """,
                (project_id, composition_id),
            ).fetchone()
        return str(row["artifact_id"]) if row is not None else None

    def _worker_loop(self) -> None:
        while True:
            job = self._next_job()
            if job is None:
                with self._condition:
                    self._condition.wait(timeout=1)
                continue
            self._process(job)

    def _ensure_worker(self) -> None:
        with self._condition:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="cueverge-render-worker",
                daemon=True,
            )
            self._worker.start()

    def _next_job(self) -> StoredRenderJob | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM render_jobs
                WHERE state = 'queued'
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            occurred_at = _now()
            connection.execute(
                """
                UPDATE render_jobs
                SET state = 'running', progress = 0.05,
                    started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (occurred_at, occurred_at, str(row["id"])),
            )
            connection.commit()
        return self.get(str(row["id"]))

    def _process(self, job: StoredRenderJob) -> None:
        try:
            if self._cancel_requested(job.id):
                self._mark_canceled(job.id)
                return
            self._set_progress(job.id, 0.1)
            revision = self.repository.get_revision(job.project_id, job.source_revision)
            composition = _mapping(
                _mapping(revision.draft.editor_metadata.get("compositions")).get(job.composition_id)
            )
            if composition.get("space_kind") == "lighting_surface":
                inputs = self._render_inputs(
                    revision.draft.editor_metadata,
                    job.composition_id,
                    job.project_id,
                )
                if (
                    _cache_key(
                        composition=inputs.composition,
                        source_checksums=inputs.source_checksums,
                        source_revision=job.source_revision,
                        environment=inputs.environment,
                    )
                    != job.cache_key
                ):
                    raise RenderJobError("lighting mapping changed after the bake was queued")
                manifest = render_lighting_composition(
                    project_id=job.project_id,
                    source_revision=job.source_revision,
                    draft=revision.draft,
                    composition_id=job.composition_id,
                    assets=self.assets,
                    mapper_export=_mapping(inputs.environment["mapper_export"]),
                    output_directory=self.output_root / "sets",
                    should_cancel=lambda: self._cancel_requested(job.id),
                    on_progress=lambda value: self._set_progress(job.id, value),
                )
                current = self._render_inputs(
                    revision.draft.editor_metadata,
                    job.composition_id,
                    job.project_id,
                )
                if current != inputs:
                    raise RenderJobError("lighting mapping changed during baking; retry the bake")
            elif composition.get("space_kind") == "stage":
                inputs = self._render_inputs(
                    revision.draft.editor_metadata,
                    job.composition_id,
                    job.project_id,
                )
                if (
                    _cache_key(
                        composition=inputs.composition,
                        source_checksums=inputs.source_checksums,
                        source_revision=job.source_revision,
                        environment=inputs.environment,
                    )
                    != job.cache_key
                ):
                    raise RenderJobError("stage mapping changed after queuing; retry the bake")
                mapper, coverage, overlaps, admission = self._stage_inputs(
                    revision.draft.editor_metadata,
                    composition,
                )
                manifest = render_stage_composition(
                    project_id=job.project_id,
                    source_revision=job.source_revision,
                    draft=revision.draft,
                    composition_id=job.composition_id,
                    assets=self.assets,
                    mapper_export=mapper,
                    coverage=coverage,
                    overlaps=overlaps,
                    sync_admission=admission,
                    output_directory=self.output_root / "sets",
                    on_frame=lambda index, total: self._stage_frame_progress(job.id, index, total),
                )
                if (
                    self._render_inputs(
                        revision.draft.editor_metadata,
                        job.composition_id,
                        job.project_id,
                    )
                    != inputs
                ):
                    raise RenderJobError("stage mapping changed during baking; retry the bake")
            else:
                manifest = render_projector_composition(
                    project_id=job.project_id,
                    source_revision=job.source_revision,
                    draft=revision.draft,
                    composition_id=job.composition_id,
                    assets=self.assets,
                    output_directory=self.output_root / "sets",
                )
            output_path = self.output_root / "sets" / str(manifest["activation_identity"])
            if self._cancel_requested(job.id):
                shutil.rmtree(output_path)
                self._mark_canceled(job.id)
                return
            self._set_progress(job.id, 0.95)
            self._promote(job, manifest, output_path)
        except Exception as error:
            if self._cancel_requested(job.id):
                self._mark_canceled(job.id)
            else:
                self._mark_failed(job.id, str(error))

    def _promote(
        self,
        job: StoredRenderJob,
        manifest: dict[str, object],
        output_path: Path,
    ) -> None:
        occurred_at = _now()
        artifact_id = str(manifest["activation_identity"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO render_artifacts(
                    id, project_id, composition_id, cache_key,
                    output_path, manifest_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    job.project_id,
                    job.composition_id,
                    job.cache_key,
                    str(output_path),
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")),
                    occurred_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO active_render_artifacts(
                    project_id, composition_id, artifact_id, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(project_id, composition_id) DO UPDATE SET
                    artifact_id = excluded.artifact_id,
                    updated_at = excluded.updated_at
                """,
                (job.project_id, job.composition_id, artifact_id, occurred_at),
            )
            connection.execute(
                """
                UPDATE render_jobs
                SET state = 'completed', progress = 1, artifact_id = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (artifact_id, occurred_at, occurred_at, job.id),
            )
            connection.commit()

    def _mark_failed(self, job_id: str, error: str) -> None:
        occurred_at = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE render_jobs
                SET state = 'failed', error = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (error, occurred_at, occurred_at, job_id),
            )

    def _mark_canceled(self, job_id: str) -> None:
        occurred_at = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE render_jobs
                SET state = 'canceled', completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (occurred_at, occurred_at, job_id),
            )

    def _stage_frame_progress(self, job_id: str, index: int, total: int) -> None:
        if index % 10 != 0:
            return
        if self._cancel_requested(job_id):
            raise InterruptedError("stage bake canceled")
        self._set_progress(job_id, 0.1 + 0.8 * index / max(1, total))

    def _set_progress(self, job_id: str, progress: float) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE render_jobs SET progress = ?, updated_at = ? WHERE id = ?
                """,
                (progress, _now(), job_id),
            )

    def _cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested FROM render_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return bool(row["cancel_requested"]) if row is not None else True

    def _render_inputs(
        self,
        editor_metadata: Mapping[str, object],
        composition_id: str,
        project_id: str,
    ) -> RenderInputs:
        compositions = _mapping(editor_metadata.get("compositions"))
        composition = _mapping(compositions.get(composition_id))
        if not composition:
            raise RenderJobError("composition does not exist")
        space_kind = composition.get("space_kind")
        if space_kind not in {"projector_native", "stage", "lighting_surface"}:
            raise RenderJobError("composition space cannot be rendered")
        layers = _mapping(editor_metadata.get("timeline_transform_layers")).get(composition_id, [])
        if not isinstance(layers, list) or (not layers and space_kind != "lighting_surface"):
            raise RenderJobError("composition has no media layers")
        checksums: dict[str, str] = {}
        for item in layers:
            layer = _mapping(item)
            source_id = layer.get("source_id")
            if not isinstance(source_id, str):
                raise RenderJobError("media layer source ID is invalid")
            asset = (
                self.assets.get_original(source_id)[0]
                if space_kind in {"lighting_surface", "stage"}
                else self.assets.get_asset(source_id)
            )
            if asset.project_id != project_id or asset.digest_sha256 is None:
                raise RenderJobError("media layer source is unavailable")
            checksums[source_id] = asset.digest_sha256
        environment: dict[str, object] = {}
        if space_kind == "lighting_surface":
            installation_id = editor_metadata.get("installation_id")
            if not isinstance(installation_id, str) or self.mapper_export is None:
                raise RenderJobError("lighting bake requires a mapped installation")
            environment = {
                "mapper_export": self.mapper_export.export(installation_id),
                "transform_layers": layers,
                "author_layers": _mapping(editor_metadata.get("lighting_author_layers")).get(
                    composition_id,
                    [],
                ),
            }
        if space_kind == "stage":
            mapper, coverage, overlaps, admission = self._stage_inputs(editor_metadata, composition)
            selected_stage_lighting_surfaces(composition, mapper)
            if not admission.allowed:
                raise RenderJobError(f"{admission.code}: {admission.message}")
            environment = {
                "mapper_export": mapper,
                "transform_layers": layers,
                **_stage_raster_identity(composition),
                "coverage": asdict(coverage),
                "overlaps": [
                    {
                        "id": overlap.id,
                        "revision": overlap.revision,
                    }
                    for overlap in overlaps
                ],
                "sync_rehearsal_id": (
                    admission.rehearsal.id if admission.rehearsal is not None else None
                ),
            }
        return RenderInputs(composition, checksums, environment)

    def lighting_artifacts(self, project_id: str, revision: int) -> dict[str, Path]:
        stored = self.repository.get_revision(project_id, revision)
        artifacts: dict[str, Path] = {}
        for composition_id, raw in _mapping(
            stored.draft.editor_metadata.get("compositions")
        ).items():
            composition = _mapping(raw)
            if composition.get("space_kind") != "lighting_surface" and not (
                composition.get("space_kind") == "stage"
                and composition.get("stage_lighting_surfaces")
            ):
                continue
            inputs = self._render_inputs(stored.draft.editor_metadata, composition_id, project_id)
            key = _cache_key(
                composition=inputs.composition,
                source_checksums=inputs.source_checksums,
                source_revision=revision,
                environment=inputs.environment,
            )
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT output_path FROM render_artifacts "
                    "WHERE project_id = ? AND cache_key = ?",
                    (project_id, key),
                ).fetchone()
            if row is None or not Path(str(row["output_path"])).is_dir():
                raise RenderJobError(
                    f"lighting composition {composition_id} needs a current bake; "
                    "media, authoring or installation calibration may have changed"
                )
            artifacts[composition_id] = Path(str(row["output_path"]))
        return artifacts

    def _stage_inputs(
        self,
        editor_metadata: Mapping[str, object],
        composition: dict[str, object] | None = None,
    ) -> tuple[
        dict[str, object],
        StageCoverage,
        tuple[StoredProjectorOverlap, ...],
        SyncAdmission,
    ]:
        installation_id = editor_metadata.get("installation_id")
        if not isinstance(installation_id, str):
            raise RenderJobError("stage render requires an installation ID")
        if (
            self.mapper_export is None
            or self.stage_coverage is None
            or self.projector_overlap is None
            or self.sync_rehearsal is None
        ):
            raise RenderJobError("stage render services are unavailable")
        mapper = self.mapper_export.export(installation_id)
        coverage = self.stage_coverage.analyze(installation_id)
        overlaps = self.projector_overlap.list(installation_id)
        try:
            participating = (
                selected_stage_projectors(composition, mapper)
                if composition is not None
                else tuple(_mapping(item) for item in _list(mapper.get("projectors")))
            )
        except ProjectorRenderError as error:
            raise RenderJobError(str(error)) from error
        resources = tuple(sorted(_string(item.get("resource")) for item in participating))
        if not resources:
            admission = SyncAdmission(
                True,
                "lights_only_stage",
                "No projector output is baked. Rehearse LED timing before live use.",
                None,
            )
        elif len(resources) == 1:
            admission = SyncAdmission(
                True,
                "single_projector_stage",
                "No cross-projector synchronization is needed. "
                "Rehearse LED/projector latency before live use.",
                None,
            )
        else:
            context = self.resource_context(resources)
            admission = self.sync_rehearsal.evaluate(
                resources=resources,
                current_context=context,
                now=_now(),
            )
        return mapper, coverage, overlaps, admission

    def resource_context(
        self,
        resources: tuple[str, ...],
    ) -> dict[str, object]:
        assignments = {
            assignment.resource: assignment
            for assignment in self.repository.list_resource_assignments()
        }
        devices = {device.id: device for device in self.repository.list_devices()}
        context: dict[str, object] = {}
        for resource in resources:
            assignment = assignments.get(resource)
            if assignment is None:
                raise RenderJobError(f"projector resource {resource} has no device assignment")
            device = devices.get(assignment.device_id)
            if device is None:
                raise RenderJobError(f"projector resource {resource} references a missing device")
            context[resource] = {
                "device_id": device.id,
                "certificate_fingerprint": device.certificate_fingerprint_sha256,
                "software_version": device.capabilities.software_version,
                "capability_kind": assignment.capability_kind,
                "device_output": assignment.device_output,
                "assignment_revision": assignment.revision,
            }
        return context

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.repository.database_path,
            isolation_level=None,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def _estimate_reservation(composition: dict[str, object]) -> int:
    if composition.get("space_kind") == "lighting_surface":
        return 256 * 1024 * 1024
    settings = _mapping(composition.get("render_settings"))
    width = _integer(settings.get("render_width"))
    height = _integer(settings.get("render_height"))
    if composition.get("space_kind") == "stage":
        width, height = stage_raster_size(composition)
    numerator = _integer(settings.get("frame_rate_numerator"))
    denominator = _integer(settings.get("frame_rate_denominator"))
    duration_ms = _integer(composition.get("duration_ms"))
    frame_count = max(
        1,
        (duration_ms * numerator + 1000 * denominator - 1) // (1000 * denominator),
    )
    lighting = 256 * 1024 * 1024 if composition.get("stage_lighting_surfaces") else 0
    if is_lights_only_stage(composition):
        # Frames are composed, sampled and discarded one at a time (float32 RGBA).
        return width * height * 4 * 4 * 2 + lighting
    raw_video = width * height * 4 * frame_count
    raw_audio = duration_ms * 48 * 2 * 4
    return (raw_video + raw_audio) * 2 + lighting


def _stage_raster_identity(composition: Mapping[str, object]) -> dict[str, object]:
    """Distinguish bakes drawn before stage rasters followed the composition's shape.

    Only compositions whose stored render height differs from the derived one render
    differently now, so only their cache keys (and bakes) change.
    """
    raster = stage_raster_size(composition)
    stored = _mapping(composition.get("render_settings")).get("render_height")
    identity: dict[str, object] = {} if stored == raster[1] else {"stage_raster": list(raster)}
    if composition.get("stage_lighting_surfaces") and leds_show_ambient(composition):
        # LED sequences now carry coverage so the ambient shows where nothing is drawn.
        identity["led_coverage"] = True
    return identity


def _cache_key(
    *,
    composition: dict[str, object],
    source_checksums: dict[str, str],
    source_revision: int,
    environment: dict[str, object],
) -> str:
    payload = {
        "renderer": "projector-runner-v1",
        "source_revision": source_revision,
        "composition": composition,
        "source_checksums": source_checksums,
        "environment": environment,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _stored_job(row: sqlite3.Row) -> StoredRenderJob:
    return StoredRenderJob(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        source_revision=int(row["source_revision"]),
        composition_id=str(row["composition_id"]),
        cache_key=str(row["cache_key"]),
        state=cast(RenderJobState, str(row["state"])),
        progress=float(row["progress"]),
        reserved_bytes=int(row["reserved_bytes"]),
        error=str(row["error"]) if row["error"] is not None else None,
        artifact_id=(str(row["artifact_id"]) if row["artifact_id"] is not None else None),
        cancel_requested=bool(row["cancel_requested"]),
        created_by=str(row["created_by"]),
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=(str(row["completed_at"]) if row["completed_at"] is not None else None),
        updated_at=str(row["updated_at"]),
    )


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def _string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise RenderJobError("render environment string is invalid")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RenderJobError("render reservation value is invalid")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()
