# Maker quote-width feasibility

Implemented and locally tested in 0.21.0. This is a screen of quoted spreads,
not a market-making backtest, passive fill simulator or accepted strategy.
The [fixed plan](../research/maker-spread-v1.json) was published in `3e55ae7`
before calculation. Evaluation source: `f4b28f9`.

## Source and scope

The screen reuses the exact twenty-sample Nobitex all-book recording from the
triangular study. It verifies the recording, capture-plan, universe-plan and
catalog hashes, original response bodies, timestamps, sample order, spacing
and terminal record. It replays all 248 active, unscaled, dual-quote assets from
the pinned metadata, considering only their USDT books. That inherited universe
excludes single-quote and scaled assets. No new exchange request is needed.

This recording was already seen for arbitrage work. Its roughly ten minutes
of sparse, correlated quotes do not become fresh evidence through a new name.
Every missing, invalid, stale, wrong-tick or below-minimum comparison remains
in the report. Capture completeness means the recording ended normally;
universe coverage requires at least one valid comparison per market/scenario,
not twenty valid samples or complete exchange history.

```powershell
uv run python -m cryptoir maker-screen --plan research/maker-spread-v1.json --universe-plan research/triangular-v2-universe.json --catalog data/triangular-v1-setup/catalog.json --recording data/triangular-v1-observation.jsonl.gz --output data/maker-spread-v1-result
```

## Conditional arithmetic

For each book, the hypothetical buy uses the displayed best bid and the sell
uses the displayed best ask. These are **two assumed passive executions** that
need not happen, let alone at one snapshot's prices. A candle touch, a quote
update or displayed liquidity is never converted into a fill.

The synthetic quote budget is 10 USDT per comparison. Quantity is rounded down
to the native step and capped by that budget and 10% of each displayed top-level
quantity. Competitor quotes limit our hypothetical footprint only; they are
neither executable contra-side liquidity nor proven queue capacity. The buy fee
is deducted in received base, the sell quantity is rounded again, and dust
remains owned with zero assumed resale value. Both minimum notionals and price
ticks must pass. Unspent USDT remains explicit.

The base scenario uses the current public USDT maker fee of 0.1% per leg; stress
doubles it. The source is [Nobitex's pricing page](https://nobitex.ir/pricing/),
checked on 2026-11-16. These are public assumptions, not account acceptance or
reconstructed historical tiers. Additional reserves of 15/30 basis points on
each leg's notional represent hypothetical adverse-selection/cost sensitivity;
they are not measured execution losses. A separate zero-cost scenario is diagnostic.

Known-width margin equals sell proceeds after its fee, minus buy expenditure
and the reserve. It is not realized P&L. Margins are not summed across times or
markets, and capital is not duplicated into a claimed portfolio return.

## Fixed follow-up rule and observed result

Only markets with at least ten valid observations per cost scenario, at least
50% positive widths in both base and stress, and a strictly positive median
stressed width qualify. Select at most three by the median per-sample stressed
width divided by that sample's spent USDT; ties use ascending asset name. This
permits only a separately registered fresh public book/trade observation.
It is not a strategy, paper-performance or deployment gate.

| Scenario | Valid comparisons | Positive conditional widths | Markets with valid samples |
| --- | ---: | ---: | ---: |
| Zero-cost diagnostic | 4,202 | 4,202 | 217 |
| Base assumed | 4,202 | 3,026 | 217 |
| Stress assumed | 4,202 | 1,898 | 217 |

Of 248 markets, 181 had all twenty valid comparisons and 31 had none. Each
scenario excluded 540 missing/invalid-book comparisons and 218 below-minimum
quote footprints. All source samples completed, but universe coverage is partial.
There are 14,880 total comparisons across all three scenarios; none is an
independent trading trial.

The unchanged rule selected RDNT, G and SKL for fresh observation. All had
twenty positive stressed quote widths. Their median spent footprints were
0.5038600, 1.9994912 and 9.999858 USDT, respectively. A large percentage width
on a tiny displayed quote must not be presented as a return on the whole 10-USDT
budget or owner capital. The corresponding hypothetical stressed width values
were 0.1156357800, 0.0747024994 and 0.349913771 USDT. No such profit was earned.
[Complete per-market results and evidence hashes](../research/evidence/maker-spread-v1-summary.json).

## Still needed

Fresh book/trade reception, continuity, trade activity, queue evolution,
adverse-selection markouts, inventory risk, cancellations, conditional fill
bounds and sustained prospective performance remain unverified. Official
[public WebSocket trades](https://raw.githubusercontent.com/nobitex/docs-api/master/source/includes/_websocket.md)
and [REST trade-list](https://raw.githubusercontent.com/nobitex/docs-api/master/source/includes/_market_data.md)
documentation provide discovery routes; documentation alone is not observed
acceptance, and a public trade is not our order's fill. No private credentials,
paper account changes or live orders are part of this screen.
