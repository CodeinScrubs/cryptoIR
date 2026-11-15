# Historical Toman reference: units and failed data acceptance

Version 0.18.0 adds a scoped **historical** Wallex `USDT/IRT` adapter. Its native
`USDTTMN` mapping requires a preserved public catalog explicitly identifying USDT
as base, Toman as quote and the expected spot flags. Raw prices remain unchanged.
This does not enable general fiat trading or verify historical execution rules.

The official [candle reference](https://developers.wallex.ir/docs/basic-services-candles)
documents `/v1/udf/history`, native symbols, resolution and timestamp bounds.
The [public market catalog](https://api.wallex.ir/hector/web/v1/markets) observed
on 2026-11-15 identifies the quote with `TMN`, `Toman` and `تومان`. Its SHA-256 is
`00b60c28735aa1914fe95af440e03298f730fa4d8489cd257ac7878fed105a62`.
The collector copies its raw bytes into the dataset. Audit reconstructs the unit
identity and checks the manifest's hash and multiplier. Missing, altered or
ambiguous evidence fails; units are never inferred from price magnitude.

Nobitex's native IRT-suffixed **books** have a different known unit convention;
that convention is not assumed for its historical candles. Two bounded USDTIRT
history requests, at minute and hourly resolution, returned HTTP 403. Its official
USDT public price page also displayed a temporarily unavailable chart. These
observations do not prove permanent API absence. No alternate host or guessed
endpoint was used to work around them.

## Registered collection and results

The [collection plan](../research/fx-history-v1.json) was published in `6f3d97e`
before the full-window download. With the preserved source catalog:

```powershell
uv run python -m cryptoir history --venue wallex --pair USDT/IRT --start 2025-07-30 --end 2026-01-01 --unit-catalog data/fx-unit-probe-v018/wallex-catalog.json --output data/fx-history-v1-wallex-usdt
uv run python -m cryptoir history-audit data/fx-history-v1-wallex-usdt
```

Output directories are exclusive. Eight hourly requests and one daily request
all returned HTTP 200; minimum start spacing was 1.100306 seconds. All 3,720 hours
and 155 complete UTC sessions were present, with positive volume in every hour.
Nevertheless, exact hourly/daily reconciliation failed on five dates:

| UTC exchange date | Open delta | High delta | Low delta | Close delta | Volume delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2025-10-03 | 476 | 833 | -362 | 0 | 5287462.93225376 |
| 2025-11-03 | 232 | 3 | 0 | 0 | 2271926.32736756 |
| 2025-12-09 | -1373 | 0 | 0 | 0 | 1084360.35587866 |
| 2025-12-28 | 0 | 0 | 0 | 0 | 131.70000000 |
| 2025-12-29 | 0 | 0 | 0 | 0 | -917.32000000 |

Deltas are hourly aggregate minus daily source. Prices use source-declared Toman;
volume uses reported API units, not independently established executable capacity.
Each failed date has 24 hours; the other 150 dates match exactly. Neither
resolution was selected as the truth or patched to agree.

The result is **rejected at data acceptance**. Raw responses, all date-by-date
deltas and normalized data remain local. [Public evidence](../research/evidence/fx-history-v1-summary.json)
retains all failures and artifact hashes. No retry, interval trimming, tolerance,
interpolation or IRT performance calculation followed.

## Remaining attribution requirements

An accepted reference and previously observed portfolio artifacts must be pinned
before calculating IRT values. Only completed FX candles available at each
portfolio timestamp can be used. Wallex's UTC hourly boundaries and Nobitex's
half-hour offset need an explicit past-only timestamp policy and source-age limit.

An external Wallex reference would provide indicative currency reporting, not the
price of conversion at Nobitex. Venue basis, FX costs, capacity, rounding and
historical rules remain separate requirements for net executable IRT performance.
Existing USDT strategy research rejects fiat histories instead of interpreting
Toman prices as USDT capital. No prior gate, position or live permission changed.
