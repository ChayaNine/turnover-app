# Pull request for AmazonAppDev/ring-api-helloworld

**Title:** Verify webhooks with Ring's HMAC `X-Signature` and accept all documented event types

## How to open it (about 5 minutes)

1. Open https://github.com/AmazonAppDev/ring-api-helloworld and click **Fork**.
2. On your computer:

   ```bash
   git clone https://github.com/<your-username>/ring-api-helloworld.git
   cd ring-api-helloworld
   git checkout -b fix/webhook-hmac-signature
   git am /path/to/turnover/docs/open-source/0001-Verify-webhooks-with-Ring-s-HMAC-X-Signature-and-acc.patch
   git push -u origin fix/webhook-hmac-signature
   ```

3. GitHub shows **Compare & pull request**. The base must be `AmazonAppDev/ring-api-helloworld` `main`. Paste the description below and open it.
4. Copy the PR URL into the Devpost Open Source fields.

---

## Description (paste into GitHub)

### What

The webhook receiver in this sample doesn't authenticate deliveries the way Ring sends them, and it rejects most documented event types. This PR makes `/api/webhook` match the [Ring Partner API notifications spec](https://developer.amazon.com/docs/ring/api-documentation.html#notifications).

### Why

- **Signature.** Ring signs every webhook as `X-Signature: sha256=<hex>`: an HMAC-SHA256 of the raw request body using the app's **HMAC Signing Key**. The handler instead compared an `Authorization: Bearer` header against `RING_WEBHOOK_SECRET`. Ring doesn't send that header, so with the secret set every real delivery got a 401, and without it nothing was verified.
- **Event types.** The Zod schema allowed only `motion_detected`, `device_added`, `device_removed` and an undocumented `person_detected`. Real `button_press`, `device_online`, `device_offline`, `app_integration_*` and `subscription_*` deliveries failed validation and fell through to the generic normalizer, losing `request_id` metadata.
- **Fields.** `sub_type` (the motion classification), `component_ids` (multi-camera devices) and `meta.account_id` weren't modelled.

Developers copy this starter into their own backends, so these patterns spread.

### Changes

- `lib/webhook-signature.ts` (new): `signRingWebhook` and `verifyRingWebhook`, with a constant-time compare and an optional `sha256=` prefix.
- `app/api/webhook/route.ts`:
  - reads the raw bytes and verifies `X-Signature` with `RING_HMAC_KEY` **before** parsing; returns 401 on mismatch and 400 on malformed JSON;
  - keeps `RING_WEBHOOK_SECRET` as a deprecated fallback and warns once when neither is set;
  - sets `device_id` only when `source_type` is `devices`, since `app_integration_*` events carry the account ID in `source`;
  - parses timestamps that arrive as epoch-ms numbers, numeric strings or ISO strings.
- `lib/schemas/webhook.ts` and `lib/types/webhook.ts`:
  - all 10 documented types are exported as `RING_EVENT_TYPES`, and unknown types are still accepted as strings so a new event type doesn't break handlers;
  - `sub_type` is treated as an open vocabulary, as the Oct 5 2026 release notes advise;
  - adds `component_ids`, `timestamp_readable`, `meta.account_id`, and `relationships.devices.data` for subscription events.
- `app/api/webhook/test/route.ts`: builds a documented v1.1 payload, signs it with `RING_HMAC_KEY` and POSTs it through the real `/api/webhook`, so verification and de-duplication are exercised locally. The Developer Playground doesn't deliver webhooks. Supports `?type=` and `?sub_type=`. The simulated bounding boxes used by the canvas overlay are kept and labelled as not part of Ring's payload.
- `README.md` and `.env.example` document `RING_HMAC_KEY`, with an `openssl` recipe for signing a request by hand.

### Testing

`npx tsc --noEmit` passes. With `RING_HMAC_KEY=test-hmac-key npx next dev`:

| Request | Result |
|---|---|
| `button_press` with a forged signature | `401 {"error":"Invalid signature"}` |
| `button_press` signed with the README `openssl` recipe | `{"status":"processed",...}` |
| Same `request_id` again | `{"status":"already_processed",...}` |
| `POST /api/webhook/test?type=motion_detected&sub_type=package_delivery` | `{"status":"delivered","signed":true,"webhook_status":200,...}` |

The signature output was also cross-checked byte for byte against an independent Python implementation.

No behaviour change for users who set neither variable, apart from a one-time console warning.
