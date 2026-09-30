"""Official loopback Sign in with ChatGPT flow and token lifecycle."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt

from .credentials import DpapiTokenStore, KeyringRegistrationStore
from .errors import ErrorCategory, SpikeError
from .host_identity import KeyringHostIdentityStore
from .models import AuthStatus, LogoutStatus, RegistrationIdentity, TokenSet

ISSUER = "https://auth.openai.com"
OPENID_CONFIGURATION_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZE_ENDPOINT = f"{ISSUER}/api/accounts/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
API_BASE_URL = "https://api.openai.com/v1"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
APP_NAME = "Sign in with ChatGPT Spike"
REDIRECT_PATH = "/auth/callback"
REDIRECT_PORT = 1455
CALLBACK_TIMEOUT_SECONDS = 180.0
REQUIRED_SCOPES = frozenset(
    {
        "openid",
        "profile",
        "email",
        "offline_access",
        "resource.invoke",
        "chatgpt.tokens.use.direct",
    }
)


class AuthFlowError(SpikeError):
    """Authentication error whose message is safe to show to a user."""

    def __init__(
        self,
        message: str,
        *,
        category: ErrorCategory = "authentication",
    ) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class AuthorizationCallback:
    code: str = field(repr=False)
    client_id: str


def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.decode("ascii").rstrip("=")


def build_authorization_url(
    *,
    client_id: str,
    host_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    registration: bool,
) -> str:
    parsed_redirect = urlparse(redirect_uri)
    if (
        parsed_redirect.scheme != "http"
        or parsed_redirect.hostname != "127.0.0.1"
        or parsed_redirect.path != REDIRECT_PATH
        or parsed_redirect.username is not None
        or parsed_redirect.password is not None
    ):
        raise AuthFlowError("The local sign-in callback is invalid.")

    parameters = {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(sorted(REQUIRED_SCOPES)),
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    if registration:
        parameters["agent_name_hint"] = APP_NAME
    return f"{AUTHORIZE_ENDPOINT}?{urlencode(parameters)}"


def validate_callback(
    callback_query: str,
    *,
    expected_state: str,
    expected_client_id: str | None,
) -> AuthorizationCallback:
    values = parse_qs(callback_query, keep_blank_values=True, strict_parsing=False)
    state_values = values.get("state", [])
    if len(state_values) != 1 or not hmac.compare_digest(state_values[0], expected_state):
        raise AuthFlowError("The sign-in callback did not match this authorization attempt.")
    if values.get("error"):
        raise AuthFlowError("Sign-in was not completed.")

    code_values = values.get("code", [])
    if len(code_values) != 1 or not code_values[0]:
        raise AuthFlowError("The sign-in callback did not contain an authorization result.")

    returned_ids = values.get("client_id", [])
    if len(returned_ids) > 1:
        raise AuthFlowError("The sign-in callback contained an invalid registration.")
    returned_client_id = returned_ids[0] if returned_ids else None
    if expected_client_id is not None:
        if returned_client_id is not None and returned_client_id != expected_client_id:
            raise AuthFlowError("The sign-in callback returned a different registration.")
        client_id = expected_client_id
    elif returned_client_id and returned_client_id != DYNAMIC_CLIENT_ID:
        client_id = returned_client_id
    else:
        raise AuthFlowError("The new sign-in registration was not issued.")

    return AuthorizationCallback(code=code_values[0], client_id=client_id)


def subject_digest(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


def verify_id_token(
    id_token: str,
    *,
    client_id: str,
    expected_nonce: str | None,
    jwks: dict[str, object],
    now: datetime | None = None,
) -> dict[str, object]:
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise AuthFlowError("The identity token could not be verified.")
        keys = jwks.get("keys")
        if not isinstance(keys, list):
            raise AuthFlowError("The identity token could not be verified.")
        matching_keys = [
            key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]
        ]
        if len(matching_keys) != 1:
            raise AuthFlowError("The identity token could not be verified.")
        signing_key = jwt.PyJWK.from_dict(matching_keys[0]).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            options={
                "verify_exp": False,
                "verify_iat": False,
                "require": ["exp", "iat", "sub"],
            },
        )
        current_time = (now or datetime.now(UTC)).timestamp()
        expiry = claims.get("exp")
        issued_at = claims.get("iat")
        if not isinstance(expiry, (int, float)) or expiry <= current_time:
            raise AuthFlowError("The identity token has expired.")
        if not isinstance(issued_at, (int, float)) or issued_at > current_time + 30:
            raise AuthFlowError("The identity token is not yet valid.")
        audiences = claims.get("aud")
        if isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != client_id:
            raise AuthFlowError("The identity token audience could not be verified.")
        if expected_nonce is not None:
            nonce = claims.get("nonce")
            if not isinstance(nonce, str) or not hmac.compare_digest(nonce, expected_nonce):
                raise AuthFlowError("The identity token did not match this sign-in attempt.")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise AuthFlowError("The identity token did not contain an account identity.")
        return claims
    except AuthFlowError:
        raise
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise AuthFlowError("The identity token could not be verified.") from None


def receive_loopback_callback(
    authorization_url: str,
    redirect_uri: str,
    timeout: float,
) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.hostname != "127.0.0.1" or parsed.path != REDIRECT_PATH or not parsed.port:
        raise AuthFlowError("The local sign-in callback is invalid.")

    callback: dict[str, str | None] = {"query": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            request = urlparse(self.path)
            if request.path != REDIRECT_PATH:
                self.send_error(404)
                return
            callback["query"] = request.query
            page = b"Sign-in completed. You can return to the terminal."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, format: str, *args: object) -> None:
            # The request target contains the authorization code and state.
            return

    try:
        server = HTTPServer(("127.0.0.1", parsed.port), CallbackHandler)
    except OSError:
        raise AuthFlowError(
            "The local sign-in port is unavailable.", category="transport"
        ) from None

    with server:
        server.timeout = min(timeout, 1.0)
        if not webbrowser.open(authorization_url, new=1, autoraise=True):
            raise AuthFlowError("Could not open the system browser for sign-in.")
        deadline = time.monotonic() + timeout
        while callback["query"] is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthFlowError(
                    "Sign-in timed out before the browser callback arrived.",
                    category="timeout",
                )
            server.timeout = min(remaining, 1.0)
            server.handle_request()
        return callback["query"]


class AuthManager:
    def __init__(
        self,
        *,
        host_identity_store: KeyringHostIdentityStore,
        registration_store: KeyringRegistrationStore,
        token_store: DpapiTokenStore,
        http_client: httpx.Client | None = None,
        callback_receiver: Callable[[str, str, float], str] = receive_loopback_callback,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        callback_timeout_seconds: float = CALLBACK_TIMEOUT_SECONDS,
        expiry_skew_seconds: int = 60,
    ) -> None:
        self.host_identity_store = host_identity_store
        self.registration_store = registration_store
        self.token_store = token_store
        self.http = http_client or httpx.Client(timeout=15.0, trust_env=False)
        self.callback_receiver = callback_receiver
        self.now = now
        self.callback_timeout_seconds = callback_timeout_seconds
        self.expiry_skew_seconds = expiry_skew_seconds

    def login(self) -> AuthStatus:
        # Host identity is stable across sign-out and must exist before OAuth starts.
        host_id = self.host_identity_store.get_or_create_host_id()
        registration = self.registration_store.load()
        requested_client_id = registration.client_id if registration else DYNAMIC_CLIENT_ID
        redirect_uri = f"http://127.0.0.1:{REDIRECT_PORT}{REDIRECT_PATH}"
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = generate_pkce_pair()
        authorize_url = build_authorization_url(
            client_id=requested_client_id,
            host_id=host_id,
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            registration=registration is None,
        )

        openid_configuration = self._load_openid_configuration()
        try:
            callback_query = self.callback_receiver(
                authorize_url, redirect_uri, self.callback_timeout_seconds
            )
        except AuthFlowError:
            raise
        except Exception:
            raise AuthFlowError("The browser sign-in did not complete.") from None

        callback = validate_callback(
            callback_query,
            expected_state=state,
            expected_client_id=registration.client_id if registration else None,
        )
        token_data = self._exchange_code(
            callback,
            verifier=verifier,
            redirect_uri=redirect_uri,
        )
        scopes = self._read_scopes(token_data)
        id_token = self._required_string(token_data, "id_token")
        claims = verify_id_token(
            id_token,
            client_id=callback.client_id,
            expected_nonce=nonce,
            jwks=self._load_jwks(openid_configuration),
            now=self.now(),
        )
        identity = RegistrationIdentity(
            client_id=callback.client_id,
            subject_digest=subject_digest(claims["sub"]),
        )
        if registration and not hmac.compare_digest(
            registration.subject_digest, identity.subject_digest
        ):
            raise AuthFlowError("The signed-in account did not match this registration.")

        tokens = self._make_token_set(token_data, scopes, id_token=id_token)
        self.registration_store.save(identity)
        self.token_store.save(tokens)
        return self._status_for(tokens)

    def status(self) -> AuthStatus:
        return self._status_for(self.token_store.load())

    def access_token(self) -> str:
        tokens = self.token_store.load()
        if tokens is None:
            raise AuthFlowError("Sign in with ChatGPT before making a request.")
        if "chatgpt.tokens.use.direct" not in tokens.scopes:
            raise AuthFlowError("ChatGPT plan usage was not authorized for this sign-in.")
        if tokens.expires_at <= self.now() + timedelta(seconds=self.expiry_skew_seconds):
            tokens = self._refresh_tokens(tokens)
        return tokens.access_token

    def logout(self) -> LogoutStatus:
        tokens = self.token_store.load()
        registration = self.registration_store.load()
        revoked = False
        if tokens is not None and tokens.refresh_token and registration is not None:
            try:
                configuration = self._load_openid_configuration()
                endpoint = configuration.get("revocation_endpoint")
                if isinstance(endpoint, str) and self._is_openai_auth_url(endpoint):
                    response = self.http.post(
                        endpoint,
                        data={
                            "token": tokens.refresh_token,
                            "token_type_hint": "refresh_token",
                            "client_id": registration.client_id,
                        },
                    )
                    revoked = response.status_code == 200
            except Exception:
                # Local sign-out still removes tokens; callers can report revocation UNKNOWN.
                revoked = False
        self.token_store.delete()
        return LogoutStatus(tokens_removed=True, remote_revocation_confirmed=revoked)

    def _refresh_tokens(self, previous: TokenSet) -> TokenSet:
        registration = self.registration_store.load()
        if registration is None or not previous.refresh_token:
            raise AuthFlowError("The sign-in session expired; sign in again.")
        configuration = self._load_openid_configuration()
        form = {
            "grant_type": "refresh_token",
            "client_id": registration.client_id,
            "refresh_token": previous.refresh_token,
            "resource": RESOURCE,
        }
        data = self._post_token_form(form)
        scopes = self._read_scopes(data, default=previous.scopes)
        id_token = data.get("id_token", previous.id_token)
        if not isinstance(id_token, str) or not id_token:
            raise AuthFlowError("The refreshed sign-in response was incomplete.")
        if id_token != previous.id_token:
            claims = verify_id_token(
                id_token,
                client_id=registration.client_id,
                expected_nonce=None,
                jwks=self._load_jwks(configuration),
                now=self.now(),
            )
            if not hmac.compare_digest(registration.subject_digest, subject_digest(claims["sub"])):
                raise AuthFlowError("The refreshed sign-in did not match this registration.")

        refreshed = self._make_token_set(
            data,
            scopes,
            id_token=id_token,
            fallback_refresh_token=previous.refresh_token,
        )
        self.token_store.save(refreshed)
        if "chatgpt.tokens.use.direct" not in refreshed.scopes:
            raise AuthFlowError("ChatGPT plan usage is no longer authorized.")
        return refreshed

    def _exchange_code(
        self,
        callback: AuthorizationCallback,
        *,
        verifier: str,
        redirect_uri: str,
    ) -> dict[str, object]:
        return self._post_token_form(
            {
                "grant_type": "authorization_code",
                "client_id": callback.client_id,
                "code": callback.code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": RESOURCE,
            }
        )

    def _post_token_form(self, form: dict[str, str]) -> dict[str, object]:
        try:
            response = self.http.post(TOKEN_ENDPOINT, data=form)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            raise AuthFlowError("OpenAI token exchange timed out.", category="timeout") from None
        except httpx.HTTPStatusError as error:
            raise AuthFlowError(
                "OpenAI rejected the token exchange.",
                category=self._http_error_category(error.response.status_code),
            ) from None
        except httpx.HTTPError:
            raise AuthFlowError("OpenAI token exchange failed.", category="transport") from None
        except ValueError:
            raise AuthFlowError("OpenAI returned an invalid token response.") from None
        if not isinstance(data, dict):
            raise AuthFlowError("OpenAI returned an invalid token response.")
        return data

    def _load_openid_configuration(self) -> dict[str, object]:
        try:
            response = self.http.get(OPENID_CONFIGURATION_URL)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            raise AuthFlowError("OpenID discovery timed out.", category="timeout") from None
        except httpx.HTTPStatusError as error:
            raise AuthFlowError(
                "OpenID discovery was rejected.",
                category=self._http_error_category(error.response.status_code),
            ) from None
        except httpx.HTTPError:
            raise AuthFlowError("OpenID discovery failed.", category="transport") from None
        except ValueError:
            raise AuthFlowError("OpenID discovery returned invalid metadata.") from None
        if not isinstance(data, dict) or data.get("issuer") != ISSUER:
            raise AuthFlowError("OpenID discovery could not be verified.")
        jwks_uri = data.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not self._is_openai_auth_url(jwks_uri):
            raise AuthFlowError("OpenID discovery could not be verified.")
        return data

    def _load_jwks(self, configuration: dict[str, object]) -> dict[str, object]:
        jwks_uri = configuration.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not self._is_openai_auth_url(jwks_uri):
            raise AuthFlowError("OpenID signing keys could not be verified.")
        try:
            response = self.http.get(jwks_uri)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            raise AuthFlowError(
                "OpenID signing-key lookup timed out.", category="timeout"
            ) from None
        except httpx.HTTPStatusError as error:
            raise AuthFlowError(
                "OpenID signing-key lookup was rejected.",
                category=self._http_error_category(error.response.status_code),
            ) from None
        except httpx.HTTPError:
            raise AuthFlowError(
                "OpenID signing keys could not be verified.", category="transport"
            ) from None
        except ValueError:
            raise AuthFlowError("OpenID returned invalid signing keys.") from None
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            raise AuthFlowError("OpenID signing keys could not be verified.")
        return data

    def _make_token_set(
        self,
        data: dict[str, object],
        scopes: frozenset[str],
        *,
        id_token: str,
        fallback_refresh_token: str | None = None,
    ) -> TokenSet:
        access_token = self._required_string(data, "access_token")
        refresh_token = data.get("refresh_token", fallback_refresh_token)
        if not isinstance(refresh_token, str) or not refresh_token:
            raise AuthFlowError("The sign-in did not provide a renewable session.")
        expires_in = data.get("expires_in")
        if not isinstance(expires_in, (int, float)) or expires_in <= 0:
            raise AuthFlowError("The sign-in response did not include a valid expiry.")
        expiry = self.now() + timedelta(seconds=expires_in)
        return TokenSet(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            scopes=scopes,
            expires_at=expiry,
        )

    @staticmethod
    def _required_string(data: dict[str, object], name: str) -> str:
        value = data.get(name)
        if not isinstance(value, str) or not value:
            raise AuthFlowError("The sign-in response was incomplete.")
        return value

    @staticmethod
    def _read_scopes(
        data: dict[str, object],
        *,
        default: frozenset[str] | None = None,
    ) -> frozenset[str]:
        raw_scopes = data.get("scope")
        if raw_scopes is None and default is not None:
            return default
        if not isinstance(raw_scopes, str):
            raise AuthFlowError("OpenAI did not return granted sign-in scopes.")
        return frozenset(raw_scopes.split())

    def _status_for(self, tokens: TokenSet | None) -> AuthStatus:
        if tokens is None:
            return AuthStatus(False, None, False, False)
        expiry = tokens.expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return AuthStatus(
            authenticated=bool(tokens.access_token),
            expires_at=expiry,
            refresh_available=bool(tokens.refresh_token),
            plan_usage_enabled="chatgpt.tokens.use.direct" in tokens.scopes,
        )

    @staticmethod
    def _http_error_category(status_code: int) -> ErrorCategory:
        if status_code in (401, 403):
            return "authentication"
        if status_code == 429:
            return "rate_limit"
        if 400 <= status_code < 500:
            return "authentication"
        return "transport"

    @staticmethod
    def _is_openai_auth_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme == "https" and parsed.hostname == "auth.openai.com"
