# Research ledger

These are preliminary conditional screens, not realized or deployable returns.
No personal account balances or capital assumptions are published here.

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
