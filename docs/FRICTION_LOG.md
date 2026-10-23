# Friction log

Each entry has: the task, the steps taken, expected vs actual, severity, workaround, and a suggestion.

Severity scale: **High** blocks or silently breaks a production integration · **Medium** costs hours · **Low** costs minutes.

> Entries 1–6 happened while building Turnover. Entries marked **[confirm]** depend on your own Playground session: check them, add what you actually saw (error text, timings), and delete any that didn't happen to you.

---

### 1. The webhook signature format is out of reach for AI-assisted reading
- **Task:** Find the exact `X-Signature` format (header name, encoding, what is signed) to implement verification.
- **Steps:** Opened the Ring Partner API documentation page, then asked an AI coding assistant to extract the Notifications section, then fetched the `#notifications` anchor directly.
- **Expected:** The webhook authentication section and its payload examples.
- **Actual:** The page is one document of about 120,000 characters. The fetcher returned the first ~100,000, which end in the Partner-Initiated OAuth section, so Notifications was never reached. A second pass with an offset returned the Access Tokens section instead. The only signature detail visible was one aside in the nonce section ("webhook signatures use hex encoding with a `sha256=` prefix").
- **Severity:** Medium
- **Workaround:** Read the source of two community SDKs on PyPI (`ringpartner`, `ring-sandbox`) to confirm `X-Signature: sha256=<hex HMAC-SHA256 of the raw body>`, then cross-checked it against the hints in the nonce section.
- **Suggestion:** Split the reference into one page per section, or publish `llms.txt` and per-section Markdown. Add official test vectors (key, body, expected header) for webhook signatures and for nonces.

### 2. The official starter's webhook handler doesn't verify Ring's signature
- **Task:** Reuse `AmazonAppDev/ring-api-helloworld`'s webhook route as a reference.
- **Steps:** Read `app/api/webhook/route.ts`, `lib/schemas/webhook.ts` and `lib/types/webhook.ts`.
- **Expected:** `X-Signature` HMAC verification over the raw body, and all documented event types.
- **Actual:**
  - It compares `Authorization: Bearer <RING_WEBHOOK_SECRET>`, a header Ring doesn't send. With the secret set, every real delivery would get a 401; without it, nothing is verified.
  - The Zod enum allows only `motion_detected`, `device_added`, `device_removed` and an undocumented `person_detected`. Real `button_press`, `device_online/offline`, `app_integration_*` and `subscription_*` deliveries fail validation and fall through to a generic normalizer.
- **Severity:** High. Developers copy starters into production.
- **Workaround:** Wrote our own verification. Also fixed the starter and submitted the fix as our Open Source contribution: tested against forged, signed and duplicate deliveries, and type-checked.
- **Suggestion:** Merge the fix. Add a CI test that signs a fixture body with a known key and posts it to the route.

### 3. No way to exercise a webhook handler end to end without hardware **[confirm]**
- **Task:** Test the webhook handler against real Ring deliveries.
- **Steps:** Looked for a webhook sender in the Developer Playground (token, API explorer, live-view simulation) and in the Developer Portal app settings.
- **Expected:** "Send test event" to the registered Webhook URL, signed with the app's HMAC key.
- **Actual:** We found none. The Playground simulates live view sessions, and the release notes don't mention webhook delivery.
- **Severity:** High for anyone without a Ring device.
- **Workaround:** Built a scenario player that builds documented v1.1 payloads, signs them with the HMAC key, and POSTs them to our real `/webhooks/ring` route. There's also a CLI: `python -m turnover.simulator send …`.
- **Suggestion:** Add a "Send test webhook" button per event type. Echo back the HTTP status and response time, since Ring expects a 200 within 5 seconds.

### 4. `sub_type: motion` is ambiguous, and nothing says which case you're in
- **Task:** Decide whether a motion event at a vacant home is a person.
- **Steps:** Read the Oct 5 2026 release note about motion `sub_type`, then looked for a Smart Alerts setting in `configurations`.
- **Expected:** A way to tell "Smart Alerts off, unclassified" apart from "classified as generic motion".
- **Actual:** The release note says no API reports whether Smart Alerts is enabled.
- **Severity:** Medium. It's either false alarms or missed intrusions at a vacant home.
- **Workaround:** Treat `motion` as "unclassified" and send it through the CV funnel, but only where one event changes the outcome (vacant or turnover). Without vision it's treated as a person at a vacant home and muted during a stay.
- **Suggestion:** Add `configurations.smart_alerts.enabled`, or a `classified: true|false` flag on motion webhooks.

### 5. AWS App Runner, the usual "container to HTTPS" answer, is closed to new customers
- **Task:** Host the webhook endpoint on AWS with HTTPS.
- **Steps:** Planned App Runner, the default in most tutorials and in AI assistants' suggestions, then checked its status.
- **Expected:** Create a service from an ECR image.
- **Actual:** App Runner moved to maintenance mode and stopped accepting new customers on April 30, 2026. ECS Express Mode is the recommended replacement. Its getting-started page shows `create-express-gateway-service` but not update or delete.
- **Severity:** Medium
- **Workaround:** Read the ECS API model in botocore for `UpdateExpressGatewayService` and `DescribeExpressGatewayService` (`activeConfigurations[0].ingressPaths[0].endpoint` gives the URL). Then wrote an idempotent deploy script.
- **Suggestion:** Add an Express Mode banner to App Runner tutorials. Add update and delete examples to the Express Mode getting-started page.

### 6. Location-scoped subscriptions can't be resolved to a place
- **Task:** Map a per-property subscription to the home it pays for.
- **Steps:** Read the Oct 12 2026 Subscriptions Query change, then looked for a location endpoint.
- **Expected:** `GET /v1/locations/{id}` with a name.
- **Actual:** The release note says no endpoint resolves a location ID on its own; location data comes only through a device, and `/v1/devices/{id}/location` is coarse (country and state).
- **Severity:** Low today, Medium for per-property pricing.
- **Workaround:** Store the location ID on the property after the host maps devices.
- **Suggestion:** Add `GET /v1/locations/{id}` with a display name.

### 7. Playground token lifetime during demo recording **[confirm]**
- **Task:** Record the demo video against the real Ring API.
- **Steps:** Generated a token, connected, recorded several takes.
- **Expected:** One token per session.
- **Actual:** Tokens last about 30 minutes, so later takes failed with 401 until a new token was pasted. *(Add the exact error you saw.)*
- **Severity:** Low
- **Workaround:** Paste a fresh token before each take. Turnover keeps it in memory only.
- **Suggestion:** Offer a longer-lived sandbox token (for example 8 hours, read-only) for development.

### 8. Snapshot requests from the Playground **[confirm]**
- **Task:** Fetch a snapshot for an alert.
- **Steps:** `POST /v1/devices/{id}/media/image/download` with `at_timestamp`, then with `latest_in_range`.
- **Expected:** A JPEG, or a 303 redirect to one.
- **Actual:** *(Record what you saw: the status code and error detail. A community SDK reports a 403 "time range not within authorized boundaries" for range requests.)*
- **Severity:** *(set after testing)*
- **Workaround:** Turnover tries `at_timestamp`, then a ±90-second `latest_in_range`, then shows the alert without an image.
- **Suggestion:** Document the time-range limits for snapshots in the Playground and in production.
