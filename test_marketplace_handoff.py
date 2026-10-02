from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from marketplace_handoff import BuyerUpdate, HandoffRequest, SellerAsset, handoff


@pytest.mark.asyncio
async def test_cap_is_set_before_order_handoff():
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path, request.read().decode()))
        return httpx.Response(200, json={"ok": True, "data": {}, "error": None, "metadata": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as budget_client:
        completion = AsyncMock(side_effect=lambda **kwargs: (
            calls.append(("CHAT", kwargs["model"], kwargs["extra_headers"])) or
            SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Your asset is ready for review."))])
        ))
        ai = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completion)))
        result = await handoff(
            HandoffRequest(order_id="order-42",
                           asset=SellerAsset(title="Poster draft", delivery_notes="Review the PNG proof"),
                           buyer=BuyerUpdate(request="Use the blue version")),
            Decimal("25"), "test-key", budget_client, ai,
        )

    assert result.message == "Your asset is ready for review."
    assert calls[0][0:2] == ("PUT", "/v1/account/budget/set")
    assert '"hard_cap_usd":"25"' in calls[0][2]
    assert '"period":"monthly"' in calls[0][2]
    assert calls[1] == ("CHAT", "auto", {"Idempotency-Key": "handoff-order-42"})
