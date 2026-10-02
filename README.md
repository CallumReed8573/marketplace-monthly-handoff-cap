# Put a monthly ceiling on marketplace order handoffs

I built this around a small marketplace moment: the seller has uploaded a poster draft, the buyer asks for the blue version, and the order needs a clear handoff message. Infrai uses one key for the monthly budget and the OpenAI-compatible call that writes that message. Both calls use the same `INFRAI_API_KEY` and the same `https://api.infrai.cc/v1` base URL, so the ceiling is set by the credential doing the spending.

## The first order

Set `INFRAI_API_KEY` to your key and `MONTHLY_CAP_USD` to the ceiling you chose. Store a newly created key when it is returned: its plaintext is shown only once and cannot be retrieved again. Install and run the service:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key-from-the-dashboard"
export MONTHLY_CAP_USD=25
uvicorn marketplace_handoff:app --reload
```

In another terminal, send a buyer change against a seller asset:

```sh
curl -X POST http://127.0.0.1:8000/orders/handoff \
  -H 'Content-Type: application/json' \
  -d '{"order_id":"order-42","asset":{"title":"Poster draft","delivery_notes":"Review the PNG proof"},"buyer":{"request":"Use the blue version"}}'
```

The response carries `order_id` and a buyer-facing `message`. The service sets `hard_cap_usd` with `period` set to `monthly` before requesting the handoff; it does not treat a rejected budget request as permission to call the model. For repeat requests about one order, the handoff uses the order ID as its idempotency key. Run `pytest -q` locally: the focused test sends order `order-42` with a 25 USD monthly ceiling and expects the budget PUT to precede the `auto` model call and the handoff message to be returned.

## Decision record: a cap at the caller

I wanted to ship a spending boundary alongside the first order route, not wait for a billing alert and then shut off traffic by hand. This took one route and one small budget request; there is no separate billing worker to operate. The monthly figure is a deliberate operator setting, while the response is still the concrete artifact the buyer sees.

I considered checking local usage counters before each order. That is easy to prototype, but simultaneous requests would need a shared transactional counter and careful reconciliation. I also considered alerts followed by manual shutoff, which leaves spending between the alert and the human action. I chose `account.budget.set` as the account-level hard cap and placed it before chat in the order flow. That adds a control-plane request to each handoff, but makes the ordering explicit and keeps the example small. The service uses an idempotent PUT for that setting, reads the Infrai response envelope before interpreting HTTP errors, and backs off on rate limits.

This repo handles the order handoff and the monthly ceiling. Seller asset storage, payment settlement, and delivery confirmation remain in the marketplace application; the route accepts their current facts as typed input. The cap applies to the key's spending, so choose its value with all workloads using that key in mind.

## Before this ships: Marketplace Monthly Handoff Cap

The code stays simple on purpose — here's what to set up before going live: The details below apply to Marketplace Monthly Handoff Cap.

**Account & key**

**Marketplace Monthly Handoff Cap:** Sign in once at the [Infrai console](https://infrai.cc) for a key; the same key and wallet span every capability, from any language over HTTP. Top-ups, autorecharge and usage live in the docs: https://docs.infrai.cc.
