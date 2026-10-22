"""Secret storage: OS keychain first, encrypted file only as a warned fallback.

Stored items (app client secrets and OAuth tokens) never touch config files, logs, reports,
run manifests or the dashboard.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from socialledger import paths
from socialledger.http import register_secret

M = TypeVar("M", bound=BaseModel)

SERVICE = "socialledger"
#: Model fields whose values are registered for verbatim redaction when loaded.
SECRET_FIELDS = ("access_token", "refresh_token", "client_secret")
PASSPHRASE_ENV = "SOCIALLEDGER_TOKEN_PASSPHRASE"  # noqa: S105 - env var name
FILE_STORE_WARNING = (
    "No OS keychain is available, so credentials will be stored in an encrypted file "
    "(~/.socialledger/tokens.enc). Its safety depends entirely on your passphrase "
    f"({PASSPHRASE_ENV}). Prefer a system keychain where possible."
)


class SecretStoreError(Exception):
    pass


class AppCredentials(BaseModel):
    """A developer app the user registered on a platform (bring-your-own)."""

    client_id: str
    client_secret: str | None = None


class OAuthToken(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"  # noqa: S105
    scope: str | None = None
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    obtained_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> OAuthToken:
        now = datetime.now(UTC)
        expires_in = data.get("expires_in")
        refresh_in = data.get("refresh_expires_in") or data.get("refresh_token_expires_in")
        known = {
            "access_token",
            "refresh_token",
            "token_type",
            "scope",
            "expires_in",
            "refresh_expires_in",
            "refresh_token_expires_in",
            "id_token",
        }
        return cls(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            token_type=data.get("token_type") or "Bearer",
            scope=data.get("scope"),
            expires_at=now + timedelta(seconds=int(expires_in)) if expires_in else None,
            refresh_expires_at=now + timedelta(seconds=int(refresh_in)) if refresh_in else None,
            obtained_at=now,
            extra={k: v for k, v in data.items() if k not in known},
        )

    def expires_within(self, delta: timedelta) -> bool:
        return self.expires_at is not None and datetime.now(UTC) + delta >= self.expires_at


class SecretStore(ABC):
    name: str

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    # Typed helpers ---------------------------------------------------------------------

    def get_model(self, key: str, model: type[M]) -> M | None:
        raw = self.get(key)
        if raw is None:
            return None
        obj = model.model_validate_json(raw)
        for field in SECRET_FIELDS:
            value = getattr(obj, field, None)
            if isinstance(value, str):
                register_secret(value)
        return obj

    def set_model(self, key: str, obj: BaseModel) -> None:
        self.set(key, obj.model_dump_json())


class MemoryStore(SecretStore):
    """In-memory store for tests."""

    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


class KeyringStore(SecretStore):
    name = "os-keychain"

    def __init__(self) -> None:
        import keyring

        self._kr = keyring

    def get(self, key: str) -> str | None:
        value: str | None = self._kr.get_password(SERVICE, key)
        return value

    def set(self, key: str, value: str) -> None:
        self._kr.set_password(SERVICE, key, value)

    def delete(self, key: str) -> None:
        import keyring.errors

        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            self._kr.delete_password(SERVICE, key)

    @staticmethod
    def available() -> bool:
        try:
            import keyring
            from keyring.backends import fail

            backend = keyring.get_keyring()
        except Exception:  # pragma: no cover - import/backend discovery failure
            return False
        if isinstance(backend, fail.Keyring):
            return False
        # ChainerBackend with no usable children also counts as unavailable
        return getattr(backend, "priority", 1) > 0


class EncryptedFileStore(SecretStore):
    """Fernet-encrypted JSON file; key derived from a passphrase with scrypt."""

    name = "encrypted-file"

    def __init__(self, path: Path, passphrase: str) -> None:
        if not passphrase:
            raise SecretStoreError(
                f"An encrypted token file needs a passphrase. Set {PASSPHRASE_ENV}."
            )
        self.path = path
        self._passphrase = passphrase.encode()

    def _fernet(self, salt: bytes) -> Any:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(self._passphrase)
        return Fernet(base64.urlsafe_b64encode(key))

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        from cryptography.fernet import InvalidToken

        blob = json.loads(self.path.read_text(encoding="utf-8"))
        salt = base64.b64decode(blob["salt"])
        try:
            plain = self._fernet(salt).decrypt(blob["data"].encode())
        except InvalidToken as exc:
            raise SecretStoreError("Wrong passphrase for the encrypted token file.") from exc
        data: dict[str, str] = json.loads(plain)
        return data

    def _save(self, data: dict[str, str]) -> None:
        salt = os.urandom(16)
        token = self._fernet(salt).encrypt(json.dumps(data).encode()).decode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"v": 1, "salt": base64.b64encode(salt).decode(), "data": token}),
            encoding="utf-8",
        )
        if os.name == "posix":
            tmp.chmod(0o600)
        tmp.replace(self.path)

    def get(self, key: str) -> str | None:
        return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        data = self._load()
        data[key] = value
        self._save(data)

    def delete(self, key: str) -> None:
        data = self._load()
        if data.pop(key, None) is not None:
            self._save(data)


def default_store() -> SecretStore:
    """OS keychain if usable, else the encrypted file (caller must show FILE_STORE_WARNING)."""
    if os.environ.get("SOCIALLEDGER_SECRET_STORE") != "file" and KeyringStore.available():
        return KeyringStore()
    return EncryptedFileStore(paths.home() / "tokens.enc", os.environ.get(PASSPHRASE_ENV, ""))


def app_key(platform: str) -> str:
    return f"{platform}:app"


def token_key(platform: str, account: str = "default") -> str:
    return f"{platform}:token:{account}"


def app_credentials_from_env(platform: str) -> AppCredentials | None:
    """Headless alternative to the keychain: SOCIALLEDGER_<PLATFORM>_CLIENT_ID / _SECRET."""
    prefix = f"SOCIALLEDGER_{platform.upper()}_"
    client_id = os.environ.get(prefix + "CLIENT_ID") or os.environ.get(prefix + "APP_ID")
    if not client_id:
        return None
    secret = os.environ.get(prefix + "CLIENT_SECRET") or os.environ.get(prefix + "APP_SECRET")
    register_secret(secret)
    return AppCredentials(client_id=client_id, client_secret=secret)
