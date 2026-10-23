"""One-way (Ring-driven) account linking and token lifecycle.

Flow, as Ring documents it:

1. The host installs Turnover in the Ring AppStore, consents, and picks devices.
2. Ring POSTs an authorization code to our Token Exchange URL. We exchange it within its
   60-second lifetime, call /v1/users/me to learn the Ring Account ID through a trusted
   channel, and park the tokens as *unclaimed*.
3. Ring redirects the host's browser to our Account Link URL with ``nonce`` and ``time``.
   The page must present a sign-in: the nonce is matched only after the host signs in.
4. We recompute HMAC-SHA256(key, "<time>:<account_id>") for each unclaimed token
   (URL-safe base64, no padding), compare in constant time, and claim the match.
5. POST /v1/accounts/me/app-integrations with the nonce -> Ring verifies -> ``awaiting``.
6. The host maps devices to properties on our App Homepage (the setup page), then we PATCH
   ``completed``. Splitting 5 and 6 is the point of the two-step design: the integration
   only goes live once the host has finished configuring it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..store import Store

VALIDATION_WINDOW_S = 600


def compute_nonce(time_param: str, account_id: str, hmac_key: str) -> str:
    mac = hmac.new(hmac_key.encode("utf-8"), f"{time_param}:{account_id}".encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def check_freshness(time_param: str, now_ms: int | None = None) -> None:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        delta = (now_ms - int(time_param)) / 1000
    except ValueError as exc:
        raise LinkError("invalid time parameter") from exc
    if delta > VALIDATION_WINDOW_S:
        raise LinkError("link request expired: start again from the Ring app")
    if delta < 0:
        raise LinkError("invalid timestamp: in the future")


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    masked = (local[0] + "***") if len(local) <= 2 else (local[0] + "***" + local[-1])
    return f"{masked}@{domain}"


class LinkError(Exception):
    pass


@dataclass
class TokenRecord:
    account_id: str
    access_token: str
    refresh_token: str | None
    expires_at: float
    claimed_by: str | None = None
    status: str = "unclaimed"  # unclaimed | awaiting | completed

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class TokenManager:
    """Stores Ring tokens per account, refreshes proactively and on 401.

    In production, back this with KMS-encrypted DynamoDB or Secrets Manager; tokens are
    credentials for a customer's cameras."""

    def __init__(self, store: Store, *, client_id: str | None, client_secret: str | None, oauth_url: str, http: httpx.AsyncClient | None = None) -> None:
        self._store = store
        self._client_id = client_id
        self._client_secret = client_secret
        self._oauth_url = oauth_url
        self._http = http or httpx.AsyncClient(timeout=10)
        self._locks: dict[str, asyncio.Lock] = {}
        self.playground_token: str | None = None

    def _get(self, account_id: str) -> TokenRecord | None:
        d = self._store.get("kv", f"tokens:{account_id}")
        return TokenRecord(**d) if d else None

    def _put(self, rec: TokenRecord) -> None:
        self._store.put("kv", f"tokens:{rec.account_id}", rec.to_dict())

    def records(self) -> list[TokenRecord]:
        return [TokenRecord(**d) for d in self._store.list("kv") if "access_token" in d]

    def unclaimed(self) -> list[TokenRecord]:
        return [r for r in self.records() if r.status == "unclaimed"]

    async def exchange_code(self, code: str) -> dict[str, Any]:
        if not (self._client_id and self._client_secret):
            raise LinkError("RING_CLIENT_ID / RING_CLIENT_SECRET not configured")
        resp = await self._http.post(
            self._oauth_url,
            data={"grant_type": "authorization_code", "client_id": self._client_id, "code": code, "client_secret": self._client_secret},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise LinkError(f"token exchange failed ({resp.status_code})")
        return resp.json()

    def park(self, account_id: str, tokens: dict[str, Any]) -> TokenRecord:
        rec = TokenRecord(
            account_id=account_id,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=time.time() + float(tokens.get("expires_in", 14400)),
        )
        self._put(rec)
        return rec

    def match_nonce(self, nonce: str, time_param: str, hmac_key: str) -> TokenRecord | None:
        for rec in self.unclaimed():
            if hmac.compare_digest(compute_nonce(time_param, rec.account_id, hmac_key), nonce):
                return rec
        return None

    def mark(self, account_id: str, *, status: str, claimed_by: str | None = None) -> None:
        rec = self._get(account_id)
        if rec:
            rec.status = status
            if claimed_by:
                rec.claimed_by = claimed_by
            self._put(rec)

    def forget(self, account_id: str) -> None:
        self._store.delete("kv", f"tokens:{account_id}")

    async def refresh(self, account_id: str) -> TokenRecord:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            rec = self._get(account_id)
            if rec is None or not rec.refresh_token:
                raise LinkError("no refresh token: the host must re-link")
            resp = await self._http.post(
                self._oauth_url,
                data={"grant_type": "refresh_token", "refresh_token": rec.refresh_token, "client_id": self._client_id or "", "client_secret": self._client_secret or ""},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code != 200:
                raise LinkError(f"refresh failed ({resp.status_code}): the host must re-link")
            tokens = resp.json()
            # Each refresh returns a new refresh token and invalidates the old one: store it.
            rec.access_token = tokens["access_token"]
            rec.refresh_token = tokens.get("refresh_token", rec.refresh_token)
            rec.expires_at = time.time() + float(tokens.get("expires_in", 14400))
            self._put(rec)
            return rec

    def source_for(self, account_id: str | None):
        """A token source for RingClient: playground token, or an account's tokens with
        proactive refresh 5 minutes before expiry and forced refresh after a 401."""

        async def source(force: bool) -> str:
            if account_id is None:
                if not self.playground_token:
                    raise LinkError("no Ring token: paste a Developer Playground token or link an account")
                return self.playground_token
            rec = self._get(account_id)
            if rec is None:
                raise LinkError("account not linked")
            if force or rec.expires_at - time.time() < 300:
                rec = await self.refresh(account_id)
            return rec.access_token

        return source

    async def refresh_all(self) -> int:
        """Background job (daily): refresh tokens well inside the ~30-day refresh window,
        since an expired refresh token can only be recovered by the host re-linking."""
        n = 0
        for rec in self.records():
            if rec.status == "completed":
                try:
                    await self.refresh(rec.account_id)
                    n += 1
                except LinkError:
                    pass
        return n
