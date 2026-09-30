"""Small value types shared by authentication and the Responses client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    display_name: str


@dataclass(frozen=True)
class UsageSummary:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass(frozen=True)
class ResponseSummary:
    response_id: str | None
    model: str | None
    text: str
    output_items: tuple[dict[str, object], ...]
    usage: UsageSummary
    event_types: tuple[str, ...]
    request_id: str | None
    rate_limit_metadata: dict[str, str]
    streamed_function_call_item_done_count: int = 0


@dataclass(frozen=True)
class RegistrationIdentity:
    """Non-token metadata that identifies one issued OAuth registration."""

    client_id: str
    subject_digest: str = field(repr=False)


@dataclass(frozen=True)
class TokenSet:
    """Renewable credentials; token values are excluded from repr output."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    id_token: str = field(repr=False)
    scopes: frozenset[str]
    expires_at: datetime


@dataclass(frozen=True)
class AuthStatus:
    authenticated: bool
    expires_at: str | None
    refresh_available: bool
    plan_usage_enabled: bool


@dataclass(frozen=True)
class LogoutStatus:
    tokens_removed: bool
    remote_revocation_confirmed: bool
