import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from turnover.ring.client import RingClient, RingAPIError


def test_snapshot_json_error_is_not_image():
    async def token(force_refresh):
        return "test-token"

    def fake_ring(request):
        return httpx.Response(
            200,
            json={
                "errors": [{
                    "status": "416",
                    "code": "MEDIA_NOT_FOUND"
                }]
            }
        )

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(fake_ring)
        ) as http:
            ring = RingClient(
                token,
                base_url="https://example.com",
                http=http
            )
            now = datetime.now(timezone.utc)

            try:
                await ring.snapshot(
                    "test-device",
                    between=(now - timedelta(minutes=10), now)
                )
            except RingAPIError as error:
                assert error.status == 416
                assert "MEDIA_NOT_FOUND" in str(error)
            else:
                raise AssertionError(
                    "JSON error was incorrectly accepted as an image"
                )

    asyncio.run(check())
