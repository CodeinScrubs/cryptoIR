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
