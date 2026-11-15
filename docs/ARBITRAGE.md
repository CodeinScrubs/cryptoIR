# Cross-venue snapshot feasibility

Version 0.12.0: implemented and locally tested. This is a rejection/diagnostic
tool, not an arbitrage execution engine, portfolio backtest or accepted strategy.
It uses public recordings and needs no account credentials.

## Registered observation and reconstruction

[The v1 plan](../research/arbitrage-v1.json) fixes BTC/USDT, the three venues,
one 900-second observation, storage/freshness limits, a gross buy amount, cost
scenarios, market rules and independent synthetic capacity ceilings. It was
registered before the new recording. The earlier ten-minute sample was already
seen; this is a new prospective observation, not a claim of independent strategy
discovery or sufficient statistical evidence.

```powershell
uv run python -m cryptoir spread-screen --plan research/arbitrage-v1.json --catalog data/catalog-arbitrage-v1.jsonl --recording data/arbitrage-v1-observation.jsonl.gz --output data/arbitrage-v1-replay-new
```

Use a new output directory. These input recordings are local, not in Git. A
fresh catalog or observation needs an appropriately registered new plan; do not
replace the pinned catalog hash silently. The command verifies that the original
catalog bodies, normalized markets and registered rules agree. It then audits
and reconstructs raw WebSocket frames, checking the requested pair, venues,
duration and byte cap against the recording header. Source and plan hashes are
included in the report. Git history and recording timestamps supply registration
evidence; the local tool alone cannot prove when an idea was selected.

## Conditional accounting

- For a received-base buy fee, net acquired base equals gross buy quantity less
  that fee. Sell quantity is rounded down to the selling venue's increment.
  It never exceeds acquired base, and separate prefunded sell inventory must
  cover it. Quote-denominated buy fees are also supported explicitly.
- Both sides walk the displayed depth. Missing depth, known order-minimum or
  quantity-step violations, insufficient buy cash including its reserve, and
  insufficient sell inventory reject the comparison.
- Net quote estimate is sell proceeds after the assumed quote fee, less buy
  cost and both legs' adverse-cost reserves. Residual base remains owned and
  is reported with zero credited quote value. It is not a realized exit.
- No balances change. Capacity ceilings are reused for each comparison, so
  positive estimates cannot be added into profit, a return or a trade count.
  Event-frequency counts are correlated observations, not independent trials.
- Strict comparisons require known fresh source timestamps on both books.
  Missing minimum notional also blocks them. Unknown minimum quantity is
  disclosed. Disconnects and unavailable books clear the affected venue state.
  Later books never become available at an earlier decision time.

The zero-cost case is only an optimistic diagnostic. The base/stress scenarios
are explicit assumptions; Wallex and Tabdeal fee rates and charged assets are
not verified account terms. The Nobitex rate uses a previously observed public
schedule, also not an account tier. No default fee is inferred from a missing
field. The normalized private account reports are not imported automatically.

## Remaining evidence

Even a positive stressed estimate cannot establish an executable opportunity.
Full venue filters and asset identity, authenticated fees/permissions, depth
stability, latency, non-atomic legs, rejects, partial fills and outage exposure
remain unresolved. Inventory moves between venues in a successful pair of
trades; keeping the aggregate amount does not replenish the selling venue.
Manual rebalancing costs and delays can consume any apparent spread.

A subsequent two-leg paper model must carry venue balances through time, record
each fill independently, charge costs in the right asset, retain unhedged
exposure, and include manual rebalance assumptions. This diagnostic reports
neither fiat return nor leveraged performance. It changes no personal paper
account, live permission, withdrawal or transfer behavior.
