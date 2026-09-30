from __future__ import annotations

import importlib
import importlib.util


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


def test_host_id_is_stable_and_independent_of_registration_or_token_lifecycle(tmp_path) -> None:
    credentials = load_module("credentials")
    identity_module = load_module("host_identity")
    keyring = MemoryKeyring()
    host_store = identity_module.KeyringHostIdentityStore(keyring_api=keyring)

    first_host_id = host_store.get_or_create_host_id()
    credentials.KeyringRegistrationStore(keyring_api=keyring).save(
        load_module("models").RegistrationIdentity(
            client_id="oaiapp_EXAMPLE_REGISTRATION_ID", subject_digest="example-subject-digest"
        )
    )
    credentials.DpapiTokenStore(path=tmp_path / "tokens.dpapi").delete()

    second_host_id = identity_module.KeyringHostIdentityStore(
        keyring_api=keyring
    ).get_or_create_host_id()

    assert first_host_id.startswith("urn:uuid:")
    assert second_host_id == first_host_id
