# Exchange capability inventory

Reviewed 2026-11-14. Documentation is evidence of advertised interfaces, not account entitlement, matching-engine performance, solvency or trading success.

Account addendum reviewed 2026-11-15: version 0.10 implements bounded read-only
Nobitex profile/spot wallets, Wallex fee/main-wallet and Tabdeal spot-account
assessment. Authentication and normalization are locally tested. Version 0.13
has **account acceptance for Nobitex profile/balance and Wallex fee/balance reads**;
Tabdeal's signed read remains partial because duplicate asset balances are
ambiguous. Automatic reuse of saved Windows credentials was verified for all
three. See [ACCOUNTS.md](ACCOUNTS.md) for official sources, hidden
local key entry, exact endpoints and unresolved fee/balance semantics. The table
below describes public-market integration, not authenticated account evidence.

Version 0.11 adds [leveraged-product observations](LEVERAGE.md): public Nobitex
and Wallex margin catalogs, Tabdeal FAPI catalog and scoped depth, plus Wallex
public pre-position calculations. Observed metadata and quoted terms are distinct
from account eligibility, a calibrated liquidation model and order acceptance.

| Venue | Official sources | Observed documentation | This version |
| --- | --- | --- | --- |
| Nobitex | [API](https://apidocs.nobitex.ir/), [getting started](https://apidocs.nobitex.ir/getting-started) | Spot, margin and WebSocket sections | Public catalog, REST/WS BTC books, and audited BTC/ETH/SOL hourly windows |
| Wallex | [API](https://developers.wallex.ir/docs/), [depth](https://developers.wallex.ir/docs/basic-services-depth) | Spot, margin and WebSocket sections | Public catalog and REST/WS BTC books; WS sides are asynchronous; one historical daily discrepancy found |
| Tabdeal | [API](https://docs.tabdeal.org/) | Spot, margin, professional leverage API (FAPI) and streams | Public catalog and REST/WS BTC books; source-age failures observed in the ten-minute sample |
| Versland | [official website](https://versland.io/) | Website advertises leveraged products | Public documented trading API not established; no adapter |

REST books: Nobitex uses `apiv2.nobitex.ir/v3/orderbook/{symbol}`; Wallex uses `api.wallex.ir/v1/depth`; Tabdeal uses `api1.tabdeal.org/r/api/v1/depth`. Bootstrap uses public GET requests only.

Nobitex supplies an update timestamp in the inspected response. The inspected Wallex and Tabdeal REST book payloads do not establish a source timestamp, so it remains unknown. Stream semantics and observations are documented in [STREAMS.md](STREAMS.md) and [VALIDATION.md](VALIDATION.md). Wallex documentation discourages repeated REST polling for depth; continuous collection uses its documented streams.

The public names `IRT`, `TMN`, `RLS`, and contract multipliers cannot be assumed interchangeable. The initial network adapters use USDT spot pairs plus a scoped Nobitex USDT/IRR book mapping: native `USDTIRT` prices remain in rial, and reported toman is explicitly divided by ten. See [unit and funding evidence](VALUATION.md). Other fiat network mappings and historical fiat candles are still disabled pending verification.

Account API rights, fee tiers, minimums, leverage availability, collateral rules and liquidation behavior need independent verification before execution. A website's advertised maximum leverage does not prove it is available through an API or to a particular account.

Version 0.2.0 also reads public catalogs from [Nobitex options](https://apidocs.nobitex.ir/options/get-system-options), [Wallex markets](https://developers.wallex.ir/docs/basic-services-markets) and [Tabdeal exchangeInfo](https://docs.tabdeal.org/). Unknown rules remain unknown. Wallex's observed `otc_min_notional` is not used as a spot minimum; Nobitex scaled symbols are not collapsed into ordinary coin symbols.

Browser observations are for inspection and integration diagnosis; the production order path is intended to use stable, documented APIs. A separate browser/controller's availability does not prove API reachability, and API reachability does not prove logged-in browser access.

## Fee observation, 2026-11-14

The [official Nobitex pricing page](https://nobitex.ir/pricing/) lists base USDT
market maker/taker rates of 0.10%/0.13%, with tiers based on 30-day volume. It
describes charging fees in the asset received. Version 0.5 supports that buy-fee
mechanism as well as explicit quote-fee scenarios. These published current rates
do not establish a user's tier or the rates used in historical periods. The
spot backtester does not model margin renewal charges or profit-sharing fees.
