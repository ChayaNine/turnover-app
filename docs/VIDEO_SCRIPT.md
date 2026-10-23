# Demo video script (target 2:50, hard limit 3:00)

Judges may stop watching at 3:00, so the best moment comes first. Record at 1440×900 or larger, browser zoom 100%, with no notifications on screen. Use no music, or a track from the YouTube Audio Library, and no third-party logos or footage. Speak slowly; this script is about 380 words.

**Before recording:**
- Run with `BEDROCK_ENABLED=1`, so vision results say "Amazon Bedrock".
- Put your own photos in `demo/images/` (see the runbook).
- Paste a fresh Playground token.
- Set speed to **Normal** and tick **Pause on alerts**.

---

### 0:00–0:15 · The hook
**Screen:** The Today page, already paused at 12:00 PM. The greeting reads "Lakeview Cabin needs you. The other three homes are on track." The "No cleaner yet" card sits under it with the **Text Dev** button, and Lakeview's row shows the red marker inside its amber turnover window.
**Say:** "It's noon. Checkout was at eleven, new guests arrive at four, and the cleaner hasn't shown up. Your Ring doorbell can't tell you that, because nothing happened at the door. Turnover can."

### 0:15–0:35 · The idea
**Screen:** Point at the "Your homes today" list: the blue stay, the amber turnover window, and the small grey marks for events Turnover kept quiet.
**Say:** "Turnover is a Ring Appstore app for short-term-rental hosts. It reads every Ring event against each home's booking calendar, the iCal feed Airbnb and VRBO already publish. The same motion means guests during a stay, the cleaner during a turnover, and a stranger at an empty home."

### 0:35–1:40 · The day, live
**Screen:** Press **Start over**, then **Play the day**. Narrate as it plays and pauses.
- *Morning marks stay grey:* "Guests heading out. Kept quiet."
- *Cedar, 9:40:* "This old camera has Smart Alerts off, so Ring just says 'motion'. Turnover pulls one snapshot, Bedrock sees nobody, and it's muted. That's a false alarm that never reached the host."
- *Juniper, 11:34:* "A package at a vacant home goes on the cleaner's list."
- *12:00 pause:* "Here's the no-show." Click **Text Dev**, and the message appears. "One tap and the backup is on the way."
- *12:52:* "Dev arrives, and the alert resolves itself."
- *3:02 pause:* "Camera offline at an empty home. That's a blind spot, so it's urgent." Resume. "Back online after 22 minutes."
- *3:30:* "Thirty minutes before check-in: verified at the door, on site an hour forty-nine. Approve the cleaner's payment."
- *4:26:* "Guests arrived. From here, their comings and goings are muted."
- *9:42 pause, Cedar:* "A person at a vacant home, confirmed by the vision check, with a snapshot."
- *11:15 pause, Harbor:* "Three late arrivals in thirty minutes during quiet hours. One vision check counts about nine people against a four-guest booking. The host reviews the snapshot first. Nobody is identified."

**Screen:** Scroll up to the greeting.
**Say:** "Twenty-eight things happened at these doors today. Four needed the host."

### 1:40–2:10 · Real Ring API, done right
**Screen:**
1. Click **Lakeview Cabin**: the day told step by step, with routine events folded away and the cleaner's visit on the ribbon. Then open **Activity** to show every Ring event and what Turnover did with it.
2. Open **Ring account** in the sidebar, paste the Playground token, and show the real devices with their encryption status. Assign one to a home.
3. Cut to the terminal: `python -m turnover.simulator send --url https://<deployed>/webhooks/ring --key … --type motion_detected --sub-type human`. Show `200 accepted`, then show it appear on the deployed dashboard.

**Say:** "Every decision is explained. Under the hood: webhooks are verified by Ring's HMAC signature over the raw bytes and de-duplicated on request ID. Event History backfills anything we missed. Encrypted devices never go to vision. And account linking follows Ring's one-way flow, with the nonce, sign-in, then POST and PATCH."

### 2:10–2:30 · AWS
**Screen:** The architecture diagram from the README. Then your phone receiving the SNS email or SMS for an alert.
**Say:** "It runs on Amazon ECS Express Mode, with Ring secrets in Secrets Manager. Amazon Bedrock does the vision tier and writes the end-of-day brief, and its output is checked before a host sees it. Alerts reach the host's phone through Amazon SNS."

### 2:30–2:45 · Voice
**Screen:** MCP Inspector connected to `/mcp`. Call `home_status` with `the cabin` and highlight the `say` field.
**Say:** "Turnover is also an MCP server, so Alexa+ can answer 'Is the cabin ready for tonight?' from the same engine."

### 2:45–2:55 · Close
**Screen:** The dashboard end state.
**Say:** "It's priced per property on the Ring Appstore, using its new location-based subscriptions. We also fixed webhook verification in Ring's own starter app. Turnover: Ring tells you what happened. Turnover tells you what it means."

---

## Shot checklist
- [ ] The no-show card is readable at 1080p, so zoom the browser to 110% for that shot.
- [ ] Bedrock attribution is visible on the vision line ("Checked by Amazon Bedrock").
- [ ] The real Ring device list is shown (it proves real API use, which the Ring track requires).
- [ ] A signed webhook reaches the deployed URL.
- [ ] The SNS message arrives on your phone.
- [ ] The MCP Inspector call is shown.
- [ ] Total runtime is under 3:00, and the video is public on YouTube.
