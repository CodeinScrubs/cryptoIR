# Research ledger

These are preliminary conditional screens, not realized or deployable returns.
No personal account balances or capital assumptions are published here.

## triangular-v2: catalog-wide exploration, no cost-bearing positives

Plan registration and evaluation code: `b3dd298`, published before calculation.
The deterministic catalog rule selected 248 active scale-one assets with both
quote markets, excluding nine scaled and ten inactive assets. This reuses the
same twenty v1 all-market samples; BTC/ETH/SOL outcomes were already seen.
It is retrospective exploration, not another prospective observation.

Twenty samples were available, but universe coverage remained incomplete.
Both directions for 220 assets had twenty valid comparisons each. Another
27 assets had no usable pair books, and RDNT failed displayed exit-depth checks
in both directions. These 28 assets remain unevaluated, not negative returns.

| Scenario | Valid comparisons | Positive estimates | Unavailable-book rejections | Exit-depth rejections |
| --- | ---: | ---: | ---: | ---: |
| Zero-cost diagnostic | 8,800 | 4 | 1,080 | 40 |
| Assumed base costs | 8,800 | 0 | 1,080 | 40 |
| Assumed stress costs | 8,800 | 0 | 1,080 | 40 |

The four zero-cost positives were BICO forward (one sample, best 0.0000356 USDT)
and S reverse (three samples, best 0.0151990 USDT), each against the unchanged
synthetic 10-USDT budget. Neither survived costs. The best base-cost estimate
across valid routes was -0.092793668320000 USDT. Separate maxima need not occur
in the same sample. No estimates are added into P&L or treated as independent.

No route met the preregistered condition of at least ten valid comparisons and
three positive estimates in both cost-bearing scenarios. Therefore no route
advances to fresh follow-up under this plan. There was no threshold, cost,
budget, freshness or market-minimum relaxation after inspecting outcomes.
This short observed window cannot exclude other periods or unknown markets.

The [evidence summary](evidence/triangular-v2-universe-summary.json) retains every
route, rejection reason, source hash and input pin, including the hash of the
full local report. Source hashes match `b3dd298`; capture source hashes remain
those of `d6de259`. No new market download, account access, paper order or real
order was needed. Profitability and autonomous execution remain unestablished.

## triangular-v1: three-leg Nobitex cycles, no positive estimates

Registration: `68b6b1c`; capture and evaluation: `d6de259`. Twenty fresh public
all-market responses were recorded from 2026-11-15 16:05:45 through 16:15:33 UTC,
ending at the registered sample limit. All 140 required books reconstructed from
original bodies, met the five-second source/receive-age bound and obeyed recorded
price ticks. The setup availability probe was not included. Full route details,
source hashes and book audit are in [the evidence summary](evidence/triangular-v1-summary.json).

Each independent cycle starts with a synthetic 10 USDT and can consume at most
10% of displayed depth. Fees and reserves are deducted in the received asset at
each leg before the next leg is sized. Unspent starting USDT remains cash;
intermediate crypto/IRR dust is reported with zero assumed liquidation value.
These are conditional estimates, not modeled asynchronous orders or actual fills.

| Asset | Direction | Comparisons per scenario | Best zero-cost estimate, USDT | Best base estimate, USDT | Best stress estimate, USDT |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | USDT -> BTC -> IRR -> USDT | 20 | -0.00777706 | -0.14717706 | -0.22625706 |
| BTC | USDT -> IRR -> BTC -> USDT | 20 | -0.0404966 | -0.19736524576 | -0.28919962776 |
| ETH | USDT -> ETH -> IRR -> USDT | 20 | -0.006018570 | -0.115698570 | -0.234738570 |
| ETH | USDT -> IRR -> ETH -> USDT | 20 | -0.01984 | -0.140140165888 | -0.239723991616 |
| SOL | USDT -> SOL -> IRR -> USDT | 20 | -0.015315580 | -0.124955580 | -0.234035580 |
| SOL | USDT -> IRR -> SOL -> USDT | 20 | -0.0354784 | -0.14317889356 | -0.25441541632 |

There were zero positive estimates in all three scenarios: 120 comparisons per
scenario, with no rejected comparison. Best values in separate columns can refer
to different samples. Reused budgets and overlapping liquidity prevent adding
these estimates into a profit figure or treating them as independent trials.

Base costs use public assumed taker rates of 0.13% for USDT markets and 0.25% for
IRR markets, plus 15 bp of gross received units on every leg. Stress doubles the
fee rates and uses 30 bp reserves. Actual account tiers and execution slippage
remain unverified. No real or paper order, account allocation or transfer occurred.
This short window does not exclude opportunities at other times, sizes, assets
or venues; it supplies no evidence for deploying these cycles.

## baseline-v4: fixed longer discovery, all candidates rejected

Plan and code registration: `86de86d`, published before evaluation. The same
three v2 datasets passed raw-response reconstruction and the new manifest and
normalized-data pins. Each contains 482 exact daily matches, 11,568 hourly bars,
no interior gaps and usable reported volume under the existing acceptance guard.
Full daily delta tables remain local; hashes and all trial metrics are in the
[public audit summary](evidence/baseline-v4-summary.json).

Discovery: 2025-07-31 20:30 UTC through 2025-12-31 20:30 UTC, exclusive.
Signal parameters, synthetic 100-USDT reference cash, 25% entry allocation,
quantity rules, one-hour decision delay, base/stress costs and advancement gates
are unchanged from v2. This extends an already observed discovery interval; it
is exploratory evidence, not an independent replication.

| Asset | Strategy | Base net return | Stress net return | Base max hourly-close drawdown | Completed sells |
| --- | --- | ---: | ---: | ---: | ---: |
| BTC | Hold, 25% entry budget | -6.2867% | -6.3468% | 9.2676% | 0 |
| BTC | 24/168 trend | -4.8490% | -7.2271% | 7.8321% | 17 |
| BTC | 168/72 breakout | -2.4241% | -3.3437% | 5.3054% | 7 |
| BTC | 24-hour mean reversion | -4.1787% | -7.1190% | 5.2405% | 23 |
| ETH | Hold, 25% entry budget | -5.1143% | -5.2257% | 14.2338% | 0 |
| ETH | 24/168 trend | -1.4051% | -3.7068% | 7.4754% | 16 |
| ETH | 168/72 breakout | -5.2854% | -6.3160% | 10.3711% | 8 |
| ETH | 24-hour mean reversion | -7.7067% | -13.3985% | 9.0538% | 46 |
| SOL | Hold, 25% entry budget | -7.0252% | -7.1246% | 17.0577% | 0 |
| SOL | 24/168 trend | -6.5978% | -8.8271% | 11.4088% | 17 |
| SOL | 168/72 breakout | -6.7369% | -7.9997% | 9.4527% | 9 |
| SOL | 24-hour mean reversion | -6.7982% | -14.1231% | 11.9422% | 59 |

USDT cash returned zero. Every candidate had a negative return in both cost
scenarios; some also failed the exit-count, drawdown or asset-hold gate. Returns
are relative to total synthetic starting cash, not only its invested portion.
The extended BTC trend sample now has enough completed exits, but loses under
both cost scenarios. The earlier favorable short-window result did not persist
through the registered extension. This does not establish performance outside
the tested conditional model or reject every possible strategy.

All three experiments stopped at discovery. No January-October 2026 strategy or
benchmark return was calculated. The original v2 November-December validation
has now been consumed as exploratory discovery; it cannot be described as
unseen validation. There is no automatic further date search or relaxed gate.
Neither leverage nor reversing a losing signal is justified by these results.
Historical fees, IRT attribution, intrahour drawdown and execution calibration
remain unresolved; no real or paper order was made by this study.

## arbitrage-v1: prospective BTC capacity diagnostic

Plan registration: `77d5f4b`; evaluation code: `5e9ea7a`. One 900-second public
observation began at 2026-11-15 10:13:11 UTC. It ended by its duration limit and
replayed 1,272 Nobitex, 4,286 Wallex and 450 Tabdeal books. The registered gross
buy size was 0.0001 BTC; capacity ceilings and fees are synthetic assumptions.
See [the full audit and route summary](evidence/arbitrage-v1-summary.json).

| Buy -> sell | Valid comparisons per scenario | Positive zero-cost estimates | Best zero-cost quote surplus | Positive base / stress estimates | Best base estimate | Best stress estimate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Nobitex -> Tabdeal | 1,713 | 64 | +0.003367 USDT | 0 / 0 | -0.094591622990 USDT | -0.12708838598 USDT |
| Tabdeal -> Nobitex | 1,713 | 75 | +0.00472368 USDT | 0 / 0 | -0.088761441500 USDT | -0.116739133000 USDT |

Wallex routes had zero strict comparisons because source timestamps were
unknown. Its spot minimum also remains unknown. These exclusions do not prove
there was no Wallex opportunity. Each accepted pair used known source ages no
greater than five seconds; stale receive/source observations were rejected.
Nobitex had four replayed books older than five seconds at reception. Tabdeal
had no future or over-five-second source time in this recording.

All 3,426 valid directional comparisons were nonpositive under both registered
cost-bearing scenarios. Zero-cost positives are not trades or deployable edge.
Base/stress results round sale quantity after the buy fee and give residual
base zero quote value. This is a conservative cash comparison, not total
portfolio P&L or proof that inventory pooling could never improve results.

The same capacity is reused across comparisons; counts are correlated and
cannot be summed into profit or independent samples. This one short window
does not reject arbitrage across other periods, sizes, markets or inventory
policies. No asynchronous two-leg fill, order, transfer, account allocation or
performance acceptance occurred.

## baseline-v3: longer history rejected before strategy evaluation

Plan `baseline-v3-pre-gap.json` was registered in `72a70b9` before return
calculation. Requested interval: 2023-01-01 through 2025-06-01 exclusive. All
three bounded downloads completed, preserving 21,144 hourly bars across 881
complete exchange days per asset, with no interior gaps or missing daily rows.
Complete coverage did **not** establish data acceptance:

| Asset | Exact daily matches | Days with OHLC disagreement | Positive-volume hours | Zero-volume hours with a price range | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| BTC | 878 / 881 | 3 | 0 | 21,106 | blocked before performance |
| ETH | 869 / 881 | 12 | 0 | 20,889 | blocked before performance |
| SOL | 863 / 881 | 18 | 0 | 20,496 | blocked before performance |

Every hourly candle reported zero volume. Matching zero daily volume cannot
validate the prior-volume capacity proxy used by the screen. Zero is preserved
as the source value; its cause and the actual historical volume are unknown.

A bounded two-request recheck of BTC on 2024-06-12 Tehran time reproduced the
same 24 hourly candles and daily candle exactly, despite the narrower request
window. Hourly aggregate minus daily value remained:

| Exchange day (Tehran) | Hours | Open delta | High delta | Low delta | Close delta | Volume delta | Match |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2024-06-12 | 24 | 0 | +1970.62 | 0 | +682.48 | 0 | false |

Price deltas are USDT per BTC. This reproduces a source disagreement; it does
not establish which resolution is correct. Raw records were not overwritten,
and no discrepancy was tolerated away. All failed dates, hashes and the bounded
recheck are in [the audit evidence](evidence/baseline-v3-data-audit.json).

No discovery, validation or held-out strategy return was computed for v3. The
original v2 validation and 2026 holdout remain uninspected. Rechecking the v2
datasets with the new volume guard found 11,561 / 11,560 / 11,547 positive-volume
hours for BTC / ETH / SOL, respectively, and no zero-volume candle with a price
range. Their earlier conditional rejection results remain unchanged; no return
was recalculated. This does not remove their documented model limitations.

## baseline-v1b: BTC/USDT discovery, Nobitex

Code: `8aa9c8557974a65b83af35d0c01159c0e544404a` (v0.4.0).
Plan: `baseline-v1b.json`, registered in `68e3815`.
Data: normalized SHA-256
`bca91a50f83c03d56d23d8adb38966a4d3016c5223754153b83024b20bb01611`.
Evaluation: 2025-07-31 20:30 UTC through 2025-10-31 20:30 UTC, exclusive.
Reference cash: synthetic 100 USDT; maximum entry allocation: 25%.
Both fees assumed paid in quote currency; base fee 0.4% per leg plus 15 bp
adverse price cost; stress fee 0.8% plus 30 bp. These are hypothetical costs.

| Strategy | Base net return | Stress net return | Base maximum hourly-close drawdown | Completed sells (base) | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| USDT cash | 0.0000% | 0.0000% | 0.0000% | 0 | benchmark |
| BTC holding, 25% entry allocation | -1.7089% | -1.9641% | 4.4683% | 0 | benchmark |
| 24/168 trend | +0.2196% | -1.9502% | 2.6921% | 8 | rejected |
| 168/72 breakout | +0.7247% | -0.3725% | 1.9639% | 4 | rejected |
| 24-hour mean reversion, 2% entry | -3.3957% | -6.4841% | 4.5409% | 12 | rejected |

All candidates failed positive stressed return; trend and breakout also failed
the predeclared minimum of ten completed sells. No candidate advanced, and
validation/held-out strategy performance was not calculated. ETH and SOL failed
data acceptance before performance calculation. See [amendments](CHANGELOG.md).

## Ten-minute conditional spread diagnostic

The recorded BTC/USDT stream with SHA-256
`83bf19d4ebacfcc345e554d9c8186ba25a5d327ef9d687df097c1ab6f6af60bf`
produced 486 directional comparisons with known source timestamps and ages at
most five seconds. At a hypothetical 0.0001 BTC size, 0.4% quote fee per leg and
15 bp reserve per leg, none had positive estimated net proceeds. The best was
-0.074024660 USDT. Other comparisons were excluded for stale/unknown times.

This narrow observation assumes prefunded inventory, equal asset identities and
displayed liquidity. It does not model two-leg failure, filters, transfers or
actual account costs, and does not rule out arbitrage at other times or sizes.

## baseline-v2: current-fee counterfactual, three assets

Code: `277998a` (v0.5.0 implementation). Plan registration: `5c3c1d8`.
Discovery dates and 25% allocation remain identical to v1b. The cost assumptions
are 0.13%/0.26% fees and 15/30 bp adverse price costs for base/stress,
respectively. Buy fees are in received base, sell fees in USDT, and unsellable
dust has zero estimated liquidation value. Historical fee tiers are unknown.

All nine candidates were rejected at discovery. BTC trend and breakout remain
interesting hypotheses, but fail the fixed minimum of ten completed exits.
They have **not** earned deployment or out-of-sample acceptance. No validation or
held-out performance was calculated for any asset. BTC discovery was already
seen under v1b; this is an exploratory fee-model revision, not independent
replication. ETH and SOL were first performance evaluations.

| Asset | Strategy | Base net return | Stress net return | Base max drawdown | Completed sells | Gate failure |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| BTC | Holding, 25% entry budget | -1.6632% | -1.7580% | 4.4780% | 0 | benchmark |
| BTC | 24/168 trend | +1.2188% | +0.1212% | 2.3173% | 8 | insufficient completed exits |
| BTC | 168/72 breakout | +1.1704% | +0.6169% | 1.8065% | 4 | insufficient completed exits |
| BTC | Mean reversion | -1.8972% | -3.4841% | 3.2790% | 12 | negative return |
| ETH | Holding, 25% entry budget | +0.9094% | +0.7640% | 7.6918% | 0 | benchmark |
| ETH | 24/168 trend | +1.3029% | -0.3795% | 5.9330% | 12 | negative stressed return |
| ETH | 168/72 breakout | +0.3712% | -0.3276% | 5.1266% | 5 | stress, exits and benchmark |
| ETH | Mean reversion | -5.2748% | -8.4924% | 6.4820% | 25 | negative return |
| SOL | Holding, 25% entry budget | +1.9397% | +1.7901% | 9.8039% | 0 | benchmark |
| SOL | 24/168 trend | +0.3254% | -1.1376% | 7.0257% | 11 | stress and benchmark |
| SOL | 168/72 breakout | -3.6741% | -4.7273% | 6.5290% | 8 | negative return and exits |
| SOL | Mean reversion | -8.7790% | -12.7259% | 9.9370% | 32 | negative return and stressed drawdown |

USDT cash returned zero in both scenarios. Returns are relative to synthetic
total starting cash, not just the invested portion. Maximum drawdown is measured
at hourly closes and does not bound intrahour loss. These results do not include
IRT attribution, full-capital BTC benchmarks, historical fee tiers or calibrated
execution. More trials on the same discovery interval do not create new evidence.
