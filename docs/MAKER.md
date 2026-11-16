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

## Bounded fresh observation

The separately registered [activity plan](../research/maker-observation-v1.json)
selects RDNT, G and SKL before requesting fresh books or trades. Registration
commit: `4a2ab36`. The implementation first validates one fresh public options
response against the pinned native rules. Active currency metadata is not proof
that order placement is available. It then opens one public WebSocket for 900
seconds, subscribes to all six book/trade channels, records original frames and
public commands, and stops without retry on protocol, sequence, clock or
connection failure. The logical limit is 64 MiB with a 512-MiB disk reserve.

```powershell
uv run python -m cryptoir maker-observe --plan research/maker-observation-v1.json --catalog data/triangular-v1-setup/catalog.json --directory data/maker-observation-v1
uv run python -m cryptoir maker-observation-audit data/maker-observation-v1 --output data/maker-observation-v1/report.json
```

Use a new directory; do not overlap venue collectors. The offline audit verifies
raw hashes, command/acknowledgement order, sequence, metadata and clocks. It
reconstructs public activity and conditional widths using time of availability,
retaining stale or invalid observations as exclusions. Only an already received,
source-timed book fresh at trade availability may enter descriptive trade-price
comparisons. Trade types remain raw labels; no aggressor, unique-trade identity,
queue position, cancellation or passive fill is inferred. A fall in displayed
quantity can be a cancellation, trade or replacement. Unknown completeness of
the matching engine is retained even when observed publication offsets agree.

The fixed further-measurement rule requires normal full duration, all six
acknowledgements, no detected continuity/clock break, at least ten fresh book
publications and five positive-volume fresh trade publications per asset, plus
a positive median stressed conditional width. This permits only separately
registered queue/adverse-selection measurement. Quiet assets are not replaced,
and a quiet window is not proof that the venue had no trades. No public
observation qualifies as strategy acceptance or prospective portfolio P&L.

## Fresh observation result (0.22.0)

The single connection ran from 15:40:00 to 15:55:00 UTC on 2026-11-16
(19:10 to 19:25 Tehran), ending normally after 900.0869162 seconds. All six
subscriptions were accepted. The raw audit reconstructed 233 records and
229,372 logical bytes; source hashes match implementation commit `1fcf67a`.
The maximum local clock discrepancy was 5.4383 ms. All 149 received book
publications had source ages below 447 ms and contiguous observed offsets.
These are properties of this recorded connection, not a completeness claim
about the matching engine or unobserved channels.

| Asset | Fresh book publications | Trade publications | Valid widths per scenario | Median stressed conditional width (USDT) | Further-measurement gate |
| --- | ---: | ---: | ---: | ---: | --- |
| RDNT | 0 | 0 | 0 | Unknown | Not met |
| G | 90 | 0 | 87 | 0.0535331600 | Not met |
| SKL | 59 | 0 | 59 | 0.258093210 | Not met |

All valid base/stress widths were positive. Three G books per scenario were
excluded by the unchanged minimum/footprint rule. These publication-weighted
values remain hypothetical two-fill widths, not earned profit, independent
trials or time-weighted returns. G had two same-price bid-quantity decreases;
none is classified as a trade or our fill. RDNT has no measured width in this
window; its previous screen result is not substituted.

No asset meets the preregistered activity condition. The run was not extended
and assets were not replaced. Acknowledgement without any trade publication
also leaves the end-to-end trade-data route unverified. No independent active
control market was included in this fixed six-channel run. A separately
registered reference-market control is the next data-quality question; silence
here is not proof that the venue had no trades. [Evidence and hashes](../research/evidence/maker-observation-v1-summary.json).

## Still needed

Actual trade-payload reception, unobserved-channel continuity, queue evolution,
adverse-selection markouts, inventory risk, cancellations, conditional fill
bounds and sustained prospective performance remain unverified. Official
[public WebSocket trades](https://raw.githubusercontent.com/nobitex/docs-api/master/source/includes/_websocket.md)
and [REST trade-list](https://raw.githubusercontent.com/nobitex/docs-api/master/source/includes/_market_data.md)
documentation provide discovery routes; documentation alone is not observed
acceptance, and a public trade is not our order's fill. No private credentials,
paper account changes or live orders are part of this screen.


The subsequent [reference trade-feed control](STREAMS.md#reference-trade-feed-control-0230)
observed fresh trade publications for BTCUSDT and USDTIRT. It retains a candidate
zero-versus-0.01 volume conflict and does not establish the prior selected
markets' trade activity. Their activity gate remains unmet. Further maker
selection needs measured activity together with net quote width; a wide quote
alone does not justify queue simulation or deployment.


## Activity-aware feasibility screen (0.24.0)

The [new registered screen](../research/maker-activity-v1.json), committed in
`56bf5f6`, ranks active unscaled USDT native markets by reported quote-volume
statistics before requesting new books or recent trades. It keeps at most
twenty, with closed/unknown markets and invalid/missing metadata explicitly
excluded. The aggregate statistic has no source timestamp; it is a ranking
input, not evidence of current executable activity. No below-cutoff market is
substituted after quote or activity results.

```powershell
uv run python -m cryptoir maker-activity --plan research/maker-activity-v1.json --directory data/maker-activity-v1
uv run python -m cryptoir maker-activity-audit data/maker-activity-v1 --output data/maker-activity-v1-result
```

Use new directories and no overlapping venue collector. One options read and
one market-statistics read precede ten all-book requests at thirty-second
spacing. One recent-trades list per selected symbol follows, at two-second
spacing. The maximum is thirty-two public GETs, with no retry, WebSocket,
private account or order action. Raw response hashes, sequence, clocks,
monotonic request spacing, deterministic selection and termination are audited.
The logical cap is 64 MiB and the disk-reserve guard is 512 MiB.

The existing synthetic quote footprint and all three maker fee/reserve scenarios
are pinned unchanged. Both received-asset fees, rounding, minimum notionals,
dust, stale data and size limits remain in every comparison. A later recent
list never enters an earlier quote calculation. It supplies only a final
follow-up activity indicator: at least five distinct positive time/price/volume/
raw-type fingerprints within five minutes of list availability. Repeated rows,
zero volumes, stale rows and unknown unique-trade identity remain explicit.
Recent lists are finite; quote and activity windows need not coincide.

Further observation requires ten valid quotes per cost, at least half positive
in both base/stress, and a positive median stressed width, plus the fixed
activity threshold. At most three are ranked first by recent distinct positive
fingerprints, then median stressed width per spent USDT and native symbol.
This permits only separately registered prospective measurement. No candidate
has earned fills, portfolio returns, strategy acceptance or live permission.

### Activity-aware screen result

The capture ran on 2026-11-17 at 00:26:46-00:32:05 Tehran
(2026-11-16, 20:56:46-21:02:05 UTC), lasting about 319 seconds. All thirty-two
registered GETs completed. The audit reconstructed 35 journal records and
10,620,827 logical bytes; the maximum local clock discrepancy was 0.7672 ms.
Code/plan bytes match implementation `3848c93` and registration `56bf5f6`.

Of 267 USDT metadata markets, 208 were eligible. The exclusions were 28 closed
or unknown market states, 16 nonpositive reported quote volumes, nine scaled
assets and six inactive/unknown currencies. The top twenty were fixed before
books; the remaining 188 eligible markets were not substituted later.

| Cost scenario | Valid comparisons | Positive conditional widths | Excluded comparisons |
| --- | ---: | ---: | ---: |
| Zero-cost diagnostic | 187 | 187 | 13 |
| Base assumed | 187 | 18 | 13 |
| Stress assumed | 187 | 13 | 13 |

There are 600 comparisons across costs, 561 valid and 39 excluded for the
minimum/quote-footprint constraint. Cash, received-fee, dust and reserve
identities were checked on every valid row. Quotes and markets are correlated;
these are not 600 independent trading trials or realized returns.

Seven markets have at least five recent distinct positive fingerprints. Sixteen
have ten valid comparisons in every scenario. No market meets all fixed
follow-up conditions. HUMA has nine valid positive stressed widths and nine
recent qualifying fingerprints, but one below-minimum quote prevents its
selection. ONE has four valid positive stressed widths and no qualifying recent
fingerprint. Their median conditional stressed widths are 0.0075458208 and
0.0145044345 USDT, respectively; neither is earned profit or a portfolio return.

The registered screen ends with no candidate. Costs/reserves remain assumptions,
activity lists are finite, and quote/activity windows differ. No parameter,
threshold, symbol or duration was changed to promote a near-pass result.
[Complete market summaries, exclusions and hashes](../research/evidence/maker-activity-v1-summary.json).
