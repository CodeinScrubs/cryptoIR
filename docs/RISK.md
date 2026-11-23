# Persistent paper risk guard (0.6.0)

Implemented and locally tested. This is a component for **one venue and one
USDT spot pair**, not live risk protection or a continuous paper service.

```powershell
uv run python -m cryptoir risk-demo --output data/risk-demo.sqlite
```

The example creates synthetic capital and invented prices, simulates a purchase
and price drop, rejects a later entry, and reopens the ledger to verify the loss
latch. It does not connect to an exchange or use the user's capital.

## State and limits

`SpotRiskGuard` uses the same SQLite ledger as `PaperLedger`. Initial capital,
market rules, fee mode, exposure fraction, order notional, open-order count,
daily-loss fraction, maximum-drawdown fraction, feed age and displayed-depth
participation must be specified. A configuration hash seals these settings;
reopening the experiment with different settings is refused. The guard must be
initialized before any order, with quote-only capital equal to the stated seed.

The guard marks inventory using displayed bids less sell fees. Unknown assets,
another venue's inventory, insufficient depth, missing source times and stale
data block new orders; they never acquire favorable default valuations.
Quantity below an executable lot/minimum remains owned but has zero assumed
liquidation value. Quote reserves and base reserves are included in portfolio
equity, and pending buys are included in projected exposure.

Exposure is an **entry budget** based on the supplied book and estimated
round-trip costs. Price movement can change an existing position's weight.
This module does not automatically rebalance a position to the entry fraction.
Before filling pending buys on a new book, the guard rechecks the budget.

Daily loss uses Tehran calendar days (UTC+03:30 in these present-day runs).
An overnight data gap uses the last known equity as the new day baseline, so a
new mark does not silently erase an unobserved overnight loss. Overall drawdown
uses the persistent observed high-water mark. Neither calculation observes price
extremes between received books or establishes a guaranteed loss bound.

A daily-loss, drawdown or manual-stop latch blocks new buys and cancels pending
buys. It persists across recovery, day changes and restarts. It has no automatic
reset. Owned sell orders remain possible with valid data and within the order
limits. Halting **does not liquidate existing holdings**. Existing exposure,
unrealized loss and data uncertainty must still be reported and managed.

## Runtime contract

- Route reservations through `guard.submit`. Its preflight executes under the
  same SQLite write transaction as the reservation. A rejected intent commits
  the risk latch and rejection journal, but creates no order or reservation.
- Route books through `guard.on_book`. Source admission, pre-fill risk checks,
  matching, balances, journal and post-fill risk share a single SQLite write
  transaction. The forward runner uses `process_book` to also obtain admission.
  Standalone `observe` admits a book for valuation and consumes its timestamp.
  Use the matching entry point for observations intended to evaluate orders.
- On a feed gap, call `guard.invalidate`; it cancels pending buys and marks data
  unavailable. A newer valid source observation can clear the temporary data
  block. Regressed timestamps and conflicting levels at the current timestamp
  cause a data block. Equivalent repeats leave risk state as it was. Loss and
  manual latches persist.
- Drive `guard.advance` from a timer even when the feed stops. It expires orders
  and cancels pending buys when the exchange timestamp exceeds the freshness
  budget. A recent processing time does not extend that budget.
- Use a single owner for a ledger. The [forward runner](FORWARD.md) enforces
  OS-level single-writer ownership and routes session mutations through the guard.

No portfolio conversion across IRT/USDT or venues, automatic hedge, exchange
native protection, cancellation-race model, authenticated reconciliation,
always-running service, notification delivery or live order transport is included.
Those remain required before unattended trading. A laptop shutdown cannot
provide exchange-side protection.
