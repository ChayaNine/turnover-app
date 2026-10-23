"""End-of-day host brief: one short paragraph per property.

The facts are computed by the engine; the model only phrases them. If Bedrock is off or
its output fails the checks (length, numbers it was not given, block list), the template
version is used instead, so the brief is never wrong, only less elegant.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from zoneinfo import ZoneInfo
from datetime import datetime

from .vision import BLOCKLIST

PROMPT = """Write a 2-sentence end-of-day update for a vacation-rental host about one property.
Use only the facts in the JSON. Do not add numbers, times or names that are not in it.
Plain, calm, specific. No greeting, no emoji, no bullet points.

FACTS:
{facts}"""


def facts(day: dict[str, Any], alerts: list[dict[str, Any]]) -> dict[str, Any]:
    prop = day["property"]
    tz = ZoneInfo(prop["timezone"])

    def t(iso: str) -> str:
        return datetime.fromisoformat(iso).astimezone(tz).strftime("%I:%M %p").lstrip("0")

    return {
        "property": prop["name"],
        "state_now": day["state"],
        "ring_events_today": day["metrics"]["ring_events"],
        "muted_as_expected": day["metrics"]["suppressed"],
        "alerts_sent": day["metrics"]["alerts"],
        "turnover_visits": [
            {"arrived": t(v["first_at"]), "last_seen": t(v["last_at"]), "minutes_on_site": v["minutes"]} for v in day["visits"]
        ],
        "alerts": [
            {"time": t(a["created_at"]), "title": a["title"].split(" · ")[0], "severity": a["severity"], "status": a["status"]}
            for a in sorted(alerts, key=lambda x: x["created_at"])
        ],
    }


def template(f: dict[str, Any]) -> str:
    parts = [f"{f['ring_events_today']} Ring events today; {f['muted_as_expected']} were expected and muted."]
    if f["turnover_visits"]:
        v = f["turnover_visits"][0]
        parts.append(f"Turnover seen {v['arrived']}–{v['last_seen']} ({v['minutes_on_site']} min).")
    flagged = [a for a in f["alerts"] if a["severity"] in ("urgent", "warn")]
    if flagged:
        word = {"resolved": "resolved", "acted": "you acted", "dismissed": "dismissed", "open": "still open"}
        parts.append("Needed you: " + "; ".join(f"{a['title'][0].lower() + a['title'][1:]} at {a['time']} ({word.get(a['status'], a['status'])})" for a in flagged[:3]) + ".")
    else:
        parts.append("Nothing needed your attention.")
    done = [a for a in f["alerts"] if a["severity"] == "good" and a["title"].startswith("Ready")]
    if done:
        parts.append(f"{done[0]['title']} at {done[0]['time']}.")
    return " ".join(parts)


def _checks_pass(text: str, f: dict[str, Any]) -> bool:
    if not text or len(text) > 420 or BLOCKLIST.search(text):
        return False
    allowed = set(re.findall(r"\d+", json.dumps(f)))
    return all(n in allowed for n in re.findall(r"\d+", text))


async def write(day: dict[str, Any], alerts: list[dict[str, Any]], *, bedrock_client=None, model_id: str | None = None) -> dict[str, str]:
    f = facts(day, alerts)
    fallback = template(f)
    if bedrock_client is None or not model_id:
        return {"text": fallback, "source": "template"}

    def call() -> str:
        resp = bedrock_client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": PROMPT.format(facts=json.dumps(f, indent=1))}]}],
            inferenceConfig={"maxTokens": 160, "temperature": 0.2},
        )
        return "".join(p.get("text", "") for p in resp["output"]["message"]["content"]).strip()

    try:
        text = await asyncio.wait_for(asyncio.to_thread(call), timeout=15)
    except Exception:
        return {"text": fallback, "source": "template"}
    if not _checks_pass(text, f):
        return {"text": fallback, "source": "template (model output failed checks)"}
    return {"text": text, "source": f"bedrock:{model_id}"}
