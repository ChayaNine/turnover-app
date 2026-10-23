# Devpost submission: paste-ready

Fill in the `<angle brackets>` before submitting.

---

## Project name
Turnover

## Tagline (under 200 characters)
Your Ring doorbell, read against your booking calendar: alerts when the cleaner doesn't show, silence when your guests walk to the beach.

## Tracks and challenges
- **Primary track:** Ring
- **Mini challenges:** AWS Builder, Open Source

## Built with
python, fastapi, ring-partner-api, amazon-bedrock, amazon-nova, amazon-sns, amazon-ecs, aws-fargate, amazon-ecr, aws-secrets-manager, mcp, sqlite, javascript, html, css

---

## About the project

### Inspiration

A short-term-rental host's most expensive event is one that never happens: the cleaner doesn't show up between a checkout and a same-day check-in. Ring can't alert on it, because nothing happened at the door. Meanwhile the same host's phone buzzes every time the guests walk to the beach.

Hosts already own Ring cameras, and they already have a machine-readable calendar: every Airbnb, VRBO and Booking.com listing exports one. Nobody joins the two. Airbnb alone has more than 5.5 million hosts and 9 million active listings. In Hospitable's 2026 industry survey, more than a third of hosts said they lost bookings or got bad reviews in 2025 because of staffing or contractor problems, and nearly 40% struggle to find dependable cleaners.

### What it does

Turnover is a Ring Appstore app that reads every Ring event against the home's booking calendar. At any moment a home is **vacant**, **guests staying**, or **in its turnover window**, and the same motion means something different in each.

- **It alerts on what didn't happen.** Turnover expects the cleaner by checkout + 60 minutes and a verified turnover 30 minutes before the next check-in. If either deadline passes, the host gets: "No cleaner yet. Checkout was 11:00 AM and nobody has arrived. Guests check in at 4:00 PM." with a **Text Dev** button for the backup cleaner. When someone arrives, the alert resolves itself.
- **It mutes what's expected.** Guest activity during a stay never reaches the phone. In the demo day, 22 of 28 Ring events are muted, and the dashboard shows the reason for each one.
- **It confirms the business events.** Cleaner arrived. "Ready for the 4:00 PM check-in: on site 12:52–2:41 PM (1 h 49 min)," with an *Approve cleaner payment* button as proof of service. Guests checked in.
- **It escalates by context.** A person at a vacant home is urgent and comes with a snapshot. A package at a vacant home goes on the cleaner's list. A camera that goes offline while the home is empty is flagged as a blind spot and resolves itself when it reconnects.
- **It flags possible gatherings carefully.** Several late arrivals during quiet hours trigger one vision check. Amazon Bedrock counts people in the snapshot, and the count is compared with the booking's guest limit. The host reviews the snapshot before acting. There is no face recognition and nobody is identified.
- **It answers by voice.** Turnover is also an MCP server, so an assistant like Alexa+ can answer "Is the cabin ready for tonight?" or "Text Dev to cover the cabin."

### How we built it

**Ring Partner API.**
- *Webhooks:* `X-Signature` is verified over the raw bytes, `meta.request_id` is de-duplicated, and the handler acknowledges within 5 seconds while a queue does the processing.
- *Device discovery:* uses `include=status,capabilities,location,configurations` and reads `e2e_encryption` (TAKE/E2EE devices are never sent to CV).
- *Event History:* reconciled every 10 minutes, because webhooks can be missed.
- *Image snapshots:* fetched at the event timestamp, following the 303 redirect without the bearer token.
- *Subscriptions Query:* reads both the `devices` and the new `locations` relationships, since per-property pricing maps onto location-scoped subscriptions.
- *One-way account linking:* unclaimed tokens, mandatory sign-in, the HMAC nonce in URL-safe base64, then POST → `awaiting` → host setup → PATCH `completed`.
- *Token lifecycle:* proactive refresh, 429 backoff, and re-linking on 401.

**Expectation engine.**
- A state machine built from the calendar, with an injected clock.
- Absence rules are modelled as expectations with deadlines.
- Every event gets a recorded disposition (muted, tracked or alerted) and a reason.

**CV funnel, following Ring's Computer Vision Guidelines and the guardrails section published Oct 19.**
- Tier 0 is Ring's own `sub_type`. Tier 1 is the booking context. Tier 2 is a snapshot, fetched only when it would change the decision. Tier 3 is Amazon Bedrock (Converse API with image input, Amazon Nova Lite).
- The model's output is checked before any host sees it:
  - schema validation and range limits;
  - a block list that strips descriptive words about people;
  - a confidence floor;
  - a context check (if Ring said "human" but the model saw nobody, it falls back to "review the snapshot");
  - an optional Bedrock Guardrail.
- The same funnel handles cameras with Smart Alerts off, which report everything as `motion`. In the demo, wind at a vacant studio is checked and muted instead of paging the host.

**AWS.**
- Deployed on Amazon ECS Express Mode (Fargate behind HTTPS), since App Runner closed to new customers in April 2026.
- Ring secrets live in AWS Secrets Manager. Alerts go out through Amazon SNS by SMS or email. The image is in Amazon ECR.
- Least-privilege IAM: the task can call Bedrock and publish to one topic, nothing else.
- Bedrock also writes the end-of-day brief from facts computed in code. If the model introduces any number that isn't in those facts, the template version is used instead.

**Demo without hardware.** As far as we could find, the Developer Playground has no way to deliver a webhook to your endpoint, so Turnover plays a full day across four homes as HMAC-signed v1.1 webhooks through the real endpoint, on a virtual clock. The Playground token still connects the real Ring API for devices and snapshots.

### Challenges we ran into

- **Proving a negative.** A doorbell sees arrivals and departures, not the time spent inside. We model a turnover visit as first-to-last activity at the door inside the turnover window, and verify it at a fixed ready-check time instead of guessing when the cleaner left.
- **Reading Ring's docs.** The Partner API reference is one page of about 120,000 characters, and AI tools and fetchers truncate it before the Notifications section. Details are in the friction log.
- **Making signature verification right.** Amazon's own `ring-api-helloworld` starter checks a Bearer header that Ring doesn't send, and its schema rejects most documented event types. We fixed it upstream (see Open Source below).

### Accomplishments we're proud of

- The 12:00 PM "No cleaner yet" alert. It's the alert hosts most need, and no camera can send it on its own.
- Reasoning you can audit: every Ring event shows what Ring said, what the home was doing, and why Turnover muted, tracked or escalated it.
- Following Ring guidance published in the last six weeks: `sub_type` as an open vocabulary, the encryption flag as a JSON boolean, location-scoped subscriptions, and CV output guardrails.

### What we learned

The value of a camera event is almost entirely in its context. The same 2 PM motion is the cleaner on a turnover day, an intruder at a vacant home, and noise during a stay. Ring partners need booking-style context to build anything better than "motion detected".

### What's next for Turnover

- Ring's early-access contact sensors, for exact visit durations.
- Channel-manager APIs (Guesty, Hostaway) for guest counts and cleaner schedules.
- A DynamoDB store, and S3 for snapshots.
- Per-property pricing on the Ring Appstore through location-scoped subscriptions.
- Certification against Ring's UX Design Guide checklist.

---

## Did the project exist before the hackathon?
No. All of Turnover was built during the submission window, Oct 20–23, 2026. The Open Source contribution is a new patch to Amazon's `ring-api-helloworld`, also written during the window. *(If you used AI coding tools, say so here. The hackathon encourages them.)*

## Repository
`https://github.com/<your-username>/turnover`. Public, MIT license.

## Demo video
`<YouTube or Vimeo URL, public, English, under 3:00>`

## Try it
Live demo: `<https://turnover….ecs.us-east-1.on.aws/>` (user `host`, password `<DASHBOARD_PASSWORD>`). Press **Play the day**.

---

## AWS Builder mini challenge: documented integrations
- **Amazon Bedrock, Converse API with an image block (Amazon Nova Lite):** tier 3 of the CV funnel. It counts people for the gathering check and classifies motion from cameras with Smart Alerts off. Code: `turnover/vision.py` (`BedrockVision`). The output is validated by `guard()` before any host sees it. Bedrock Guardrails is optional via `BEDROCK_GUARDRAIL_ID`.
- **Amazon Bedrock, Converse API (text):** writes the end-of-day host brief from facts computed in code. The text is rejected if it contains numbers that aren't in the facts. Code: `turnover/brief.py`.
- **Amazon SNS:** pushes alerts to the host by SMS or email, and sends the "text the backup cleaner" message. Code: `turnover/notify.py`.
- **Amazon ECS Express Mode on Fargate, Amazon ECR, AWS Secrets Manager, IAM, CloudWatch Logs:** hosting, image, and Ring secrets (client secret and HMAC key are never in env files or git), with a least-privilege task role. Code: `scripts/deploy_aws.sh`, `Dockerfile`.

## Open Source mini challenge
- **Contribution URL:** `<https://github.com/AmazonAppDev/ring-api-helloworld/pull/NN>`, or your fork's branch `fix/webhook-hmac-signature`
- **Repo URL:** `https://github.com/<your-username>/turnover`
- **GitHub username:** `<your-username>`
- **What, how, why:** Amazon's official Ring starter authenticated webhooks with a Bearer header that Ring never sends. Its Zod schema also rejected `button_press`, `device_online/offline`, `app_integration_*` and `subscription_*`. The PR:
  - verifies Ring's `X-Signature` (hex HMAC-SHA256 over the raw body) with a constant-time compare;
  - accepts all 10 documented event types, with `sub_type` as an open vocabulary;
  - models `component_ids` and `account_id`;
  - makes `/api/webhook/test` sign a v1.1 payload and send it through the real handler, so verification is exercised locally;
  - documents an `openssl` signing recipe.

  It's type-checked and was tested against forged, signed and duplicate deliveries. It matters because developers copy this starter into production backends.

## Product feedback, feature requests, friction log
Paste from `docs/FEEDBACK.md` and `docs/FRICTION_LOG.md`.
