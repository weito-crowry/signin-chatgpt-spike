"""Stable host-level identity, stored independently from OAuth registration."""

from __future__ import annotations

import uuid

from .credentials import KeyringAccess, KeyringApi

HOST_ID_KEY = "host-id"


class KeyringHostIdentityStore:
    def __init__(self, keyring_api: KeyringApi | None = None) -> None:
        self._keyring = KeyringAccess(keyring_api)

    def get_or_create_host_id(self) -> str:
        existing = self._keyring.load(HOST_ID_KEY)
        if existing is not None:
            self._validate_host_id(existing)
            return existing

        host_id = f"urn:uuid:{uuid.uuid4()}"
        self._keyring.save(HOST_ID_KEY, host_id)
        return host_id

    @staticmethod
    def _validate_host_id(host_id: str) -> None:
        prefix = "urn:uuid:"
        if not host_id.startswith(prefix):
            raise ValueError("Saved host identity is invalid.")
        try:
            uuid.UUID(host_id.removeprefix(prefix))
        except ValueError:
            raise ValueError("Saved host identity is invalid.") from None
