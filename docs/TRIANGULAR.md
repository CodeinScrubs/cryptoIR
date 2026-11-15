# Single-venue triangular diagnostics

Version 0.15.0 implements bounded recording and replay for Nobitex BTC, ETH and
SOL cycles through USDT and IRR. This is a conditional feasibility screen, not
three-leg execution, realized profit or a strategy acceptance test.

The fixed [first plan](../research/triangular-v1.json) was published before
performance calculation. It freezes 20 samples, at least 30 seconds between
requests, a synthetic 10-USDT budget per cycle, 10% displayed-depth participation,
observed minimums/increments, and zero/base/stress cost scenarios.

## Source and units

The official [market-data reference](https://github.com/nobitex/docs-api/blob/master/source/includes/_market_data.md)
documents the public all-market order-book GET. Each market has its own source
timestamp: sharing one response does not prove simultaneous execution.
The [WebSocket reference](https://github.com/nobitex/docs-api/blob/master/source/includes/_websocket.md)
identifies native `BTCIRT` prices as rial. The adapter retains raw prices in IRR
for the explicitly scoped BTC/ETH/SOL/USDT fiat books. It never labels them toman
or divides only one leg by ten. Other venue fiat mappings remain unsupported.

Public system options supply the recorded minimum notionals, quantity steps,
price ticks, active-currency flag and native scale. The complete catalog file is
hash-pinned; replay reconstructs its rules from the raw response before use.
All selected native scales must be one. Currency metadata does not establish
market status, account eligibility or order acceptance.

The [public fee page](https://nobitex.ir/pricing/) observed on 2026-11-15 listed
base taker rates of 0.13% for USDT markets and 0.25% for fiat markets, charged in
the received asset. These are explicit scenarios, not a verified account tier.
Stress doubles each rate. An additional 15/30 basis points of **gross received
units** is reserved on every leg. This is a hypothetical cost deduction, not an
actual fee, price prediction or calibrated execution model.

## Cycle arithmetic

Each asset has two fixed directions:

1. USDT buys the asset; the asset sells for IRR; IRR buys USDT.
2. USDT sells for IRR; IRR buys the asset; the asset sells for USDT.

Buys walk only the allowed fraction of asks, round down by the recorded quantity
step and preserve unspent input. Sells require sufficient bid depth for the
rounded quantity; a missing minimum or insufficient exit depth rejects the
comparison. Each leg deducts its fee and reserve before the next leg is sized.
No later leg receives units that the earlier leg did not produce.

Unspent initial USDT stays cash and is included in final USDT. Intermediate IRR
or crypto residuals remain reported, with zero assumed USDT liquidation value.
Partial budget deployment is visible in the leg details. The estimate is final
USDT minus initial synthetic budget; cycles reuse that budget and displayed
liquidity independently. Counts are correlated and estimates must not be added
into a portfolio return.

## Capture and replay

Using the already preserved catalog pinned by the plan:

```powershell
uv run python -m cryptoir triangle-record --plan research/triangular-v1.json --catalog data/triangular-v1-setup/catalog.json --output data/triangular-v1-observation.jsonl.gz
uv run python -m cryptoir triangle-screen --plan research/triangular-v1.json --catalog data/triangular-v1-setup/catalog.json --recording data/triangular-v1-observation.jsonl.gz --output data/triangular-v1-result
```

A newly downloaded catalog needs a separately registered plan, not an edit to
the existing experiment. Output paths are exclusive. Capture uses public GETs,
stops on a source failure or logical-byte cap, and preserves incomplete evidence.
There is no automatic retry or favorable observation-window search.

Replay checks raw body hashes, parsed payload equality, duplicate JSON fields,
endpoint provenance, request clocks, capture order, spacing, plan identity and
the completion footer. Every cycle requires all three books from its own sample
with source/receive ages within five seconds and valid native price increments.
Missing, stale, future, malformed or crossed books are rejected; an earlier
sample is never substituted. Missing footer/route evidence cannot be complete.

Hash consistency is not exchange authentication. Snapshot freshness does not
model latency, partial fills, cancel races, three-leg failure, inventory recovery
or a guarantee that displayed orders remain available. No account key, actual
balance, order, transfer or paper-ledger mutation is used by these commands.
