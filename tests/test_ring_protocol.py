"""Ring protocol details that are easy to get wrong."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from turnover.ring import linking, webhooks
from turnover.ring.client import parse_devices, parse_subscriptions

KEY = "test-hmac-key"


def test_signature_is_hex_sha256_over_raw_body_with_prefix():
    body = b'{"meta":{"version":"1.1"},"data":{"id":"x"}}'
    expected = "sha256=" + hmac.new(KEY.encode(), body, hashlib.sha256).hexdigest()
    assert webhooks.sign(KEY, body) == expected
    assert webhooks.verify(KEY, body, expected)
    assert webhooks.verify(KEY, body, expected.removeprefix("sha256="))  # bare hex is accepted


def test_signature_rejects_tampering_and_reserialisation():
    body = b'{"a": 1, "b": 2}'
    sig = webhooks.sign(KEY, body)
    assert not webhooks.verify(KEY, b'{"a": 1, "b": 3}', sig)
    # Same JSON, different bytes: must fail. Verify the bytes, not the parsed object.
    assert not webhooks.verify(KEY, json.dumps(json.loads(body), separators=(",", ":")).encode(), sig)
    assert not webhooks.verify(KEY, body, None)
    assert not webhooks.verify("", body, sig)


def test_parse_motion_keeps_unknown_sub_type():
    when = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
    p = webhooks.build_payload("motion_detected", "ava1.ring.device.A", when, sub_type="animal", request_id="r1", component_ids=["1"])
    evt = webhooks.parse(webhooks.encode(p))
    assert evt.type == "motion_detected" and evt.sub_type == "animal"  # open vocabulary
    assert evt.device_id == "ava1.ring.device.A"
    assert evt.component_ids == ["1"]
    assert evt.occurred_at == when
    assert evt.dedupe_key == "r1"


def test_parse_integration_event_has_no_device():
    when = datetime(2026, 10, 20, tzinfo=timezone.utc)
    evt = webhooks.parse(webhooks.build_payload("app_integration_removed", None, when, request_id="r2", account_id="acct"))
    assert evt.device_id is None and evt.account_id == "acct"


def test_nonce_is_urlsafe_base64_without_padding():
    n = linking.compute_nonce("1771130906289", "acct-123", KEY)
    raw = hmac.new(KEY.encode(), b"1771130906289:acct-123", hashlib.sha256).digest()
    assert n == base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    assert "=" not in n and "+" not in n and "/" not in n


def test_nonce_freshness_window():
    linking.check_freshness("1000", now_ms=1000 + 599_000)
    with pytest.raises(linking.LinkError):
        linking.check_freshness("1000", now_ms=1000 + 601_000)
    with pytest.raises(linking.LinkError):
        linking.check_freshness("5000", now_ms=1000)


def test_mask_email():
    assert linking.mask_email("user@partner.example.com") == "u***r@partner.example.com"
    assert linking.mask_email("ab@x.com") == "a***@x.com"


def test_nonce_matching_picks_the_right_unclaimed_token():
    from turnover.store import Store

    tm = linking.TokenManager(Store(), client_id="c", client_secret="s", oauth_url="https://oauth.invalid")
    tm.park("acct-A", {"access_token": "a", "refresh_token": "ra", "expires_in": 14400})
    tm.park("acct-B", {"access_token": "b", "refresh_token": "rb", "expires_in": 14400})
    nonce = linking.compute_nonce("123", "acct-B", KEY)
    assert tm.match_nonce(nonce, "123", KEY).account_id == "acct-B"
    assert tm.match_nonce(nonce, "124", KEY) is None


def test_encryption_flag_is_a_json_boolean():
    doc = {
        "data": [{"id": "d1", "type": "devices", "attributes": {"name": "Front Door"},
                  "relationships": {"configurations": {"data": {"type": "configurations", "id": "d1"}}}}],
        "included": [{"type": "configurations", "id": "d1", "attributes": {"e2e_encryption": {"enabled": True}, "motion_detection": "on"}}],
    }
    assert parse_devices(doc)[0].encrypted is True
    doc["included"][0]["attributes"]["e2e_encryption"]["enabled"] = "on"  # a string is not true
    assert parse_devices(doc)[0].encrypted is False


def test_location_scoped_subscriptions_are_not_empty():
    doc = {"data": [{"id": "s1", "type": "subscriptions", "attributes": {"status": "active"},
                     "relationships": {"devices": {"data": []}, "locations": {"data": [{"type": "location", "id": "loc-1"}]}}}]}
    sub = parse_subscriptions(doc)[0]
    assert sub.device_ids == [] and sub.location_ids == ["loc-1"]
