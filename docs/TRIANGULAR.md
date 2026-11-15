# Single-venue triangular diagnostics

Version 0.16.0 extends bounded recording and replay from Nobitex BTC, ETH and
SOL to a deterministic catalog universe of cycles through USDT and IRR.
This is a conditional feasibility screen, not
three-leg execution, realized profit or a strategy acceptance test.

The fixed [first plan](../research/triangular-v1.json) was published before
performance calculation. It freezes 20 samples, at least 30 seconds between
requests, a synthetic 10-USDT budget per cycle, 10% displayed-depth participation,
observed minimums/increments, and zero/base/stress cost scenarios.

The first prospective capture completed all twenty samples. All six routes were
nonpositive in all three scenarios, including zero cost; no comparison was
rejected. [Recorded results and hashes](../research/RESULTS.md) retain the failed
feasibility observation. It is not a universal rejection across market periods.

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

## Broader exploratory universe

The [second plan](../research/triangular-v2-universe.json) selects every active
native asset with scale one and both USDT and IRT catalog markets: 248 assets,
497 books. Nine scaled and ten inactive assets are excluded. Membership uses
metadata only; no price or return ranking is applied. Wrapped and tokenized
instruments may appear without establishing underlying ownership or eligibility.
The catalog-scoped parser maps native IRT suffixes to IRR only after validating
membership and recorded rules. General paper and funding adapters retain their
existing narrower scope.

This is offline reuse of the twenty previously recorded all-market responses.
Both the recording hash and original capture-plan hash are pinned separately
from the new exploration plan. BTC/ETH/SOL outcomes were already observed.
Changing the plan does not make existing data prospective or untouched. Replay
plans cannot start a new capture.

```powershell
uv run python -m cryptoir triangle-screen --plan research/triangular-v2-universe.json --catalog data/triangular-v1-setup/catalog.json --recording data/triangular-v1-observation.jsonl.gz --output data/triangular-v2-universe-result
```

Capture completion and universe coverage are reported separately: coverage
requires at least one valid comparison for every route/scenario, not twenty.
Per-route counts and rejection reasons retain all missing evidence. An integrity
error invalidates both completion flags. Counts remain correlated observations.

Only a route with at least ten valid comparisons and three positive estimates
under both base and stress qualifies for separately registered fresh observation.
The frozen plan limits follow-up priority to five routes by stress positive count,
then base positive count, then ascending asset/route. This is a research resource
rule, not a statistical confidence threshold or profitability acceptance.

The completed replay had valid comparisons for 220 of 248 assets, with 28
remaining unavailable because of book or exit-depth limits. Each scenario had
8,800 valid comparisons; none were positive under base or stress. Four zero-cost
positives did not survive costs. No route qualified for fresh follow-up.
The [research ledger](../research/RESULTS.md) preserves all routes and limitations.
