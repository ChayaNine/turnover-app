# Product feedback

For each tool: what we used it for, what worked, what needs work, how onboarding felt, and whether we'd build with it again.

> Items marked **[confirm]** depend on your own Playground session. Check them while you test, then edit or delete them before submitting. Judges value feedback that is specific and true.

---

## Ring Partner API (Amazon Vision)

**Used for:**
- device discovery with `include=status,capabilities,location,configurations`;
- `GET /v1/users/me`;
- configurations (`e2e_encryption`);
- Event History, to reconcile missed webhooks;
- image snapshots (`at_timestamp`, with a `latest_in_range` fallback);
- the Subscriptions Query API;
- App Integrations POST and PATCH;
- webhooks (motion with `sub_type`, button press, device online/offline, device added, integration and subscription events);
- OAuth token exchange and refresh.

**What worked well**
- JSON:API compound documents. One `GET /v1/devices?include=…` call gives everything a setup screen needs.
- The one-way linking design is explained unusually well. The "Why it's designed the way it is" section made the POST-then-PATCH split obvious, and we used it as intended: PATCH `completed` only after the host maps cameras to homes.
- The release notes are excellent. They are dated, specific, and say exactly what breaks naive code. Examples: `e2e_encryption.enabled` is a boolean while its neighbours are `"on"`/`"off"` strings; read *both* subscription relationships; `sub_type` is open-ended. Each of these is now a line of code and a test in Turnover.
- The Computer Vision Guidelines, especially the Oct 19 guardrails section, read like advice from people who have shipped CV on doorbell imagery. Our funnel follows it tier by tier.

**What needs work**
- There is no machine-readable spec. We couldn't find an OpenAPI or JSON Schema document, so every partner hand-writes models for the same endpoints. At least two community Python SDKs for the same API (`ringpartner`, `ring-sandbox`) appeared on PyPI this year.
- The API reference is a single page of about 120,000 characters. AI coding assistants and fetchers truncate it before the Notifications section, which is exactly where the webhook signature format lives. See the friction log.
- We found no way to send a test webhook. A "Send test event" button in the Developer Portal, signed with the app's real HMAC key and delivered to the registered Webhook URL, would remove most of the setup uncertainty. **[confirm]** that the Playground can't deliver webhooks to your endpoint.
- Some state can't be read:
  - whether Smart Alerts is enabled, so `sub_type: motion` is ambiguous;
  - a battery webhook (status must be polled);
  - a name for a location ID.

  Each forces a workaround (see feature requests).

**Onboarding:** The Playground's one-click token made the first API call take minutes. The jump from Playground to a registered app (Token Exchange URL, Account Link URL, nonce matching) is steep, but the docs carry you through it.

**Would we build with it again?** Yes. The webhook and history model is a solid base for context-aware apps, and the Appstore's per-location subscriptions are a natural fit for per-property pricing.

## Ring Developer Playground
**Used for:** an access token for real device discovery, snapshots and history in the demo.
**Worked:** a token in one click, the API explorer with curl, and live-view simulation.
**Needs work:**
- Tokens last about 30 minutes, so every demo recording needs a fresh paste.
- There's no webhook delivery. **[confirm]**
- No simulated device has Smart Alerts off or encryption on, so those code paths can't be exercised against Ring itself. **[confirm]** the device list you see.

**Again?** Yes.

## Ring docs: UX Design Guide, Marketing Guidelines, Developer FAQ
**Used for:** the account-link page (sign-in first, masked email), the "manage connection" language, naming (Turnover doesn't use Ring branding), and the encryption FAQ.
**Worked:** The FAQ answer on TAKE vs E2EE told us exactly what a partner sees in each mode.
**Needs work:** A downloadable reference implementation of the Account Link page would help. Today every partner rebuilds the nonce flow from prose.

## AmazonAppDev/ring-api-helloworld
**Used for:** the starting point for API shapes and the WHEP flow.
**Worked:** the scripts print equivalent curl commands, which are great for learning.
**Needs work:**
- The webhook handler checks `Authorization: Bearer` instead of Ring's `X-Signature` HMAC.
- Its schema rejects `button_press`, `device_online/offline`, and the integration and subscription events.

We submitted a fix (see the Open Source section). Because people copy this into production, it's the highest-impact fix in this feedback.
**Again?** Yes, after the fix.

## Amazon Bedrock (Converse API, Amazon Nova Lite, optional Guardrails)
**Used for:**
- people counts and scene classification on doorbell snapshots (tier 3 of the CV funnel);
- the end-of-day host brief.

**Worked:** The Converse API's image block meant one request shape works for any vision model, so switching between Nova and Claude is a single environment variable. `stopReason` values such as `guardrail_intervened` and `content_filtered` make the fallback path explicit.
**Needs work:**
- Model access is enabled per model and per Region in the console, and cross-Region inference profile IDs (`us.` prefix) versus base model IDs confuse first-time users. A clearer error than `AccessDeniedException` would help: name the console page that fixes it.
- We parse the model's JSON tolerantly and validate it ourselves. A worked example of schema-constrained output for vision models in the Converse docs would help.

**Onboarding:** fast once model access was granted.
**Again?** Yes.

## Amazon ECS Express Mode
**Used for:** hosting (Fargate, HTTPS load balancer, health checks, logs) from one CLI call, with secrets from Secrets Manager.
**Worked:**
- One command gives a public HTTPS URL, which is exactly what a Ring webhook endpoint needs.
- `secrets` takes Secrets Manager ARNs directly.

**Needs work:**
- The getting-started page shows create but not update or delete. We read the API model to find `update-express-gateway-service`.
- App Runner moved to maintenance on Apr 30, 2026, but most tutorials and AI assistants still recommend it. A redirect banner on App Runner tutorials would save people a dead end.

**Again?** Yes, it's the simplest container-to-HTTPS path on AWS now.

## Amazon SNS, AWS Secrets Manager, Amazon ECR
**Used for:** alert fan-out by SMS and email, storing the Ring client secret and HMAC key, and the container image.
**Worked:** No surprises; these are boring in the best way.
**Needs work:** SNS SMS needs an origination identity in many countries, which isn't obvious from the Publish call. We default to email subscriptions.

---

# Feature requests

| Request | Why it matters | Urgency |
|---|---|---|
| **"Send test webhook" in the Developer Portal**, signed with the app's HMAC key, to the registered URL, for any documented event type | Without hardware there's no end-to-end check of signature verification. Partners ship handlers that have never seen a real signature, and the official starter did too. | Critical |
| **OpenAPI or JSON Schema for the Partner API and webhook payloads** | Generated clients and validators instead of a dozen hand-written SDKs; contract tests in CI | Important |
| **Expose Smart Alerts status** (`configurations.smart_alerts.enabled`) | `sub_type: motion` means either "Ring didn't classify" or "it's just motion". Partners must spend snapshot and model calls to find out. | Important |
| **Optional snapshot reference in `motion_detected` webhooks** (pre-signed, short TTL) | Saves a round trip per alert. The best frame is the one Ring already picked. | Important |
| **`GET /v1/locations/{id}`** with a display name | Per-property apps (rentals, multi-site businesses) want to show "Lakeview Cabin", not a device name. Location-scoped subscriptions make this more pressing. | Important |
| **Battery-low webhook** | Docs say status must be polled. A dead doorbell at a vacant home is a blind spot. | Nice-to-have |
| **Split the API reference into pages, plus an `llms.txt` or Markdown export** | AI-assisted development is how many hackathon builders work, and the webhook section is currently out of reach | Important |
| **Playground devices with Smart Alerts off, TAKE encryption, and multi-camera** | Exercise the edge cases the release notes describe | Nice-to-have |
