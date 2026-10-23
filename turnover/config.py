"""Runtime configuration, read once from the environment.

Secrets (client secret, HMAC signing key, tokens) only ever come from the environment or
from AWS Secrets Manager in production. Nothing secret is written to the repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _secret(name: str) -> str | None:
    """Secrets may be injected from Secrets Manager; "unset" is the deploy placeholder."""
    raw = (os.getenv(name) or "").strip()
    return None if raw in ("", "unset") else raw


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Ring Partner API (Amazon Vision)
    ring_api_base: str = field(default_factory=lambda: os.getenv("RING_API_BASE", "https://api.amazonvision.com"))
    ring_oauth_url: str = field(default_factory=lambda: os.getenv("RING_OAUTH_URL", "https://oauth.ring.com/oauth/token"))
    ring_client_id: str | None = field(default_factory=lambda: os.getenv("RING_CLIENT_ID"))
    ring_client_secret: str | None = field(default_factory=lambda: _secret("RING_CLIENT_SECRET"))
    # One key, two encodings: hex for webhook signatures, URL-safe base64 for linking nonces.
    ring_hmac_key: str | None = field(default_factory=lambda: _secret("RING_HMAC_KEY"))
    # A Developer Playground token (~30 min) for local development and the demo.
    ring_access_token: str | None = field(default_factory=lambda: _secret("RING_ACCESS_TOKEN"))

    # Storage
    db_path: str = field(default_factory=lambda: os.getenv("TURNOVER_DB", "turnover.db"))

    # AWS
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))
    bedrock_model_id: str = field(default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0"))
    bedrock_enabled: bool = field(default_factory=lambda: _bool("BEDROCK_ENABLED", False))
    bedrock_guardrail_id: str | None = field(default_factory=lambda: os.getenv("BEDROCK_GUARDRAIL_ID") or None)
    sns_topic_arn: str | None = field(default_factory=lambda: os.getenv("SNS_TOPIC_ARN"))

    # Demo mode: virtual clock, scenario player, synthetic devices. Never on in production.
    demo_mode: bool = field(default_factory=lambda: _bool("TURNOVER_DEMO", True))
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://localhost:8000"))

    # Partner sign-in for the account-link page (demo identity store).
    host_email: str = field(default_factory=lambda: os.getenv("HOST_EMAIL", "host@example.com"))
    host_password: str = field(default_factory=lambda: os.getenv("HOST_PASSWORD", "change-me"))
    # Optional: put the dashboard and its API behind HTTP Basic auth (user "host").
    # Ring-facing routes (/webhooks, /ring/*) stay open: they authenticate by signature/nonce.
    dashboard_password: str | None = field(default_factory=lambda: _secret("DASHBOARD_PASSWORD"))

    @property
    def webhook_signing_key(self) -> str:
        # Demo mode signs simulated deliveries with a local key so verification always runs.
        return self.ring_hmac_key or ("turnover-demo-signing-key" if self.demo_mode else "")


def load() -> Settings:
    return Settings()
