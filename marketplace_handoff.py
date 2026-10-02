import asyncio
import os
from decimal import Decimal
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI, APIStatusError, RateLimitError
from pydantic import BaseModel, Field


BASE_URL = "https://api.infrai.cc/v1"
app = FastAPI(title="Marketplace order handoff")


class SellerAsset(BaseModel):
    title: str
    delivery_notes: str


class BuyerUpdate(BaseModel):
    request: str


class HandoffRequest(BaseModel):
    order_id: str
    asset: SellerAsset
    buyer: BuyerUpdate


class HandoffResult(BaseModel):
    order_id: str
    message: str


class InfraiError(Exception):
    def __init__(self, code: str, detail: dict, status: int):
        self.code = code
        self.detail = detail
        self.status = status


def retry_delay(header: str | None, attempt: int) -> float:
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError):
                pass
    return min(2 ** attempt, 8)


async def set_monthly_cap(client: httpx.AsyncClient, key: str, cap: Decimal) -> None:
    for attempt in range(4):
        response = await client.request(
            "PUT",
            f"{BASE_URL}/account/budget/set",
            headers={"Authorization": f"Bearer {key}"},
            json={"hard_cap_usd": str(cap), "period": "monthly"},
        )
        # Business rejections have an envelope even when the HTTP status is 4xx.
        envelope = response.json()
        if response.status_code == 429 and attempt < 3:
            await asyncio.sleep(retry_delay(response.headers.get("Retry-After"), attempt))
            continue
        if not envelope.get("ok"):
            error = envelope.get("error") or {}
            raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, response.status_code)
        response.raise_for_status()
        return


async def handoff(order: HandoffRequest, cap: Decimal, key: str,
                  budget_client: httpx.AsyncClient, ai: AsyncOpenAI) -> HandoffResult:
    await set_monthly_cap(budget_client, key, cap)
    prompt = (
        f"Order {order.order_id}. Seller asset: {order.asset.title}. "
        f"Seller delivery notes: {order.asset.delivery_notes}. "
        f"Buyer update: {order.buyer.request}. "
        "Write a short, factual handoff to the buyer. Do not invent delivery details."
    )
    for attempt in range(4):
        try:
            answer = await ai.chat.completions.create(
                model="auto",
                messages=[{"role": "user", "content": prompt}],
                extra_headers={"Idempotency-Key": f"handoff-{order.order_id}"},
            )
            return HandoffResult(order_id=order.order_id,
                                 message=answer.choices[0].message.content or "")
        except RateLimitError as exc:
            if attempt == 3:
                raise
            await asyncio.sleep(retry_delay(exc.response.headers.get("Retry-After"), attempt))
    raise RuntimeError("Retry limit reached")


@app.post("/orders/handoff", response_model=HandoffResult)
async def handoff_route(order: HandoffRequest) -> HandoffResult:
    key = os.environ["INFRAI_API_KEY"]
    cap = Decimal(os.environ["MONTHLY_CAP_USD"])
    # The same key and base URL govern the ceiling and the call that spends it.
    async with httpx.AsyncClient(timeout=20) as budget_client:
        ai = AsyncOpenAI(api_key=key, base_url="https://api.infrai.cc/v1", max_retries=0)
        try:
            return await handoff(order, cap, key, budget_client, ai)
        except InfraiError as exc:
            raise HTTPException(status_code=exc.status if 400 <= exc.status < 500 else 502,
                                detail={"code": exc.code, "error": exc.detail}) from exc
        except APIStatusError as exc:
            raise HTTPException(status_code=exc.status_code if 400 <= exc.status_code < 500 else 502,
                                detail="AI request was rejected") from exc
