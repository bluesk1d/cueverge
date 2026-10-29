import asyncio
import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import Field, TypeAdapter, ValidationError

from cueverge.controller.runtime import ShowMode
from cueverge.core.loader import load_show_package
from cueverge.core.models import MultiLightingLooksDefinition, SceneCatalog
from cueverge.gui.contracts import GuiModel
from cueverge.gui.render_jobs import RenderJobError

if TYPE_CHECKING:
    from cueverge.gui.publication import PublicationService, PublishedPackage
    from cueverge.gui.repository import GuiRepository
    from cueverge.gui.service import RuntimeStatusSource


class ApplyError(RuntimeError):
    pass


class ApplyRequest(GuiModel):
    source_revision: int = Field(ge=1)
    expected_head_revision: int = Field(ge=1)
    confirm_interruption: bool = False
    approve_release: bool = False
    starting_scene_id: str | None = None
    restore_package_id: str | None = None
    preserve_controller_looks: bool = Field(
        default=True,
        description=(
            "Keep the running controller's safe-state (standby) lighting for LED outputs "
            "this show's own ambient lights, when its targets exist in this show's "
            "installation. Another show's ambient baseline is never carried over; outputs "
            "without ambient in this show black out between scenes."
        ),
    )


def now() -> str:
    return datetime.now(UTC).isoformat()


LOGGER = logging.getLogger(__name__)
BAKE_POLL_SECONDS = 1.0


class ApplyService:
    def __init__(
        self,
        repository: "GuiRepository",
        publication: "PublicationService",
        runtime: "RuntimeStatusSource",
        authorize: Callable[[str, str, str | None, bool], bool],
        dependency_digest: Callable[[str, int], str],
    ) -> None:
        self.repository, self.publication, self.runtime = repository, publication, runtime
        self.authorize = authorize
        self.dependency_digest = dependency_digest
        self._lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS apply_jobs(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS applied_catalog(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1), document TEXT NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.repository.database_path, timeout=30)

    def _save(self, document: dict[str, object]) -> None:
        document["updated_at"] = now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO apply_jobs(id,project_id,document) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                (document["id"], document["project_id"], json.dumps(document)),
            )

    def get(self, job_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document FROM apply_jobs WHERE id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise ApplyError("Apply job does not exist")
        return TypeAdapter(dict[str, object]).validate_json(row[0])

    def list(self, project_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document FROM apply_jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 20",
                (project_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _current(self) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document FROM applied_catalog WHERE singleton=1"
            ).fetchone()
        if row is None:
            return None
        try:
            return TypeAdapter(dict[str, object]).validate_json(row[0])
        except ValidationError as error:
            LOGGER.error("Retained catalog record is invalid: %s", error)
            raise ApplyError(
                "Retained catalog record is invalid; restore a consistent controller backup."
            ) from error

    def active(self) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT document FROM apply_jobs WHERE json_extract(document,'$.state') "
                "NOT IN ('completed','failed','cancelled') LIMIT 1",
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _set_current(self, value: dict[str, object]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO applied_catalog(singleton,document) VALUES(1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET document=excluded.document",
                (json.dumps(value),),
            )

    def _catalog_identity(self, package: "PublishedPackage") -> dict[str, object]:
        return {
            "state": "loaded",
            "project_id": package.project_id,
            "project_name": self.repository.get_revision(package.project_id).project.name,
            "revision": package.revision,
            "digest_sha256": package.digest_sha256,
            "loaded_at": now(),
            "generation": str(uuid4()),
        }

    def _check(
        self,
        project_id: str,
        request: ApplyRequest,
        actor: str,
        token_id: str | None = None,
    ) -> None:
        if not self.authorize(project_id, actor, token_id, request.restore_package_id is not None):
            raise ApplyError("Apply authorization changed or the live-output grant is missing.")
        stored = self.repository.get_revision(project_id)
        if stored.revision != request.expected_head_revision:
            raise ApplyError("Saved show changed. Reload and review the new version before Apply.")
        if stored.project.archived_at is not None:
            raise ApplyError("Unarchive this show before Apply.")
        if (
            stored.draft.editor_metadata.get("installation_id")
            != self.repository.get_runtime_installation().id
        ):
            raise ApplyError("This show is not bound to this controller's installation.")
        if not request.confirm_interruption:
            raise ApplyError("Confirm the target and safe playback interruption before Apply.")
        if request.restore_package_id is None and request.source_revision != stored.revision:
            raise ApplyError("Apply must use the exact current saved version.")

    async def start(
        self, project_id: str, request: ApplyRequest, actor: str, token_id: str | None = None
    ) -> dict[str, object]:
        self._check(project_id, request, actor, token_id)
        with self._connect() as connection:
            pending = connection.execute("SELECT document FROM apply_jobs").fetchall()
        if any(
            json.loads(row[0]).get("state") not in {"completed", "failed", "cancelled"}
            for row in pending
        ):
            raise ApplyError("Another Apply is in progress. Inspect or cancel that job first.")
        document: dict[str, object] = {
            "id": str(uuid4()),
            "project_id": project_id,
            "source_revision": request.source_revision,
            "actor": actor,
            "state": "preparing",
            "created_at": now(),
            "package_id": None,
            "previous_package_id": (self._current() or {}).get("package_id"),
            "error": None,
            "cancel_requested": False,
            "interrupted": False,
            "loaded": False,
            "running": False,
            "token_id": token_id,
        }
        self._save(document)
        self._tasks[str(document["id"])] = asyncio.create_task(
            self._run(str(document["id"]), request),
            name=f"apply-{document['id']}",
        )
        self._tasks[str(document["id"])].add_done_callback(
            lambda task: self._unexpected(str(document["id"]), task),
        )
        return document

    def _unexpected(self, job_id: str, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            LOGGER.error("Apply job stopped unexpectedly", exc_info=task.exception())
            self._phase_failure(
                job_id, "Unexpected Apply failure. Inspect controller logs before retrying."
            )

    def cancel(self, job_id: str) -> dict[str, object]:
        document = self.get(job_id)
        if document["state"] not in {"preparing", "baking", "publishing", "staging"}:
            raise ApplyError(
                "Handover cannot be cancelled after interruption; use retained-version recovery."
            )
        document["cancel_requested"] = True
        self._save(document)
        return document

    def stop_requested(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT document FROM apply_jobs").fetchall()
        for row in rows:
            job = json.loads(row[0])
            if job.get("state") not in {"completed", "failed", "cancelled"}:
                job["cancel_requested"] = True
                job["stop_requested"] = True
                self._save(job)

    async def _bake(
        self,
        job_id: str,
        project_id: str,
        revision: int,
        document: dict[str, object],
    ) -> None:
        """Bake every composition and scene output the saved version still needs."""
        render_jobs = self.publication.render_jobs
        requirements = [
            item
            for item in await asyncio.to_thread(
                self.publication.bake_requirements, project_id, revision
            )
            if item.state != "current"
        ]
        if not requirements:
            return
        for item in requirements:
            if item.state == "unavailable":
                raise ApplyError(f"{item.label} can't be baked: {item.error}")
        job_ids: dict[str, str] = {}
        started: list[str] = []
        for item in requirements:
            if item.state in {"queued", "running"} and item.job_id is not None:
                job_ids[item.id] = item.job_id
                continue
            try:
                job = await asyncio.to_thread(
                    render_jobs.create,
                    project_id=project_id,
                    composition_id=item.id,
                    actor=str(document["actor"]),
                    expected_revision=revision,
                )
            except RenderJobError as error:
                raise ApplyError(f"{item.label} can't be baked: {error}") from error
            job_ids[item.id] = job.id
            if job.state != "completed":
                started.append(job.id)
        labels = {item.id: item.label for item in requirements}
        try:
            while True:
                jobs = [
                    await asyncio.to_thread(render_jobs.get, job_ids[bake_id])
                    for bake_id in job_ids
                ]
                self._phase(
                    job_id,
                    "baking",
                    bakes=[
                        {
                            "id": job.composition_id,
                            "label": labels[job.composition_id],
                            "state": job.state,
                            "progress": job.progress,
                            "error": job.error,
                        }
                        for job in jobs
                    ],
                )
                for job in jobs:
                    if job.state in {"failed", "canceled"}:
                        raise ApplyError(
                            f"Baking {labels[job.composition_id]} {job.state}: "
                            f"{job.error or 'the bake did not finish'}"
                        )
                if all(job.state == "completed" for job in jobs):
                    return
                await asyncio.sleep(BAKE_POLL_SECONDS)
        except (ApplyError, asyncio.CancelledError):
            # Only bakes this Apply queued; bakes someone started separately keep running.
            for bake_job_id in started:
                with suppress(RenderJobError):
                    await asyncio.to_thread(render_jobs.cancel, bake_job_id)
            raise

    def _phase(self, job_id: str, state: str, **updates: object) -> dict[str, object]:
        document = self.get(job_id)
        if document.get("cancel_requested") and not document.get("interrupted"):
            raise ApplyError("cancelled")
        document.update(state=state, **updates)
        self._save(document)
        return document

    def _verified_root(self, package: "PublishedPackage") -> Path:
        root = (self.publication.output_root / package.project_id / package.digest_sha256).resolve()
        if not root.is_relative_to(self.publication.output_root.resolve()):
            raise ApplyError("Published package path is invalid.")
        manifest = dict(package.manifest)
        digest = manifest.pop("package_digest_sha256", None)
        actual = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest != package.digest_sha256 or actual != digest:
            raise ApplyError("Published manifest checksum does not match.")
        if json.loads((root / "publication-manifest.json").read_text()) != package.manifest:
            raise ApplyError("Retained publication manifest changed.")
        files = package.manifest.get("files")
        if not isinstance(files, list):
            raise ApplyError("Published file manifest is missing.")
        for item in files:
            if not isinstance(item, dict):
                raise ApplyError("Published file entry is invalid.")
            relative_path = item.get("path")
            if not isinstance(relative_path, str):
                raise ApplyError("Published file path is invalid.")
            path = (root / relative_path).resolve()
            if (
                not path.is_relative_to(root)
                or not path.is_file()
                or path.stat().st_size != item.get("size_bytes")
                or hashlib.sha256(path.read_bytes()).hexdigest() != item.get("digest_sha256")
            ):
                raise ApplyError("Retained package integrity check failed.")
        return root

    async def _dependency_digest(self, project_id: str, revision: int) -> str:
        # Scanning assets and devices takes seconds on large shows; blocking the event loop
        # that long times out device heartbeats and fails the stop handover.
        return await asyncio.to_thread(self.dependency_digest, project_id, revision)

    def _load_verified_catalog(self, package: "PublishedPackage") -> SceneCatalog:
        return load_show_package(self._verified_root(package))

    async def _run(self, job_id: str, request: ApplyRequest) -> None:
        async with self._lock:
            document = self.get(job_id)
            project_id = str(document["project_id"])
            previous_catalog = self.runtime.catalog
            previous_identity = dict(self.runtime.loaded_catalog)
            previous_current = self._current()
            try:
                token_id = str(document["token_id"]) if document.get("token_id") else None
                self._check(project_id, request, str(document["actor"]), token_id)
                if request.restore_package_id is not None:
                    dependencies = await self._dependency_digest(
                        project_id, request.source_revision
                    )
                    package = self.publication.get(request.restore_package_id)
                    if (
                        package.project_id != project_id
                        or package.revision != request.source_revision
                    ):
                        raise ApplyError("Recovery package does not match this show/version.")
                else:
                    await self._bake(job_id, project_id, request.source_revision, document)
                    # After baking: its completed bakes are part of the guarded dependencies.
                    dependencies = await self._dependency_digest(
                        project_id, request.source_revision
                    )
                    self._phase(job_id, "publishing")
                    release = self.publication.release(project_id, request.source_revision)
                    if release.approval_state != "approved":
                        if not request.approve_release:
                            raise ApplyError("Approve this exact saved version before Apply.")
                        self.publication.update_release(
                            project_id=project_id,
                            revision=request.source_revision,
                            name=release.name,
                            summary=release.summary,
                            approval_state="approved",
                            actor=str(document["actor"]),
                            occurred_at=now(),
                        )
                    package = self.publication.publish(
                        project_id=project_id,
                        revision=request.source_revision,
                        actor=str(document["actor"]),
                        occurred_at=now(),
                        lighting_looks=MultiLightingLooksDefinition(
                            schema_version=2,
                            outputs=tuple(
                                item.definition
                                for item in (
                                    self.runtime.catalog.lighting_looks_by_resource.values()
                                    if self.runtime.catalog.lighting_looks_by_resource
                                    else (self.runtime.catalog.lighting_looks,)
                                )
                                if item is not None
                            ),
                        )
                        if request.preserve_controller_looks
                        and (
                            self.runtime.catalog.lighting_looks_by_resource
                            or self.runtime.catalog.lighting_looks
                        )
                        else None,
                    )
                # Hashing every packaged file and parsing the catalog takes seconds; keep the
                # event loop serving device heartbeats meanwhile.
                catalog = await asyncio.to_thread(self._load_verified_catalog, package)
                self.runtime.validate_catalog_handover(catalog)
                start_scene = request.starting_scene_id or (
                    catalog.program.initial_scene_id if catalog.program else None
                )
                if start_scene is not None:
                    catalog.require(start_scene)
                self._phase(job_id, "staging", package_id=package.id)
                previous = self._current()
                protected = (
                    self.publication._package_artifact_digests(
                        self.publication.get(str(previous["package_id"]))
                    )
                    if previous
                    else ()
                )
                _, warnings = await self.publication.stage_runtime(package.id, now(), protected)
                self._check(project_id, request, str(document["actor"]), token_id)
                if dependencies != await self._dependency_digest(
                    project_id, request.source_revision
                ):
                    raise ApplyError(
                        "Installation, assets or device assignments changed during preparation. "
                        "Recheck and retry Apply."
                    )
                was_running = (
                    self.runtime.status().get("active_scene") is not None
                    and self.runtime.status().get("playback_state") != "paused"
                )
                if was_running and start_scene is None:
                    raise ApplyError("Choose the scene to restart from the beginning.")
                self._phase(job_id, "handover", interrupted=True)

                async def activate() -> object:
                    self._check(project_id, request, str(document["actor"]), token_id)
                    if self.get(job_id).get("stop_requested"):
                        raise ApplyError("Apply was stopped by the operator.")
                    if dependencies != await self._dependency_digest(
                        project_id, request.source_revision
                    ):
                        raise ApplyError("Apply dependencies changed before catalog load.")
                    return None

                identity = self._catalog_identity(package)
                await self.runtime.replace_catalog(
                    catalog,
                    identity=identity,
                    start_scene_id=start_scene if was_running else None,
                    before_load=activate,
                )
                if not was_running:
                    await self.runtime.set_show_mode(ShowMode.DISABLED)
                current: dict[str, object] = {"package_id": package.id, "identity": identity}
                self._set_current(current)
                self.publication.record_runtime_load(package.id, now())
                self._phase(
                    job_id,
                    "completed",
                    loaded=True,
                    running=was_running,
                    warnings=warnings,
                    loaded_catalog=identity,
                    starting_scene_id=start_scene,
                    scene_options=[
                        {"id": item.definition.id, "name": item.definition.name}
                        for item in catalog.scenes.values()
                    ],
                )
                self.repository.append_audit(
                    actor=str(document["actor"]),
                    action="runtime.apply",
                    target=package.id,
                    result="success",
                    occurred_at=now(),
                    authorization_context={
                        "project_id": project_id,
                        "revision": package.revision,
                        "generation": str(identity["generation"]),
                        "restarted": was_running,
                    },
                )
            except asyncio.CancelledError:
                self._phase_failure(job_id, "Apply interrupted by controller shutdown.")
                raise
            except (RuntimeError, ValueError, LookupError, OSError, sqlite3.Error) as error:
                interrupted = bool(self.get(job_id).get("interrupted"))
                message = str(error)
                if interrupted:
                    try:
                        await self.runtime.set_show_mode(ShowMode.DISABLED)
                        await self.runtime.replace_catalog(
                            previous_catalog, identity=previous_identity
                        )
                        await self.runtime.set_show_mode(ShowMode.DISABLED)
                        if previous_current is not None:
                            self._set_current(previous_current)
                        else:
                            with self._connect() as connection:
                                connection.execute("DELETE FROM applied_catalog WHERE singleton=1")
                        message += " Previous catalog retained/restored; playback is stopped."
                    except (RuntimeError, ValueError, OSError) as recovery_error:
                        self.runtime.loaded_catalog = {
                            **self.runtime.loaded_catalog,
                            "state": "recovery_required",
                            "error": str(recovery_error),
                        }
                        message += (
                            f" Automatic catalog recovery failed: {recovery_error}. "
                            "Manual recovery is required."
                        )
                self._phase_failure(job_id, message)
            finally:
                self._tasks.pop(job_id, None)

    def _phase_failure(self, job_id: str, message: str) -> None:
        document = self.get(job_id)
        document.update(state="cancelled" if message == "cancelled" else "failed", error=message)
        document["observed_catalog"] = dict(self.runtime.loaded_catalog)
        self._save(document)
        self.repository.append_audit(
            actor=str(document["actor"]),
            action="runtime.apply",
            target=job_id,
            result="failure",
            occurred_at=now(),
            authorization_context={"project_id": str(document["project_id"]), "error": message},
        )

    async def recover(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT document FROM apply_jobs").fetchall()
        for row in rows:
            job = json.loads(row[0])
            if job.get("state") not in {"completed", "failed", "cancelled"}:
                self._phase_failure(
                    job["id"],
                    "Controller restarted during Apply. "
                    "Retained catalog recovery is attempted before accepting input.",
                )
        try:
            current = self._current()
            if current is None:
                return
            package_id = current.get("package_id")
            if not isinstance(package_id, str):
                raise ApplyError("Retained catalog package identity is invalid")
            package = self.publication.get(package_id)
            catalog = await asyncio.to_thread(self._load_verified_catalog, package)
            identity = self._catalog_identity(package)
            await self.runtime.replace_catalog(catalog, identity=identity)
            await self.runtime.set_show_mode(ShowMode.DISABLED)
            self._set_current({"package_id": package.id, "identity": identity})
        except (RuntimeError, ValueError, LookupError, OSError, sqlite3.Error) as error:
            LOGGER.error("Retained catalog recovery failed: %s", error)
            self.runtime.loaded_catalog = {
                "state": "recovery_required",
                "project_id": None,
                "revision": None,
                "digest_sha256": None,
                "loaded_at": None,
                "generation": None,
                "error": (
                    "Retained catalog recovery failed. Inspect logs and restore a verified "
                    "package or consistent controller backup before enabling output."
                ),
            }
            await self.runtime.set_show_mode(ShowMode.DISABLED)

    async def stop(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for outcome in outcomes:
                if isinstance(outcome, BaseException) and not isinstance(
                    outcome, asyncio.CancelledError
                ):
                    raise outcome
