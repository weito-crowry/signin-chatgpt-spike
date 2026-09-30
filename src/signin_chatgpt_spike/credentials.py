"""Protected persistence for registration metadata and OAuth tokens."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Protocol

import keyring

from .errors import SpikeError
from .models import RegistrationIdentity, TokenSet

SERVICE_NAME = "signin-chatgpt-spike"
REGISTRATION_KEY = "registration"


class KeyringApi(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class DataProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, protected: bytes) -> bytes: ...


class CredentialStorageError(SpikeError):
    """A safe, non-secret storage error suitable for CLI display."""


class KeyringAccess:
    """Use the active Windows Credential Manager backend, never a file fallback."""

    def __init__(self, keyring_api: KeyringApi | None = None) -> None:
        if keyring_api is None:
            self._require_windows_credential_manager()
            self._api: KeyringApi = keyring
        else:
            # Explicit injection is used by unit tests; the CLI never injects a backend.
            self._api = keyring_api

    @staticmethod
    def _require_windows_credential_manager() -> None:
        if os.name != "nt":
            raise CredentialStorageError("Windows Credential Manager is required for this spike.")
        try:
            from keyring.backends.Windows import WinVaultKeyring

            backend = keyring.get_keyring()
        except Exception:
            raise CredentialStorageError("Windows Credential Manager is unavailable.") from None
        if not isinstance(backend, WinVaultKeyring):
            raise CredentialStorageError("Windows Credential Manager is unavailable.")

    def load(self, username: str) -> str | None:
        try:
            return self._api.get_password(SERVICE_NAME, username)
        except Exception:
            raise CredentialStorageError("Could not read protected local credentials.") from None

    def save(self, username: str, value: str) -> None:
        try:
            self._api.set_password(SERVICE_NAME, username, value)
        except Exception:
            raise CredentialStorageError("Could not write protected local credentials.") from None

    def delete(self, username: str) -> None:
        try:
            self._api.delete_password(SERVICE_NAME, username)
        except Exception as error:
            # Deleting an absent entry is an idempotent local sign-out.
            if error.__class__.__name__ != "PasswordDeleteError":
                raise CredentialStorageError(
                    "Could not remove protected local credentials."
                ) from None


class KeyringRegistrationStore:
    """Persist the issued client ID and account-matching digest, never tokens."""

    def __init__(self, keyring_api: KeyringApi | None = None) -> None:
        self._keyring = KeyringAccess(keyring_api)

    def load(self) -> RegistrationIdentity | None:
        serialized = self._keyring.load(REGISTRATION_KEY)
        if serialized is None:
            return None
        try:
            data = json.loads(serialized)
            return RegistrationIdentity(
                client_id=data["client_id"], subject_digest=data["subject_digest"]
            )
        except (KeyError, TypeError, ValueError):
            raise CredentialStorageError("Saved registration metadata is invalid.") from None

    def save(self, identity: RegistrationIdentity) -> None:
        serialized = json.dumps(
            {"client_id": identity.client_id, "subject_digest": identity.subject_digest},
            separators=(",", ":"),
            sort_keys=True,
        )
        self._keyring.save(REGISTRATION_KEY, serialized)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


class WindowsDpapiProtector:
    """Protect token bytes for the current Windows user with DPAPI."""

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def unprotect(self, protected: bytes) -> bytes:
        return self._transform(protected, protect=False)

    @staticmethod
    def _transform(payload: bytes, *, protect: bool) -> bytes:
        if os.name != "nt":
            raise CredentialStorageError("Windows DPAPI is required for token storage.")
        try:
            source = ctypes.create_string_buffer(payload, len(payload))
            input_blob = _DataBlob(len(payload), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
            output_blob = _DataBlob()
            crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
            kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
            if protect:
                transform = crypt32.CryptProtectData
                transform.argtypes = [
                    ctypes.POINTER(_DataBlob),
                    ctypes.c_wchar_p,
                    ctypes.POINTER(_DataBlob),
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(_DataBlob),
                ]
                success = transform(
                    ctypes.byref(input_blob),
                    "Sign in with ChatGPT Spike token set",
                    None,
                    None,
                    None,
                    0x1,  # CRYPTPROTECT_UI_FORBIDDEN; default scope is current user.
                    ctypes.byref(output_blob),
                )
            else:
                transform = crypt32.CryptUnprotectData
                transform.argtypes = [
                    ctypes.POINTER(_DataBlob),
                    ctypes.c_void_p,
                    ctypes.POINTER(_DataBlob),
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(_DataBlob),
                ]
                success = transform(
                    ctypes.byref(input_blob),
                    None,
                    None,
                    None,
                    None,
                    0x1,  # CRYPTPROTECT_UI_FORBIDDEN.
                    ctypes.byref(output_blob),
                )
            if not success:
                raise OSError(ctypes.get_last_error())
            try:
                return ctypes.string_at(output_blob.pbData, output_blob.cbData)
            finally:
                kernel32.LocalFree.argtypes = [ctypes.c_void_p]
                kernel32.LocalFree.restype = ctypes.c_void_p
                kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        except CredentialStorageError:
            raise
        except Exception:
            raise CredentialStorageError("Could not protect local token data.") from None


def _default_token_file_path() -> Path:
    if os.name != "nt":
        raise CredentialStorageError("Windows DPAPI is required for token storage.")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise CredentialStorageError("Windows local credential storage is unavailable.")
    return Path(local_app_data) / "signin-chatgpt-spike" / "tokens.dpapi"


class DpapiTokenStore:
    """Persist tokens only as a current-user DPAPI-protected Local AppData blob."""

    def __init__(self, *, path: Path | None = None, protector: DataProtector | None = None) -> None:
        self.path = path if path is not None else _default_token_file_path()
        self._protector = protector if protector is not None else WindowsDpapiProtector()

    def load(self) -> TokenSet | None:
        try:
            protected = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise CredentialStorageError("Could not read protected local credentials.") from None
        try:
            data = json.loads(self._protector.unprotect(protected).decode("utf-8"))
            expiry = datetime.fromisoformat(data["expires_at"])
            return TokenSet(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                id_token=data["id_token"],
                scopes=frozenset(data["scopes"]),
                expires_at=expiry,
            )
        except Exception:
            raise CredentialStorageError(
                "Saved protected token data is invalid or unavailable."
            ) from None

    def save(self, tokens: TokenSet) -> None:
        serialized = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "id_token": tokens.id_token,
                "scopes": sorted(tokens.scopes),
                "expires_at": tokens.expires_at.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        temporary_path: Path | None = None
        try:
            protected = self._protector.protect(serialized)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=".signin-chatgpt-spike-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(protected)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
        except Exception:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise CredentialStorageError("Could not write protected local credentials.") from None

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            raise CredentialStorageError("Could not remove protected local credentials.") from None
