# Paper accounting model (0.5.0)

This is an offline spot ledger and synthetic demonstration. It is not a forward-running paper bot, a profitable strategy or an exchange-matching simulator.

```powershell
uv run python -m cryptoir paper-demo --output data/paper-demo.sqlite
uv run python -m cryptoir catalog --output data/catalog.jsonl
```

The demonstration uses invented prices and capital, explicitly labeled in the result. It is an accounting exercise only. Personal capital allocation and production settings are not stored in source control.

## Model behavior

- SQLite transactions atomically reserve balances, update fills and write journal entries. Reservations and snapshot consumption persist across restart.
- Initial balances are allocated once by venue and asset. The ledger does not refill capital, borrow, transfer or withdraw funds. Rejected intents do not mutate balances.
- Order IDs are idempotent for identical intents; conflicting reuse fails. Finite order notional bounds, quantity steps, price ticks and an explicit fee assumption are required.
- A spot limit IOC approximation is evaluated on the first eligible later snapshot and cancels any remainder. This does not imply that a venue supports IOC through its API.
- Source and receive timestamps must both follow simulated latency. Missing source time blocks a fill. A configurable fraction of displayed depth is shared across eligible orders in submission order; a snapshot cannot be consumed twice after restart.
- Explicit quote or received-base buy fees, price improvement, partial fills and finite TTL release excess reservations. Sell fees are in quote currency. Every order/report names its fee asset and separately records the quote equivalent. An unknown fee schedule must be supplied as an explicit research assumption.
- The caller must advance the simulation clock during feed loss; no background scheduler exists yet. Local cancellation is immediate; exchange cancellation races remain unmodeled.

## Remaining limits

The model lacks maker queues, self-trade prevention, fee-asset rounding, all venue filters, verified account fees, funding/liquidation, portfolio drawdown controls, benchmark valuation and forward orchestration. The order bound uses requested limit notional; it is not a complete portfolio exposure limit. Displayed depth may disappear, and later snapshots do not reflect market impact from earlier hypothetical fills. A participation fraction does not validate fill probability.

Catalogs preserve native symbols and unknown fields. They are not automatically turned into executable rules: currency scales, asset identity, minimum quantity, fee denomination and other filters must be reconciled first. OTC minimums are not spot minimums; fiat suffixes must not be assigned units by guesswork.

Tests cover reserve exhaustion, unowned sells, order limits, step rounding, fees, partial/zero fills, shared depth, latency, stale/missing time, venue isolation, cancellation, feed-loss expiry, restart and transaction rollback. These test the specified model; prospective evidence is needed to validate its assumptions.

## Versioned storage

New ledgers use schema 2. Schema 1 ledgers are refused by default rather than
silently changing their interpretation. For an intentional local migration,
`PaperLedger(path, upgrade=True)` first creates a consistent, exclusive SQLite
backup named `<filename>.schema1-backup.sqlite`, then transactionally adds fee
asset fields. Existing balances, reservations, journal entries, filled orders,
snapshot identities and old idempotency fingerprints are retained. Existing
fees remain quote-denominated; no historical fee is recalculated. A preexisting
backup is never overwritten. Old binaries refuse the new schema.

The Python `Rules` rate field is now named `fee_rate` (formerly
`quote_fee_rate`); the optional `buy_fee_mode` is `quote` or `received_base`.
The default preserves the old hypothetical quote-fee behavior. Choosing a mode
does not verify the exchange's fee currency, rounding or account tier.
