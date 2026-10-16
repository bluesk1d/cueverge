import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from cueverge.gui.collaboration import CollaborationStore
from cueverge.gui.config import GuiConfig
from cueverge.gui.contracts import Role
from cueverge.gui.passwords import verify_password
from cueverge.gui.repository import GuiRepository, StoredSession


class LoginRateLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthenticatedSession:
    username: str
    role: Role
    csrf_hash: str


@dataclass(frozen=True)
class NewSession:
    token: str
    csrf_token: str
    identity: AuthenticatedSession


class AuthService:
    def __init__(
        self,
        config: GuiConfig,
        repository: GuiRepository,
        collaboration: CollaborationStore,
    ) -> None:
        self.config = config
        self.repository = repository
        self.collaboration = collaboration
        self._attempts: dict[str, list[float]] = {}
        self._attempt_lock = threading.Lock()

    def login(self, username: str, password: str, *, remote_key: str) -> NewSession | None:
        self._check_rate_limit(remote_key)
        configured_hash = self.config.admin_password_hash.get_secret_value()
        bootstrap_username = hmac.compare_digest(username, self.config.admin_username)
        bootstrap_password = verify_password(password, configured_hash)
        local_user = self.collaboration.get_user(username)
        valid_local_user = (
            local_user is not None
            and not local_user.disabled
            and verify_password(password, local_user.password_hash)
        )
        occurred_at = _now_iso()
        if not ((bootstrap_username and bootstrap_password) or valid_local_user):
            self.repository.append_audit(
                actor=username,
                action="auth.login",
                target="browser_session",
                result="denied",
                occurred_at=occurred_at,
                authorization_context={"remote_key": remote_key},
            )
            return None
        role = (
            self.config.admin_role
            if bootstrap_username and bootstrap_password
            else local_user.role
            if local_user is not None
            else Role.VIEWER
        )
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + self.config.session_ttl_seconds
        identity = AuthenticatedSession(
            username=username,
            role=role,
            csrf_hash=_digest(csrf_token),
        )
        self.repository.create_session(
            token=token,
            username=identity.username,
            role=identity.role,
            csrf_token=csrf_token,
            expires_at=expires_at,
            created_at=occurred_at,
        )
        self.repository.append_audit(
            actor=identity.username,
            action="auth.login",
            target="browser_session",
            result="success",
            occurred_at=occurred_at,
            authorization_context={"role": identity.role.value},
        )
        return NewSession(token=token, csrf_token=csrf_token, identity=identity)

    def authenticate(self, token: str | None) -> AuthenticatedSession | None:
        if token is None:
            return None
        stored: StoredSession | None = self.repository.get_session(token, now=int(time.time()))
        if stored is None:
            return None
        if stored.username == self.config.admin_username:
            return AuthenticatedSession(stored.username, stored.role, stored.csrf_hash)
        local_user = self.collaboration.get_user(stored.username)
        if local_user is None or local_user.disabled:
            return None
        return AuthenticatedSession(
            stored.username,
            local_user.role,
            stored.csrf_hash,
        )

    def verify_csrf(self, session: AuthenticatedSession, csrf_token: str | None) -> bool:
        if csrf_token is None:
            return False
        return hmac.compare_digest(session.csrf_hash, _digest(csrf_token))

    def logout(self, token: str, session: AuthenticatedSession) -> None:
        self.repository.delete_session(token)
        self.repository.append_audit(
            actor=session.username,
            action="auth.logout",
            target="browser_session",
            result="success",
            occurred_at=_now_iso(),
            authorization_context={"role": session.role.value},
        )

    def _check_rate_limit(self, remote_key: str) -> None:
        now = time.monotonic()
        with self._attempt_lock:
            recent = [
                attempted_at
                for attempted_at in self._attempts.get(remote_key, [])
                if now - attempted_at < 60
            ]
            if len(recent) >= self.config.login_attempts_per_minute:
                raise LoginRateLimitError("too many login attempts")
            recent.append(now)
            self._attempts[remote_key] = recent


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
