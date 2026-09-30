from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

EXAMPLE_CODE = "EXAMPLE_ONLY_NOT_A_REAL_AUTHORIZATION_CODE"
EXAMPLE_ACCESS_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ACCESS"
EXAMPLE_REFRESH_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_REFRESH"
EXAMPLE_ID_TOKEN = "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ID"
CLIENT_ID = "oaiapp_EXAMPLE_REGISTRATION_ID"
ISSUER = "https://auth.openai.com"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def load_module(name: str):
    qualified_name = f"signin_chatgpt_spike.{name}"
    assert importlib.util.find_spec(qualified_name) is not None, f"{name}.py is not implemented yet"
    return importlib.import_module(qualified_name)


def make_signing_key_and_jwks() -> tuple[object, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "example-test-key", "use": "sig", "alg": "RS256"})
    return private_key, {"keys": [jwk]}


def make_id_token(
    private_key: object,
    *,
    client_id: str = CLIENT_ID,
    nonce: str = "example-nonce",
    subject: str = "example-account-subject",
    issuer: str = ISSUER,
    expires_at: int = 1_900_000_000,
) -> str:
    return jwt.encode(
        {
            "iss": issuer,
            "aud": client_id,
            "sub": subject,
            "nonce": nonce,
            "iat": 1_799_971_200,
            "exp": expires_at,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "example-test-key"},
    )


def test_pkce_uses_s256_without_padding() -> None:
    auth = load_module("auth")

    verifier, challenge = auth.generate_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )

    assert 43 <= len(verifier) <= 128
    assert challenge == expected
    assert "=" not in challenge


def test_authorize_url_uses_official_scopes_resource_and_loopback_callback() -> None:
    auth = load_module("auth")

    authorize_url = auth.build_authorization_url(
        client_id="dynamic_agent_client",
        host_id="urn:uuid:EXAMPLE-HOST-ID",
        redirect_uri="http://127.0.0.1:1455/auth/callback",
        state="example-state",
        nonce="example-nonce",
        code_challenge="example-challenge",
        registration=True,
    )
    query = parse_qs(urlparse(authorize_url).query)

    assert urlparse(authorize_url).scheme == "https"
    assert urlparse(authorize_url).netloc == "auth.openai.com"
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["redirect_uri"] == ["http://127.0.0.1:1455/auth/callback"]
    assert query["resource"] == ["https://api.openai.com/v1"]
    assert set(query["scope"][0].split()) == set(auth.REQUIRED_SCOPES)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["example-state"]
    assert query["nonce"] == ["example-nonce"]
    assert query["ext_agent_host_id"] == ["urn:uuid:EXAMPLE-HOST-ID"]
    assert query["agent_name_hint"] == ["Sign in with ChatGPT Spike"]


@pytest.mark.parametrize(
    "query, expected_client_id",
    [
        ("code=EXAMPLE_ONLY_NOT_A_REAL_TOKEN_AUTH_CODE&state=wrong", None),
        ("error=access_denied&state=expected", None),
        ("code=EXAMPLE_ONLY_NOT_A_REAL_TOKEN_AUTH_CODE&state=expected&client_id=other", CLIENT_ID),
        ("state=expected", None),
    ],
)
def test_invalid_callback_is_rejected(query: str, expected_client_id: str | None) -> None:
    auth = load_module("auth")

    with pytest.raises(auth.AuthFlowError):
        auth.validate_callback(
            query, expected_state="expected", expected_client_id=expected_client_id
        )


def test_reauthorization_reuses_issued_client_id_without_registration_hints() -> None:
    auth = load_module("auth")

    authorize_url = auth.build_authorization_url(
        client_id=CLIENT_ID,
        host_id="urn:uuid:EXAMPLE-HOST-ID",
        redirect_uri="http://127.0.0.1:1455/auth/callback",
        state="example-state",
        nonce="example-nonce",
        code_challenge="example-challenge",
        registration=False,
    )
    query = parse_qs(urlparse(authorize_url).query)

    assert query["client_id"] == [CLIENT_ID]
    assert "agent_name_hint" not in query
    assert "id_token_hint" not in query


def test_id_token_requires_signature_issuer_audience_expiry_and_nonce() -> None:
    auth = load_module("auth")
    private_key, jwks = make_signing_key_and_jwks()
    good_token = make_id_token(private_key)

    claims = auth.verify_id_token(
        good_token,
        client_id=CLIENT_ID,
        expected_nonce="example-nonce",
        jwks=jwks,
        now=datetime(2027, 1, 15, tzinfo=UTC),
    )
    assert claims["sub"] == "example-account-subject"

    wrong_signing_key, _ = make_signing_key_and_jwks()
    for token, expected_nonce in (
        (make_id_token(private_key, nonce="wrong"), "example-nonce"),
        (make_id_token(private_key, client_id="other"), "example-nonce"),
        (make_id_token(private_key, issuer="https://attacker.example"), "example-nonce"),
        (make_id_token(private_key, expires_at=1_700_000_000), "example-nonce"),
        (make_id_token(wrong_signing_key), "example-nonce"),
    ):
        with pytest.raises(auth.AuthFlowError):
            auth.verify_id_token(
                token,
                client_id=CLIENT_ID,
                expected_nonce=expected_nonce,
                jwks=jwks,
                now=datetime(2027, 1, 15, tzinfo=UTC),
            )


def test_subject_digest_does_not_store_raw_subject() -> None:
    auth = load_module("auth")
    digest = auth.subject_digest("example-account-subject")

    assert digest == hashlib.sha256(b"example-account-subject").hexdigest()
    assert "example-account-subject" not in digest


def test_oauth_timeout_is_classified_without_transport_exception_text() -> None:
    auth = load_module("auth")
    manager = auth.AuthManager(
        host_identity_store=FakeHostStore([]),
        registration_store=FakeRegistrationStore([]),
        token_store=FakeTokenStore([]),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ConnectTimeout("EXAMPLE_ONLY_NOT_A_REAL_SECRET")
                )
            )
        ),
    )

    with pytest.raises(auth.AuthFlowError) as raised:
        manager._post_token_form({"grant_type": "authorization_code"})

    assert raised.value.category == "timeout"
    assert "EXAMPLE_ONLY_NOT_A_REAL_SECRET" not in str(raised.value)


def test_reauthentication_rejects_a_different_account_without_replacing_state() -> None:
    auth = load_module("auth")
    models = load_module("models")
    private_key, jwks = make_signing_key_and_jwks()
    order: list[str] = []
    host_store = FakeHostStore(order)
    registration_store = FakeRegistrationStore(order)
    original_registration = models.RegistrationIdentity(
        client_id=CLIENT_ID,
        subject_digest=hashlib.sha256(b"some-other-account").hexdigest(),
    )
    registration_store.value = original_registration
    token_store = FakeTokenStore(order)
    original_tokens = models.TokenSet(
        access_token=EXAMPLE_ACCESS_TOKEN,
        refresh_token=EXAMPLE_REFRESH_TOKEN,
        id_token=EXAMPLE_ID_TOKEN,
        scopes=frozenset({"offline_access"}),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    token_store.value = original_tokens
    token_holder: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={"issuer": ISSUER, "jwks_uri": "https://auth.openai.com/keys"},
            )
        if request.url.path == "/keys":
            return httpx.Response(200, json=jwks)
        if request.url.path == "/api/accounts/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_NEW_ACCESS",
                    "refresh_token": "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_NEW_REFRESH",
                    "id_token": token_holder["id_token"],
                    "expires_in": 3600,
                    "scope": SCOPES,
                },
            )
        raise AssertionError("unexpected account mismatch request")

    def callback_receiver(authorize_url: str, redirect_uri: str, timeout: float) -> str:
        query = parse_qs(urlparse(authorize_url).query)
        token_holder["id_token"] = make_id_token(private_key, nonce=query["nonce"][0])
        return f"code={EXAMPLE_CODE}&state={query['state'][0]}&client_id={CLIENT_ID}"

    manager = auth.AuthManager(
        host_identity_store=host_store,
        registration_store=registration_store,
        token_store=token_store,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        callback_receiver=callback_receiver,
        now=lambda: datetime(2027, 1, 15, tzinfo=UTC),
    )

    with pytest.raises(auth.AuthFlowError) as raised:
        manager.login()

    assert raised.value.category == "authentication"
    assert registration_store.value == original_registration
    assert token_store.value == original_tokens
    assert "registration-save" not in order
    assert "tokens-save" not in order


def test_login_persists_host_before_browser_and_separates_registration_from_tokens() -> None:
    auth = load_module("auth")
    models = load_module("models")
    private_key, jwks = make_signing_key_and_jwks()
    token_holder: dict[str, str] = {}
    order: list[str] = []
    host_store = FakeHostStore(order)
    registration_store = FakeRegistrationStore(order)
    token_store = FakeTokenStore(order)
    request_forms: list[dict[str, list[str]]] = []

    def http_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": "https://auth.openai.com/keys",
                    "revocation_endpoint": "https://auth.openai.com/revoke",
                },
            )
        if request.url.path == "/keys":
            return httpx.Response(200, json=jwks)
        if request.url.path == "/api/accounts/oauth/token":
            request_forms.append(parse_qs(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "access_token": EXAMPLE_ACCESS_TOKEN,
                    "refresh_token": EXAMPLE_REFRESH_TOKEN,
                    "id_token": token_holder["id_token"],
                    "expires_in": 3600,
                    "scope": SCOPES,
                    "token_type": "Bearer",
                },
            )
        raise AssertionError(f"unexpected test URL path: {request.url.path}")

    def callback_receiver(authorize_url: str, redirect_uri: str, timeout: float) -> str:
        order.append("browser")
        query = parse_qs(urlparse(authorize_url).query)
        assert host_store.host_id == "urn:uuid:EXAMPLE-HOST-ID"
        assert redirect_uri == "http://127.0.0.1:1455/auth/callback"
        assert timeout > 0
        token_holder["id_token"] = make_id_token(private_key, nonce=query["nonce"][0])
        return f"code={EXAMPLE_CODE}&state={query['state'][0]}&client_id={CLIENT_ID}"

    http_client = httpx.Client(transport=httpx.MockTransport(http_handler))
    manager = auth.AuthManager(
        host_identity_store=host_store,
        registration_store=registration_store,
        token_store=token_store,
        http_client=http_client,
        callback_receiver=callback_receiver,
        now=lambda: datetime(2027, 1, 15, tzinfo=UTC),
    )

    status = manager.login()

    assert order[:2] == ["host", "registration-read"]
    assert "browser" in order
    assert status.authenticated is True
    assert status.plan_usage_enabled is True
    assert status.refresh_available is True
    assert registration_store.value == models.RegistrationIdentity(
        client_id=CLIENT_ID,
        subject_digest=hashlib.sha256(b"example-account-subject").hexdigest(),
    )
    assert token_store.value.access_token == EXAMPLE_ACCESS_TOKEN
    assert request_forms[0]["code"] == [EXAMPLE_CODE]
    assert request_forms[0]["code_verifier"][0]
    assert "client_secret" not in request_forms[0]


def test_identity_only_scope_is_saved_but_cannot_supply_inference_token() -> None:
    auth = load_module("auth")
    private_key, jwks = make_signing_key_and_jwks()
    token_holder: dict[str, str] = {}
    host_store = FakeHostStore([])
    registration_store = FakeRegistrationStore([])
    token_store = FakeTokenStore([])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={"issuer": ISSUER, "jwks_uri": "https://auth.openai.com/keys"},
            )
        if request.url.path == "/keys":
            return httpx.Response(200, json=jwks)
        if request.url.path == "/api/accounts/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": EXAMPLE_ACCESS_TOKEN,
                    "refresh_token": EXAMPLE_REFRESH_TOKEN,
                    "id_token": token_holder["id_token"],
                    "expires_in": 3600,
                    "scope": "openid profile email offline_access resource.invoke",
                },
            )
        raise AssertionError("unexpected test request")

    def callback_receiver(authorize_url: str, redirect_uri: str, timeout: float) -> str:
        query = parse_qs(urlparse(authorize_url).query)
        token_holder["id_token"] = make_id_token(private_key, nonce=query["nonce"][0])
        return f"code={EXAMPLE_CODE}&state={query['state'][0]}&client_id={CLIENT_ID}"

    manager = auth.AuthManager(
        host_identity_store=host_store,
        registration_store=registration_store,
        token_store=token_store,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        callback_receiver=callback_receiver,
        now=lambda: datetime(2027, 1, 15, tzinfo=UTC),
    )

    status = manager.login()

    assert status.authenticated is True
    assert status.plan_usage_enabled is False
    with pytest.raises(auth.AuthFlowError):
        manager.access_token()


def test_refresh_replaces_rotating_tokens_and_expiry_together() -> None:
    auth = load_module("auth")
    models = load_module("models")
    private_key, jwks = make_signing_key_and_jwks()
    rotated_id_token = make_id_token(private_key)
    token_store = FakeTokenStore([])
    token_store.value = models.TokenSet(
        access_token=EXAMPLE_ACCESS_TOKEN,
        refresh_token=EXAMPLE_REFRESH_TOKEN,
        id_token=EXAMPLE_ID_TOKEN,
        scopes=frozenset({"chatgpt.tokens.use.direct", "offline_access"}),
        expires_at=datetime(2027, 1, 15, tzinfo=UTC),
    )
    registration_store = FakeRegistrationStore([])
    registration_store.value = models.RegistrationIdentity(
        client_id=CLIENT_ID,
        subject_digest=hashlib.sha256(b"example-account-subject").hexdigest(),
    )
    requests: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": "https://auth.openai.com/keys",
                },
            )
        if request.url.path == "/keys":
            return httpx.Response(200, json=jwks)
        if request.url.path == "/api/accounts/oauth/token":
            requests.append(parse_qs(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "access_token": "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ROTATED_ACCESS",
                    "refresh_token": "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ROTATED_REFRESH",
                    "id_token": rotated_id_token,
                    "expires_in": 1800,
                    "scope": SCOPES,
                },
            )
        raise AssertionError("unexpected refresh request")

    manager = auth.AuthManager(
        host_identity_store=FakeHostStore([]),
        registration_store=registration_store,
        token_store=token_store,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: datetime(2027, 1, 15, tzinfo=UTC),
        expiry_skew_seconds=60,
    )

    access_token = manager.access_token()

    assert access_token == "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ROTATED_ACCESS"
    assert token_store.value.refresh_token == "EXAMPLE_ONLY_NOT_A_REAL_TOKEN_ROTATED_REFRESH"
    assert token_store.value.id_token == rotated_id_token
    assert token_store.value.expires_at == datetime(2027, 1, 15, tzinfo=UTC) + timedelta(
        seconds=1800
    )
    assert requests[0]["client_id"] == [CLIENT_ID]
    assert requests[0]["resource"] == ["https://api.openai.com/v1"]
    assert requests[0]["grant_type"] == ["refresh_token"]


def test_logout_revokes_tokens_but_preserves_registration_and_host_identity() -> None:
    auth = load_module("auth")
    models = load_module("models")
    order: list[str] = []
    registration_store = FakeRegistrationStore(order)
    registration = models.RegistrationIdentity(
        client_id=CLIENT_ID, subject_digest="example-subject-digest"
    )
    registration_store.value = registration
    token_store = FakeTokenStore(order)
    token_store.value = models.TokenSet(
        access_token=EXAMPLE_ACCESS_TOKEN,
        refresh_token=EXAMPLE_REFRESH_TOKEN,
        id_token=EXAMPLE_ID_TOKEN,
        scopes=frozenset({"chatgpt.tokens.use.direct", "offline_access"}),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    host_store = FakeHostStore(order)
    calls: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": "https://auth.openai.com/keys",
                    "revocation_endpoint": "https://auth.openai.com/revoke",
                },
            )
        if request.url.path == "/revoke":
            calls.append(parse_qs(request.content.decode()))
            return httpx.Response(200)
        raise AssertionError("unexpected logout request")

    manager = auth.AuthManager(
        host_identity_store=host_store,
        registration_store=registration_store,
        token_store=token_store,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    status = manager.logout()

    assert status.tokens_removed is True
    assert status.remote_revocation_confirmed is True
    assert token_store.value is None
    assert registration_store.value == registration
    assert host_store.host_id == "urn:uuid:EXAMPLE-HOST-ID"
    assert calls == [
        {
            "client_id": [CLIENT_ID],
            "token": [EXAMPLE_REFRESH_TOKEN],
            "token_type_hint": ["refresh_token"],
        }
    ]
    assert order[-1] == "tokens-delete"


def test_logout_clears_local_tokens_when_remote_revocation_is_unconfirmed() -> None:
    auth = load_module("auth")
    models = load_module("models")
    registration_store = FakeRegistrationStore([])
    registration_store.value = models.RegistrationIdentity(
        client_id=CLIENT_ID, subject_digest="example-subject-digest"
    )
    token_store = FakeTokenStore([])
    token_store.value = models.TokenSet(
        access_token=EXAMPLE_ACCESS_TOKEN,
        refresh_token=EXAMPLE_REFRESH_TOKEN,
        id_token=EXAMPLE_ID_TOKEN,
        scopes=frozenset({"offline_access"}),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    host_store = FakeHostStore([])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": "https://auth.openai.com/keys",
                    "revocation_endpoint": "https://auth.openai.com/revoke",
                },
            )
        if request.url.path == "/revoke":
            return httpx.Response(503, json={"detail": EXAMPLE_REFRESH_TOKEN})
        raise AssertionError("unexpected logout request")

    manager = auth.AuthManager(
        host_identity_store=host_store,
        registration_store=registration_store,
        token_store=token_store,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    status = manager.logout()

    assert status.tokens_removed is True
    assert status.remote_revocation_confirmed is False
    assert token_store.value is None
    assert registration_store.value.client_id == CLIENT_ID
    assert host_store.host_id == "urn:uuid:EXAMPLE-HOST-ID"
    assert EXAMPLE_REFRESH_TOKEN not in repr(status)


class FakeHostStore:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.host_id = "urn:uuid:EXAMPLE-HOST-ID"

    def get_or_create_host_id(self) -> str:
        self.order.append("host")
        return self.host_id


class FakeRegistrationStore:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.value = None

    def load(self):
        self.order.append("registration-read")
        return self.value

    def save(self, identity) -> None:
        self.order.append("registration-save")
        self.value = identity


class FakeTokenStore:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.value = None

    def load(self):
        return self.value

    def save(self, tokens) -> None:
        self.order.append("tokens-save")
        self.value = tokens

    def delete(self) -> None:
        self.order.append("tokens-delete")
        self.value = None
