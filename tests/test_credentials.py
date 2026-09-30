from __future__ import annotations

import base64
import importlib
import importlib.util
import json
from datetime import UTC, datetime

import pytest

EXAMPLE_ACCESS_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS"
EXAMPLE_REFRESH_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_REFRESH"
EXAMPLE_ID_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ID"


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def load_module(name: str):
    qualified_name = f"signin_chatgpt_spike.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{name}.py is not implemented yet"
    return importlib.import_module(qualified_name)


class ExampleProtector:
    """Test double only; it is not an encryption implementation."""

    prefix = b"EXAMPLE_ONLY_NOT_A_REAL_TOKEN_TEST_BLOB:"

    def protect(self, plaintext: bytes) -> bytes:
        return self.prefix + base64.b64encode(plaintext)

    def unprotect(self, protected: bytes) -> bytes:
        assert protected.startswith(self.prefix)
        return base64.b64decode(protected.removeprefix(self.prefix), validate=True)


def test_token_store_round_trips_large_values_without_plaintext_file(tmp_path) -> None:
    credentials = load_module("credentials")
    models = load_module("models")
    token_file = tmp_path / "tokens.dpapi"
    store = credentials.DpapiTokenStore(path=token_file, protector=ExampleProtector())
    large_example_token = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_" * 100
    expected = models.TokenSet(
        access_token=large_example_token,
        refresh_token=EXAMPLE_REFRESH_TOKEN,
        id_token=EXAMPLE_ID_TOKEN,
        scopes=frozenset({"offline_access", "chatgpt.tokens.use.direct"}),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )

    store.save(expected)
    actual = store.load()

    assert actual == expected
    assert all(
        token not in repr(actual)
        for token in (large_example_token, EXAMPLE_REFRESH_TOKEN, EXAMPLE_ID_TOKEN)
    )
    protected_file_contents = token_file.read_bytes()
    assert large_example_token.encode() not in protected_file_contents
    assert EXAMPLE_REFRESH_TOKEN.encode() not in protected_file_contents
    assert EXAMPLE_ID_TOKEN.encode() not in protected_file_contents
    assert (
        json.loads(base64.b64decode(protected_file_contents.removeprefix(ExampleProtector.prefix)))[
            "access_token"
        ]
        == large_example_token
    )


def test_token_store_delete_retains_registration_and_host_id(tmp_path) -> None:
    credentials = load_module("credentials")
    identity_module = load_module("host_identity")
    keyring = MemoryKeyring()
    token_store = credentials.DpapiTokenStore(
        path=tmp_path / "tokens.dpapi", protector=ExampleProtector()
    )
    registration_store = credentials.KeyringRegistrationStore(keyring_api=keyring)
    host_store = identity_module.KeyringHostIdentityStore(keyring_api=keyring)
    host_id = host_store.get_or_create_host_id()
    registration = load_module("models").RegistrationIdentity(
        client_id="oaiapp_EXAMPLE_REGISTRATION_ID", subject_digest="example-subject-digest"
    )
    token_store.save(
        load_module("models").TokenSet(
            access_token=EXAMPLE_ACCESS_TOKEN,
            refresh_token=EXAMPLE_REFRESH_TOKEN,
            id_token=EXAMPLE_ID_TOKEN,
            scopes=frozenset({"chatgpt.tokens.use.direct"}),
            expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    registration_store.save(registration)

    token_store.delete()

    assert token_store.load() is None
    assert registration_store.load() == registration
    assert (
        identity_module.KeyringHostIdentityStore(keyring_api=keyring).get_or_create_host_id()
        == host_id
    )
    assert not (tmp_path / "tokens.dpapi").exists()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows DPAPI is Windows-only")
def test_windows_dpapi_round_trip_uses_fake_payload_only() -> None:
    protector = load_module("credentials").WindowsDpapiProtector()
    example_payload = b"EXAMPLE_ONLY_NOT_A_REAL_TOKEN"

    protected = protector.protect(example_payload)

    assert protected != example_payload
    assert protector.unprotect(protected) == example_payload
