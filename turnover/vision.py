"""Vision tier of the CV funnel, with guardrails.

Ring's Computer Vision Guidelines recommend a tiered funnel, and warn that doorbell
imagery (wide-angle lens, high mount, night, backlight, low bandwidth) degrades accuracy
and that vision-language models add hallucination on top. Turnover follows that:

  Tier 0  Ring's own classification (``sub_type``)            free, every event
  Tier 1  booking context gate (vacant / turnover / quiet hrs)  free, every event
  Tier 2  snapshot from the Ring API at the event timestamp     only if tier 1 says it matters
  Tier 3  VLM on Amazon Bedrock, structured output              only if tier 2 produced an image

and guards the VLM output before a host sees it (guidelines section "Guardrails: protecting
your output before it reaches the user", Oct 19 2026):

  * schema validation and range clamping -- the model returns numbers, not prose
  * a keyword block list on the one free-text field, plus an optional Bedrock Guardrail
  * a defined fallback when any check fires: "review the snapshot", never a guess
  * a context check against Ring's own signal (Ring said human, model saw nobody -> review)
  * no identity: the prompt forbids describing people, and nothing descriptive is stored
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

MIN_CONFIDENCE = 0.6
MAX_PEOPLE = 40
SUMMARY_MAX = 90

# Attributes a host must never be shown about a person at their door.
BLOCKLIST = re.compile(
    r"\b(race|racial|ethnic\w*|skin|black|white|asian|latin\w*|hispanic|gender|male|female|man|woman|men|women|"
    r"boy|girl|age|old|young|elderly|teen\w*|attractive|ugly|fat|thin|disab\w*|religio\w*|hijab|turban|"
    r"suspicious|criminal|thief|burglar|threat\w*|dangerous|gang\w*|drunk|high)\b",
    re.IGNORECASE,
)

PROMPT = """You are the occupancy check for a vacation-rental host. The image is a snapshot from the property's front-door camera.

Return ONLY a JSON object, no other text:
{"people_count": <integer number of people visible>,
 "vehicle_present": <true|false>,
 "package_present": <true|false>,
 "confidence": <0.0-1.0, how sure you are of people_count>,
 "scene": "<at most 12 neutral words about the scene, e.g. 'group standing at front door at night'>"}

Rules:
- Count people only. Never describe anyone's appearance, clothing, age, gender, ethnicity or identity.
- Do not speculate about intent.
- If the image is dark, blurry, obstructed or ambiguous, lower confidence instead of guessing."""


@dataclass
class VisionResult:
    ok: bool
    source: str
    people_count: int | None = None
    vehicle_present: bool | None = None
    package_present: bool | None = None
    confidence: float = 0.0
    summary: str | None = None
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Vision(Protocol):
    name: str

    async def analyze(self, image: bytes, content_type: str, *, ring_sub_type: str | None = None) -> VisionResult: ...


def _extract_json(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        doc = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) else None


def guard(raw: dict[str, Any] | None, source: str, *, ring_sub_type: str | None = None) -> VisionResult:
    """Turn raw model output into something safe to show, or a defined fallback."""
    if raw is None:
        return VisionResult(False, source, fallback_reason="model output was not valid JSON")
    try:
        count = int(raw.get("people_count"))
        conf = float(raw.get("confidence", 0))
    except (TypeError, ValueError):
        return VisionResult(False, source, fallback_reason="model output failed schema validation")
    if not 0 <= count <= MAX_PEOPLE or not 0.0 <= conf <= 1.0:
        return VisionResult(False, source, fallback_reason="model output out of range")
    scene = str(raw.get("scene") or "").strip()[:SUMMARY_MAX] or None
    if scene and BLOCKLIST.search(scene):
        scene = None  # drop the free text, keep the numbers
    result = VisionResult(
        ok=True,
        source=source,
        people_count=count,
        vehicle_present=bool(raw.get("vehicle_present")),
        package_present=bool(raw.get("package_present")),
        confidence=round(conf, 2),
        summary=scene,
    )
    if conf < MIN_CONFIDENCE:
        result.ok = False
        result.fallback_reason = f"low confidence ({conf:.2f})"
    elif ring_sub_type == "human" and count == 0:
        # Context signal: Ring's detector and the VLM disagree. Don't pick a winner.
        result.ok = False
        result.fallback_reason = "Ring detected a person but the model saw none"
    return result


class BedrockVision:
    """Amazon Bedrock Converse API with an image block. Works with any Bedrock model that
    accepts images (Amazon Nova Lite/Pro, Anthropic Claude). Optional Bedrock Guardrail."""

    def __init__(self, model_id: str, region: str, guardrail_id: str | None = None, guardrail_version: str = "DRAFT") -> None:
        import boto3

        self.model_id = model_id
        self.name = f"bedrock:{model_id}"
        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._guardrail = (guardrail_id, guardrail_version) if guardrail_id else None

    def _call(self, image: bytes, fmt: str) -> str:
        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": {"format": fmt, "source": {"bytes": image}}},
                        {"text": PROMPT},
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": 200, "temperature": 0.0},
        }
        if self._guardrail:
            kwargs["guardrailConfig"] = {"guardrailIdentifier": self._guardrail[0], "guardrailVersion": self._guardrail[1]}
        resp = self._client.converse(**kwargs)
        if resp.get("stopReason") in ("guardrail_intervened", "content_filtered"):
            return ""  # -> "not valid JSON" -> defined fallback: host reviews the snapshot
        parts = resp["output"]["message"]["content"]
        return "".join(p.get("text", "") for p in parts)

    async def analyze(self, image: bytes, content_type: str, *, ring_sub_type: str | None = None) -> VisionResult:
        fmt = "png" if "png" in (content_type or "") else "jpeg"
        try:
            text = await asyncio.wait_for(asyncio.to_thread(self._call, image, fmt), timeout=20)
        except Exception as exc:  # network, throttling, access not granted
            return VisionResult(False, self.name, fallback_reason=f"Bedrock unavailable: {type(exc).__name__}")
        return guard(_extract_json(text), self.name, ring_sub_type=ring_sub_type)


class FixtureVision:
    """Offline stand-in for the demo: reads ``<image>.json`` next to a demo image.
    Results are labelled ``fixture`` in the UI so nobody mistakes them for model output."""

    name = "fixture"

    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        self._by_digest: dict[str, dict[str, Any]] = fixtures or {}

    def register(self, image: bytes, result: dict[str, Any]) -> None:
        import hashlib

        self._by_digest[hashlib.sha256(image).hexdigest()] = result

    async def analyze(self, image: bytes, content_type: str, *, ring_sub_type: str | None = None) -> VisionResult:
        import hashlib

        raw = self._by_digest.get(hashlib.sha256(image).hexdigest())
        if raw is None:
            return VisionResult(False, self.name, fallback_reason="no fixture for this image")
        return guard(raw, self.name, ring_sub_type=ring_sub_type)


class NoVision:
    name = "none"

    async def analyze(self, image: bytes, content_type: str, *, ring_sub_type: str | None = None) -> VisionResult:
        return VisionResult(False, self.name, fallback_reason="vision not configured")


def load_fixture(image_path: Path) -> dict[str, Any] | None:
    side = image_path.with_suffix(".json")
    if side.exists():
        return json.loads(side.read_text())
    return None
