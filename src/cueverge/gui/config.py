import os
from pathlib import Path

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator

from cueverge.gui.contracts import Role


class GuiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_root: Path
    admin_username: str = Field(default="admin", min_length=1, max_length=128)
    admin_password_hash: SecretStr
    admin_role: Role = Role.ADMINISTRATOR
    allowed_origins: tuple[str, ...] = Field(min_length=1)
    web_root: Path | None = None
    secure_cookies: bool = True
    session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=300, le=7 * 24 * 60 * 60)
    login_attempts_per_minute: int = Field(default=5, ge=1, le=100)
    device_enrollment_enabled: bool = False
    device_enrollment_tls_terminated: bool = False
    device_ca_key_password: SecretStr | None = None
    device_pairing_ttl_seconds: int = Field(default=300, ge=60, le=300)
    device_enrollment_attempts_per_minute: int = Field(default=10, ge=1, le=100)
    device_listener_enabled: bool = False
    device_listener_host: str = "0.0.0.0"
    device_listener_port: int = Field(default=8443, ge=1, le=65535)
    device_listener_identities: tuple[str, ...] = ()
    device_listener_public_url: AnyUrl | None = None

    @model_validator(mode="after")
    def validate_origins(self) -> "GuiConfig":
        if any(not origin.startswith(("http://", "https://")) for origin in self.allowed_origins):
            raise ValueError("GUI allowed origins must use http or https")
        if self.secure_cookies and any(
            origin.startswith("http://") and "localhost" not in origin
            for origin in self.allowed_origins
        ):
            raise ValueError("secure GUI cookies require HTTPS for non-localhost origins")
        if self.device_enrollment_enabled:
            if not self.device_enrollment_tls_terminated:
                raise ValueError("device enrollment requires explicit TLS termination confirmation")
            if (
                self.device_ca_key_password is None
                or not self.device_ca_key_password.get_secret_value()
            ):
                raise ValueError("device enrollment requires a device CA key password")
            if self.device_listener_public_url is None:
                raise ValueError("device enrollment requires the public device transport URL")
            if self.device_listener_public_url.scheme != "wss":
                raise ValueError("device listener public URL must use wss")
        if self.device_listener_enabled:
            if not self.device_enrollment_enabled:
                raise ValueError("device listener requires device enrollment")
            if not self.device_listener_identities:
                raise ValueError("device listener requires at least one DNS name or IP address")
        if self.web_root is not None and not (self.web_root / "index.html").is_file():
            raise ValueError("GUI web root must contain index.html")
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
            web_root=(Path(value) if (value := os.environ.get("CUEVERGE_GUI_WEB_ROOT")) else None),
            secure_cookies=os.environ.get("CUEVERGE_GUI_SECURE_COOKIES", "true").lower()
            in {"1", "true", "yes"},
            session_ttl_seconds=int(
                os.environ.get("CUEVERGE_GUI_SESSION_TTL_SECONDS", str(8 * 60 * 60))
            ),
            device_enrollment_enabled=os.environ.get(
                "CUEVERGE_DEVICE_ENROLLMENT_ENABLED", "false"
            ).lower()
            in {"1", "true", "yes"},
            device_enrollment_tls_terminated=os.environ.get(
                "CUEVERGE_DEVICE_ENROLLMENT_TLS_TERMINATED", "false"
            ).lower()
            in {"1", "true", "yes"},
            device_ca_key_password=(
                SecretStr(value)
                if (value := os.environ.get("CUEVERGE_DEVICE_CA_KEY_PASSWORD"))
                else None
            ),
            device_listener_enabled=os.environ.get(
                "CUEVERGE_DEVICE_LISTENER_ENABLED", "false"
            ).lower()
            in {"1", "true", "yes"},
            device_listener_host=os.environ.get("CUEVERGE_DEVICE_LISTENER_HOST", "0.0.0.0"),
            device_listener_port=int(os.environ.get("CUEVERGE_DEVICE_LISTENER_PORT", "8443")),
            device_listener_identities=tuple(
                value.strip()
                for value in os.environ.get("CUEVERGE_DEVICE_LISTENER_IDENTITIES", "").split(",")
                if value.strip()
            ),
            device_listener_public_url=(
                AnyUrl(value)
                if (value := os.environ.get("CUEVERGE_DEVICE_LISTENER_PUBLIC_URL"))
                else None
            ),
        )
