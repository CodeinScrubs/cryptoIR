# Historical data and preliminary strategy screens

Implemented in version 0.4.0. A cost screen is a preliminary rejection tool. It
does not establish tradability, profitable paper operation, or live readiness.

## Provenance and day boundaries

`history` downloads public hourly candles and separate daily candles from the
[Nobitex UDF endpoint](https://apidocs.nobitex.ir/market_data/دریافت-داده-های-ohlc)
and [Wallex UDF endpoint](https://developers.wallex.ir/docs/basic-services-candles).
Requests are bounded to 480 hourly or 365 daily bars and spaced by at least
1.1 seconds. A failed request leaves its incomplete manifest and evidence.
Use a new output directory for each download; nothing is silently replaced.

Daily timestamps define the exchange session. Observed Nobitex sessions begin
at 20:30 UTC (00:00 Tehran); observed Wallex sessions begin at 00:00 UTC
(03:30 Tehran). Hourly candles retain their actual venue timestamps. Incomplete
edge sessions are excluded. Interior gaps, session changes, unexpected hourly
alignment, duplicate epochs and inconsistent OHLCV prevent acceptance.

`history-audit` reconstructs the dataset from the original raw response bodies,
checks request windows, file and body hashes, and exact daily OHLCV aggregates.
It prints the complete date-by-date delta table. It checks the saved normalized
bars, reconciliation report and manifest against that reconstruction. Hashes
detect mismatched artifacts; they are not cryptographic proof of exchange
authenticity or a substitute for the source's own data quality.

```powershell
uv run python -m cryptoir history --venue nobitex --start 2025-07-02 --end 2026-11-01 --output data/history-nobitex-btc
uv run python -m cryptoir history-audit data/history-nobitex-btc
uv run python -m cryptoir research --plan research/baseline-v1b.json --dataset data/history-nobitex-btc --output data/research-btc
```

The first two dates are UTC request boundaries. Research splits snap backwards
to the venue's day open; actual evaluation epochs are included in every trial.

## Causal screen

- Signals see completed hourly candles only. A decision at a bar close executes
  no earlier than the open one full hour later, with a configurable 1..24-hour
  delay. No signal receives a future bar or the dataset itself.
- The model buys and sells at that later open with an explicit adverse price
  adjustment and quote-denominated fees. It makes no maker or touched-limit fill
  assumption. Price costs can place an assumed execution outside the candle.
- Quantity is rounded down, checked against an explicit minimum, capped by cash
  and the allocation at entry, and limited to a specified fraction of the
  **previous** hour's volume. This past-volume proxy cannot prove available
  liquidity, and can overestimate fills in an outage or sudden volume collapse.
- Unfilled IOC remainder is cancelled. An unfulfilled target may create a new
  intent later. Open positions are not continuously rebalanced to the initial
  allocation; their weight can grow with prices. This is not a full risk engine.
- Held base asset is marked at close less hypothetical exit price costs and
  fees. It remains an open position, not a fabricated realized sale. Reporting
  distinguishes realized P&L, unrealized P&L, actual modeled fees and estimated
  final exit costs. Hourly close drawdown misses intrahour extremes.
- Each split starts with the same synthetic reference cash. Prior completed
  candles warm up the signal, but positions and profits do not carry into the
  next split. Results are independent screening segments, not a continuous
  walk-forward portfolio equity curve.

## Experiment control

The registered plans specify three fixed candidates: 24/168-hour moving-average
trend, a 168-hour breakout with a 72-hour exit, and a 24-hour mean-reversion rule
with a 2% entry threshold. These are hypotheses, not recommendations.

Plans also freeze data windows, base/stress costs, synthetic capital, quantity
rules, allocation and gates. Discovery must pass the positive-return,
drawdown, completed-exit and equal-allocation BTC-hold gates under the specified
costs. The winner is selected by worst-cost return with a deterministic tie
break. Only that unchanged candidate advances to validation, then held-out
evaluation. Rejected discovery stops without calculating later returns.

Every trial start, result and failure is recorded in a local journal. Run
artifacts include the exact plan, plan/data/source hashes, trades, equity and
metrics. Source hashes permit code attribution; Git commits preserve source
versions. A previously inspected holdout is never made unseen by renaming a
plan. Data-driven plan amendments and the reason for them belong in
[the research changelog](../research/CHANGELOG.md).

Initial plans compare USDT cash and equal-allocation BTC holding within each
split. Full BTC allocation, IRT performance and actual conversion of personal
capital remain outstanding. Unknown historical fees, fee denomination,
precision/minimum rules, symbol selection bias, tax and operating costs remain
explicit assumptions. No borrowed money, liquidation, funding, queue model,
statistical confidence claim, calibrated fills or deployment approval is
included. Passing this screen would justify further research only.
