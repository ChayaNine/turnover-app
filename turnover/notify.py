"""Delivery: the in-app live feed (server-sent events) and, when configured, Amazon SNS.

An SNS topic fans out to the host's phone (SMS) and email subscriptions, so hosts get the
alert where they already are. Only alerts are pushed; suppressed events never leave the app.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .models import Alert, Severity

log = logging.getLogger("turnover.notify")

PUSH_SEVERITIES = {Severity.URGENT, Severity.WARN, Severity.GOOD, Severity.INFO}


class Hub:
    """Fan-out to connected dashboards."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._queues.discard(q)

    def publish(self, kind: str, payload: dict[str, Any]) -> None:
        msg = json.dumps({"kind": kind, "data": payload}, default=str)
        for q in list(self._queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


class Notifier:
    def __init__(self, hub: Hub, *, sns_topic_arn: str | None = None, region: str = "us-east-1") -> None:
        self.hub = hub
        self._topic = sns_topic_arn
        self._sns = None
        if sns_topic_arn:
            import boto3

            self._sns = boto3.client("sns", region_name=region)
        self.sent: list[dict[str, Any]] = []

    @property
    def channels(self) -> list[str]:
        return ["dashboard"] + (["sns"] if self._sns else [])

    async def alert(self, alert: Alert, property_name: str, *, update: bool = False) -> None:
        self.hub.publish("alert_update" if update else "alert", alert.to_dict())
        if update or alert.severity not in PUSH_SEVERITIES or not self._sns:
            return
        text = f"{property_name}: {alert.title}\n{alert.body}"
        try:
            await asyncio.to_thread(
                self._sns.publish,
                TopicArn=self._topic,
                Subject=f"Turnover · {property_name}"[:99],
                Message=text[:1500],
                MessageAttributes={"severity": {"DataType": "String", "StringValue": alert.severity.value}},
            )
            self.sent.append({"alert_id": alert.id, "channel": "sns"})
        except Exception as exc:  # never let delivery failure break event processing
            log.warning("SNS publish failed: %s", exc)

    async def message(self, to_label: str, text: str) -> None:
        """Operational messages the host triggers, e.g. 'text the backup cleaner'."""
        self.hub.publish("outbound", {"to": to_label, "text": text})
        if self._sns:
            try:
                await asyncio.to_thread(self._sns.publish, TopicArn=self._topic, Subject="Turnover", Message=f"To {to_label}: {text}")
            except Exception as exc:
                log.warning("SNS publish failed: %s", exc)
