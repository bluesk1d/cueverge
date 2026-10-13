import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from cueverge.gui.contracts import Role


class GuiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_root: Path
    admin_username: str = Field(default="admin", min_length=1, max_length=128)
    admin_password_hash: SecretStr
    admin_role: Role = Role.ADMINISTRATOR
    allowed_origins: tuple[str, ...] = Field(min_length=1)
    secure_cookies: bool = True
    session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=300, le=7 * 24 * 60 * 60)
    login_attempts_per_minute: int = Field(default=5, ge=1, le=100)

    @model_validator(mode="after")
    def validate_origins(self) -> "GuiConfig":
        if any(not origin.startswith(("http://", "https://")) for origin in self.allowed_origins):
            raise ValueError("GUI allowed origins must use http or https")
        if self.secure_cookies and any(
            origin.startswith("http://") and "localhost" not in origin
            for origin in self.allowed_origins
        ):
            raise ValueError("secure GUI cookies require HTTPS for non-localhost origins")
        return self

    @classmethod
    def from_environment(cls) -> "GuiConfig | None":
        if os.environ.get("CUEVERGE_GUI_ENABLED", "").lower() not in {"1", "true", "yes"}:
            return None
        password_hash = os.environ.get("CUEVERGE_GUI_ADMIN_PASSWORD_HASH")
        if not password_hash:
            raise ValueError("CUEVERGE_GUI_ADMIN_PASSWORD_HASH is required when GUI is enabled")
        origins = tuple(
            value.strip()
            for value in os.environ.get(
                "CUEVERGE_GUI_ALLOWED_ORIGINS",
                "http://localhost:5173",
            ).split(",")
            if value.strip()
        )
        return cls(
            data_root=Path(
                os.environ.get(
                    "CUEVERGE_GUI_DATA_ROOT",
                    str(Path.home() / ".local/share/cueverge/gui"),
                )
            ),
            admin_username=os.environ.get("CUEVERGE_GUI_ADMIN_USERNAME", "admin"),
            admin_password_hash=SecretStr(password_hash),
            allowed_origins=origins,
            secure_cookies=os.environ.get("CUEVERGE_GUI_SECURE_COOKIES", "true").lower()
            in {"1", "true", "yes"},
            session_ttl_seconds=int(
                os.environ.get("CUEVERGE_GUI_SESSION_TTL_SECONDS", str(8 * 60 * 60))
            ),
        )
