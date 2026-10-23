"""Turnover as an MCP server (Streamable HTTP), so a voice assistant can answer
"Is the cabin ready for tonight's guests?" from the same engine the dashboard uses.

Mounted at /mcp. Each tool returns structured data plus a `say` field: one short sentence
written to be spoken, because a host asking Alexa+ from the car wants an answer, not JSON.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from .app import Runtime

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def _t(iso: str, tz: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%I:%M %p").lstrip("0")


def transport_security(public_base_url: str) -> TransportSecuritySettings:
    """DNS-rebinding protection matters for servers on localhost; a public HTTPS deployment
    is reached server-to-server by assistants and is protected by auth instead."""
    if "localhost" in public_base_url or "127.0.0.1" in public_base_url:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "turnover.internal"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def build(rt: "Runtime") -> MCPServer:
    server = MCPServer(
        name="turnover",
        title="Turnover",
        description="Booking-aware Ring monitoring for vacation-rental hosts.",
        instructions=(
            "Use these tools to answer a host's questions about their rental homes today: who is staying, "
            "whether the cleaner arrived, whether a home is ready for check-in, and what needs attention. "
            "Prefer the `say` field when answering by voice."
        ),
        version="0.1.0",
    )

    def find(home: str) -> Any:
        q = home.strip().lower()
        props = rt.engine.properties()
        for p in props:
            if q in (p.id, p.name.lower()) or q in p.name.lower() or p.name.lower().split()[0] in q:
                return p
        return None

    @server.tool(title="Homes overview", annotations=READ_ONLY)
    def homes_overview() -> dict[str, Any]:
        """Every home's status right now: vacant, guests staying or turnover, plus anything that needs attention."""
        homes = []
        for p in rt.engine.properties():
            d = rt.engine.snapshot_of_day(p)
            open_alerts = [a for a in rt.engine.alerts(p.id, limit=30) if a.status == "open" and a.severity.value in ("urgent", "warn")]
            homes.append({"home": p.name, "state": d["state"], "status": d["headline"], "detail": d["live"], "needs_attention": [a.title for a in open_alerts]})
        flagged = [h for h in homes if h["needs_attention"]]
        if flagged:
            say = " ".join(f"{h['home']}: {h['needs_attention'][0].split(' · ')[0].lower()}." for h in flagged)
            verb = "needs" if len(flagged) == 1 else "need"
            say = f"{len(flagged)} of your {len(homes)} homes {verb} attention. {say}"
        else:
            say = f"All {len(homes)} homes are fine. " + " ".join(f"{h['home']} is {h['status'].lower()}." for h in homes)
        return {"say": say, "homes": homes}

    @server.tool(title="Is this home ready?", annotations=READ_ONLY)
    def home_status(home: str) -> dict[str, Any]:
        """Today at one home: the turnover (cleaner arrival and time on site), check-in readiness, and open alerts.

        Args:
            home: The home's name, e.g. "Lakeview" or "the cabin".
        """
        p = find(home) or (find("lakeview") if "cabin" in home.lower() else None)
        if p is None:
            names = ", ".join(x.name for x in rt.engine.properties())
            return {"say": f"I couldn't find a home called {home}. Your homes are {names}.", "found": False}
        d = rt.engine.snapshot_of_day(p)
        tz = p.timezone
        exps = {e["kind"]: e for e in d["expectations"]}
        spoken = {"vacant": "empty today", "occupied": "occupied", "turnover": "between guests"}[d["state"]]
        parts = [f"{p.name} is {spoken}."]
        if d["visits"]:
            v = d["visits"][-1]
            parts.append(f"The cleaner arrived at {_t(v['first_at'], tz)} and was last seen at the door at {_t(v['last_at'], tz)}.")
        elif "cleaner_arrival" in exps and exps["cleaner_arrival"]["status"] == "missed":
            parts.append("The cleaner hasn't arrived yet.")
        ready = exps.get("turnover_ready")
        if ready:
            parts.append({"met": "It's verified ready for check-in.", "missed": "It is not confirmed ready.", "pending": f"Readiness is checked at {_t(ready['deadline'], tz)}."}.get(ready["status"], ""))
        guest = exps.get("guest_arrival")
        if guest and guest["status"] in ("met", "late") and guest.get("met_at"):
            parts.append(f"Guests arrived at {_t(guest['met_at'], tz)}.")
        open_alerts = [a for a in rt.engine.alerts(p.id, limit=30) if a.status == "open" and a.severity.value in ("urgent", "warn")]
        extra = [a for a in open_alerts if a.kind != "cleaner_no_show"]
        if extra:
            parts.append(f"Also open: {extra[0].title.split(' · ')[0].lower()}.")
        return {
            "say": " ".join(x for x in parts if x),
            "found": True,
            "home": p.name,
            "state": d["state"],
            "visits": d["visits"],
            "expectations": d["expectations"],
            "open_alerts": [a.to_dict() for a in open_alerts],
            "ring_events_today": d["metrics"]["ring_events"],
            "muted_today": d["metrics"]["suppressed"],
        }

    @server.tool(title="Recent alerts", annotations=READ_ONLY)
    def recent_alerts(only_open: bool = True, limit: int = 5) -> dict[str, Any]:
        """The latest alerts across all homes, newest first.

        Args:
            only_open: Only alerts that still need the host.
            limit: How many to return (1-20).
        """
        names = {p.id: p.name for p in rt.engine.properties()}
        items = [a for a in rt.engine.alerts(limit=60) if not only_open or a.status == "open"][: max(1, min(limit, 20))]
        if not items:
            return {"say": "Nothing needs you right now.", "alerts": []}
        first = items[0]
        say = f"{len(items)} {'alert' if len(items) == 1 else 'alerts'}. Latest: {names.get(first.property_id, '')}, {first.title.split(' · ')[0].lower()}."
        return {"say": say, "alerts": [{**a.to_dict(), "home": names.get(a.property_id)} for a in items]}

    @server.tool(
        title="Ask the backup cleaner to cover",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
    )
    async def text_backup_cleaner(home: str) -> dict[str, Any]:
        """Message the home's backup cleaner to cover today's turnover. Only works when the regular cleaner hasn't arrived.

        Args:
            home: The home's name.
        """
        p = find(home)
        if p is None:
            return {"say": f"I couldn't find a home called {home}.", "sent": False}
        alert = next((a for a in rt.engine.alerts(p.id, limit=30) if a.kind in ("cleaner_no_show", "turnover_missing") and a.status == "open"), None)
        if alert is None:
            return {"say": f"There's no missed cleaning at {p.name} right now, so I didn't message anyone.", "sent": False}
        rt.engine.act(alert.id, "text_backup")
        who = p.backup_cleaner_name or "your backup cleaner"
        hhmm = datetime.strptime(p.checkin_time, "%H:%M").strftime("%I:%M %p").lstrip("0")
        await rt.notifier.message(who, f"Hi {who}, can you turn over {p.name} today? Guests arrive at {hhmm}.")
        return {"say": f"Done. I asked {who} to cover {p.name}. I'll tell you when someone arrives at the door.", "sent": True}

    return server
