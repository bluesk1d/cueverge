from dataclasses import dataclass
from typing import Protocol

from cueverge.core.models import SceneCatalog
from cueverge.gui.auth import AuthService
from cueverge.gui.compiler import DraftCompiler
from cueverge.gui.config import GuiConfig
from cueverge.gui.repository import GuiRepository


class RuntimeStatusSource(Protocol):
    catalog: SceneCatalog

    def status(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class GuiServices:
    config: GuiConfig
    repository: GuiRepository
    auth: AuthService
    compiler: DraftCompiler
    runtime: RuntimeStatusSource

    @classmethod
    def create(cls, config: GuiConfig, runtime: RuntimeStatusSource) -> "GuiServices":
        repository = GuiRepository(config.data_root / "gui.sqlite3")
        repository.migrate()
        return cls(
            config=config,
            repository=repository,
            auth=AuthService(config, repository),
            compiler=DraftCompiler(config.data_root / "compiled"),
            runtime=runtime,
        )
