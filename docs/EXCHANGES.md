# Exchange capability inventory

Reviewed 2026-11-14. Documentation is evidence of advertised interfaces, not account entitlement, matching-engine performance, solvency or trading success.

| Venue | Official sources | Observed documentation | This version |
| --- | --- | --- | --- |
| Nobitex | [API](https://apidocs.nobitex.ir/), [getting started](https://apidocs.nobitex.ir/getting-started) | Spot, margin and WebSocket sections | Public BTC/USDT REST book request succeeded |
| Wallex | [API](https://developers.wallex.ir/docs/), [depth](https://developers.wallex.ir/docs/basic-services-depth) | Spot, margin and WebSocket sections | Public BTC/USDT REST book request succeeded |
| Tabdeal | [API](https://docs.tabdeal.org/) | Spot, margin, professional leverage API (FAPI) and streams | Public BTC/USDT REST book request succeeded |
| Versland | [official website](https://versland.io/) | Website advertises leveraged products | Public documented trading API not established; no adapter |

REST books: Nobitex uses `apiv2.nobitex.ir/v3/orderbook/{symbol}`; Wallex uses `api.wallex.ir/v1/depth`; Tabdeal uses `api1.tabdeal.org/r/api/v1/depth`. Bootstrap uses public GET requests only.

Nobitex supplies an update timestamp in the inspected response. The inspected Wallex and Tabdeal REST book payloads do not establish a source timestamp, so it remains unknown. Venue-specific stream and metadata research is still needed. Wallex documentation discourages repeated REST polling for depth; continuous collection should use its documented streams.

The public names `IRT`, `TMN`, `RLS`, and contract multipliers cannot be assumed interchangeable. The initial network adapters are deliberately restricted to USDT spot pairs until explicit unit and asset identity mappings have been verified. The standalone IRR/IRT arithmetic utility is tested; a fiat network integration is not yet implemented.

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
