# Runbook: from now to submitted

**Deadline:** Oct 23, 2026, 12:00 PM PDT, which is **Sat Oct 24, 3:00 AM Taipei**. Aim to submit by **Fri 8:00 PM Taipei**: Devpost slows down near deadlines, and judges see the same thing either way.

The code is built. What's left needs *you*: your accounts, your AWS, your voice, your photos.

---

## Tuesday night (2–3 hours)

### 1. Put the code on GitHub (15 min)
1. Create a **public** repo named `turnover` on GitHub. Leave it empty: no README, no license.
2. Push the code:
   ```bash
   unzip turnover.zip && cd turnover
   git remote add origin https://github.com/<you>/turnover.git
   git push -u origin main
   ```
   *Or tell Claude the repo name and it can push for you.*
3. Check that GitHub shows the README with the screenshot and the diagram.

### 2. Run it on your laptop (10 min)
You need Python 3.11 or newer.
```bash
cd turnover
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn turnover.app:create_app --factory --port 8000
```
Open http://localhost:8000 and press **Play the day**. It pauses four times; press a button on each card or **Resume**.

### 3. Ring developer access (20 min)
1. Create a free account and open the [Developer Playground](https://developer.amazon.com/ring/console/playground).
2. Click **Generate Token**.
3. In Turnover, click **Ring account** in the sidebar, paste the token, and press **Connect**. You should see your Playground devices.
4. While you're there, check the **[confirm]** items in `docs/FRICTION_LOG.md` and `docs/FEEDBACK.md`:
   - Is there any way to send a webhook from the Playground?
   - Which simulated devices exist?
   - Does a snapshot request work? After connecting, open `http://localhost:8000/api/ring/devices/<device-id>/snapshot`, which asks Ring for the latest frame from the last 10 minutes.

   Write down exact error messages. Real detail is what earns the 10% friction-log bonus.

### 4. Devpost (10 min)
1. Join the hackathon and click **Start a submission**.
2. Paste the name and tagline from `docs/SUBMISSION.md` and save the draft. That locks in your entry.

---

## Wednesday (3–4 hours)

### 5. Amazon Bedrock (20 min)
1. In the AWS console, switch to **us-east-1**, open **Bedrock → Model access**, and enable **Amazon Nova Lite**.
2. Test locally:
   ```bash
   export AWS_REGION=us-east-1 BEDROCK_ENABLED=1    # uses your local AWS credentials
   uvicorn turnover.app:create_app --factory --port 8000
   ```
3. Play the day. The vision lines should now say **Checked by Amazon Bedrock, us.amazon.nova-lite-v1:0**.
4. Press **Write today's brief**. It should say "Written by bedrock:…".

### 6. Your own photos (30 min, this makes the demo honest and much stronger)
The demo frames in `demo/images/` are labelled illustrations. Replace them with photos you took, using the same file names:

| File | Photo | Bedrock should see |
|---|---|---|
| `harbor-gathering.jpg` | 6 or more friends at a front door, evening, shot from doorbell height | many people |
| `cedar-night.jpg` | one person on a path at night | 1 person |
| `cedar-wind.jpg` | an empty porch or path in daylight | nobody |

Then update the counts in the matching `.json` files, so the offline fallback matches your photos. Run the day again with Bedrock on and check the counts. Don't use stock photos of people.

### 7. Deploy to AWS (45 min; the first deploy takes the longest)
You need the AWS CLI v2 (recent) and Docker Desktop running.
```bash
export AWS_REGION=us-east-1
export ALERT_EMAIL=<your email>
export DASHBOARD_PASSWORD='<pick a password for judges>'
./scripts/deploy_aws.sh
```
- Confirm the SNS email that arrives.
- Open the printed URL (user `host`, the password you picked) and play the day. Alert emails should arrive.
- Send one signed event to the deployed URL. The deployed app uses a generated key unless you set `RING_HMAC_KEY`, so export the same key before deploying:
  ```bash
  export RING_HMAC_KEY=$(openssl rand -hex 32)   # set before deploy_aws.sh, and reuse it here
  python -m turnover.simulator send --url https://<url>/webhooks/ring --key "$RING_HMAC_KEY" \
     --device ava1.ring.device.demo-cedar-porch --type motion_detected --sub-type human
  ```
- If `create-express-gateway-service` isn't recognised, update the AWS CLI.

### 8. Open Source PR (15 min)
Follow `docs/open-source/PULL_REQUEST.md`: fork, `git am` the patch, push, and open the PR. Copy the PR URL.

### 9. Optional: a real Ring app (30 min, only if you own a Ring device with a Protect plan)
Create a **Private app** in the Ring Developer Portal and register the four URLs from step 7. Install it from the Ring app and run the linking flow. A real doorbell press in the video is gold. If you don't have a device, skip this: the track allows simulators.

---

## Thursday (3 hours)

### 10. Record the video
1. Follow `docs/VIDEO_SCRIPT.md` exactly; it's timed to 2:50.
2. Record the screen with QuickTime or OBS, and the voice-over separately if that's easier.
3. Upload to **YouTube as Public**, with an English title like "Turnover: booking-aware Ring alerts for vacation-rental hosts".
4. Watch it once on your phone. Is the 12:00 card readable?

### 11. Polish the text
- Fill every `<…>` in `docs/SUBMISSION.md`.
- Resolve every **[confirm]** in the feedback and friction log.
- Add 3–4 screenshots to the Devpost gallery: the dashboard at noon, the end of the day, the Harbor alert with snapshot, and the "Why" table.

---

## Friday (1 hour): submit by 8:00 PM Taipei
Paste these into Devpost, from `docs/SUBMISSION.md`, `docs/FEEDBACK.md` and `docs/FRICTION_LOG.md`:
- [ ] Description (the "About the project" sections)
- [ ] GitHub repo URL (public, with LICENSE)
- [ ] YouTube URL
- [ ] Track: **Ring**. Mini challenges: **AWS Builder** and **Open Source**
- [ ] AWS Builder: the integrations list
- [ ] Open Source: contribution URL, repo URL, GitHub username, and what/how/why
- [ ] Product feedback for every tool
- [ ] Feature requests
- [ ] Friction log
- [ ] "Existed before the hackathon": No, built Oct 20–23
- [ ] Live demo URL and judge password in the description
- [ ] Press **Submit**, not just Save. Then open the public project page and check it.

Leave the deployed service running until judging ends.
