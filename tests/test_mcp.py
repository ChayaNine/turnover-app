"""The MCP server answers from the same engine as the dashboard."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from turnover import config, mcp_server
from turnover.app import create_app


@pytest.mark.anyio
async def test_mcp_tools_in_memory(tmp_path):
    from mcp import Client

    s = config.Settings(db_path=str(tmp_path / "m.db"), demo_mode=True, ring_hmac_key="k")
    app = create_app(s)
    with TestClient(app) as c:
        c.post("/api/demo/start", json={"minute_delay": 0, "pause_on_urgent": False})
        for _ in range(300):
            st = c.get("/api/state").json()
            if st["player"]["delivered"] and not st["player"]["running"]:
                break
            time.sleep(0.2)
        server = mcp_server.build(app.state.rt)
        async with Client(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            assert names == {"homes_overview", "home_status", "recent_alerts", "text_backup_cleaner"}
            r = await client.call_tool("home_status", {"home": "the cabin"})
            data = r.structured_content or json.loads(r.content[0].text)
            assert data["found"] and data["home"] == "Lakeview Cabin"
            assert "verified ready" in data["say"] and "Guests arrived" in data["say"]
            r = await client.call_tool("text_backup_cleaner", {"home": "Lakeview"})
            data = r.structured_content or json.loads(r.content[0].text)
            assert data["sent"] is False  # the no-show already resolved; nothing to send


@pytest.fixture
def anyio_backend():
    return "asyncio"
