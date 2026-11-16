# Leveraged-product feasibility

Version 0.11.0, reviewed 2026-11-15. Public catalog collection, a separate
Tabdeal FAPI book observation and Wallex public margin calculations are
**implemented, locally tested and publicly probed**. No leveraged account,
position, order, transfer, complete liquidation model or profitable strategy
has been verified. Leveraged backtesting and paper execution remain planned.

## Observed interfaces

| Product | Official source | Public observation | Still needed for faithful simulation |
| --- | --- | --- | --- |
| Nobitex margin | [Current API](https://apidocs.nobitex.ir/), margin markets and pools sections | `GET /margin/markets/list`: 131 markets, each reporting maximum 5 in this unauthenticated sample | Account-specific limits; side/tier renewal schedules; pool availability; profit sharing, liquidation and expiry rules; historical changes |
| Wallex margin | [Markets](https://developers.wallex.ir/docs/margin-active-markets), [terms](https://developers.wallex.ir/docs/margin-types) | `GET /margin-trade/v1/public/markets`: 36 markets with native credit-ratio range 1..10, step 0.5 and maximum age 1,814,400 seconds | Full lifecycle cost/liquidation formulas; price trigger source; actual account limits; historical fee and product states |
| Tabdeal professional leverage | [FAPI reference](https://docs.tabdeal.org/) | `GET /r/fapi/v1/exchangeInfo`: 96 symbols; a separate BTC/USDT depth read succeeded | Contract quantity meaning/multiplier, order steps and minimums, leverage caps, maintenance tiers, funding/renewal, mark/index and liquidation rules, historical data |
| Versland | [Official website](https://versland.io/) and its linked trading panel | Website advertises leverage up to 100; documented public API not established by this bounded investigation | Supported API and authentication contract, product terms, public and historical data, account verification |

Counts and limits above are timestamped observations, not permanent product
guarantees or account entitlements. Nobitex explicitly documents that authenticated
limits can differ and some side-specific fees can be missing or null. No supported
20x/100x API scenario has been established by these responses. An advertised
website maximum does not fill that gap. Wallex's `risk_coef` retains its own
meaning; it is not silently treated as universal exposure/equity leverage.

The raw Nobitex pool probe also succeeded. Pool `capacity` and `filledCapacity`
describe participation and are **not** interpreted as immediately borrowable
inventory. That would require separate availability and account evidence.

The public Tabdeal catalog returned `BTC_USDT`, while the documentation example
uses `BTCUSDT`. The example-shaped depth request returned HTTP 400; the catalog's
native symbol returned HTTP 200. Code resolves a requested `BASE/QUOTE` against
the catalog and rejects missing, inactive or ambiguous matches. It never removes
underscores or guesses an endpoint symbol. Reported decimal precision is retained
as precision; it is not converted into an unverified tick/quantity step.

The FAPI book contained `E: 0`. Its source timestamp remains unknown, including
if a future response supplies a nonzero `E` without verified semantics. Receive
time and `lastUpdateId` do not prove freshness or delta continuity. Levels undergo
structural checks, but the returned artifact is explicitly a product book with
`spot_compatible: false`, unknown contract units and `executable: false`.
It cannot enter the existing spot arbitrage estimator or paper runner as a spot
`Book` through this command.

## Collect and reconstruct

```powershell
uv run python -m cryptoir products --tabdeal-book BTC/USDT --output data/products.jsonl
uv run python -m cryptoir products-audit data/products.jsonl
```

The first command makes one unauthenticated catalog GET per supported family
and, if requested, one Tabdeal FAPI depth GET with five levels. It does not start
polling or stream capture. Output creation is exclusive. Use a new filename each
time and avoid overlapping collectors. The catalog has all returned native
markets; the optional book currently selects one pair per bounded run.

Raw responses, hashes, URLs, receive times and normalized observations are
retained. Offline audit reconstructs catalogs and books from those raw responses,
checks provenance and clock consistency, and rejects altered derived fields.
The artifact records its requested pair and a terminal marker; truncated files,
missing requested books and repeated books fail the completeness check.
Source failures remain unavailable; an internally consistent error record is not
a successful market observation. Audit does not establish venue authenticity,
historical completeness, historical symbol membership or execution eligibility.
No account keys are used. No assumed favorable default fills missing rules.

## Wallex public calculations

Wallex documents a [collateral calculator](https://developers.wallex.ir/docs/margin-calculate-loan)
and [pre-position calculation](https://developers.wallex.ir/docs/margin-dry-run).
The fixed public POST endpoints are `/margin-trade/v1/public/loan` and
`/margin-trade/v1/public/dry-run`. These operations calculate terms; they do not
create a position. The client has no position-opening, closing, editing or
transfer route. The CLI uses `dry-run`; the collateral calculator was separately
probed during feasibility research.

```powershell
uv run python -m cryptoir margin-quote --market BTCUSDT --side long --collateral 100 --credit-ratio 5 --open-price 64500 --output data/margin-long.json
uv run python -m cryptoir margin-quote-audit data/margin-long.json
```

These are synthetic example inputs, **not** the owner's capital or approved
settings. Use an appropriate current hypothetical open price when researching;
the API enforces a range around its market price. A 60,000 example received HTTP
422 in this investigation. That response was retained; the client does not
silently change the price or retry. A separate 64,500 example was accepted for
both sides. A future invocation may reject this dated price too.

For synthetic collateral 100 USDT and native ratio 5, the sampled long response
reported a 500 USDT loan, trade fee in BTC, and opening/renewal fees in USDT.
The short response reported loan and opening/renewal fees in BTC, with trade fee
in USDT. The response's renewal interval was 14,400 seconds and maximum age was
1,814,400 seconds. These values are retained with their own currencies; no
conversion, fee aggregation or fixed percentage is inferred. Side-specific
quoted warning and liquidation prices are checked for internal ordering and
retained as observations. They are not guaranteed future liquidation prices.

Quote files contain input amounts and terms and belong in ignored local data,
never in public issues or source. The CLI summary omits amounts. Raw public
validation errors are capped and retained for diagnosis; network failures stay
unavailable and there are no retries. Offline quote audit accepts only successful
quotes whose requested market, side, collateral and ratio match their response.
It detects changes to normalized costs or prices. Exit fees and historical cost
coverage remain unknown; these quotes do not establish a complete net-return
model and are not applied to paper balances or fee settings.

## Offline fee sensitivity (0.19.0, reviewed 2026-11-16)

**Implemented and locally tested**, with two new public dry-run responses and
eighteen reconstructed scenarios. This calculation is separate from the paper
ledger and contains no network calls. It reads one immutable quote artifact,
reconstructs its normalized terms from the raw response, and pins that artifact
and response by SHA-256. A quote is an observation at its recorded receive time;
using it in a scenario does not establish that its terms remain available.

```powershell
uv run python -m cryptoir margin-cost --quote data/margin-long.json --renewals 42 --mark USDT=1 --mark BTC=64500 --output data/margin-long-cost.json
uv run python -m cryptoir margin-cost-audit --quote data/margin-long.json data/margin-long-cost.json
```

The prices are explicit hypothetical valuation inputs, in collateral units per
native unit. Every fee and collateral currency needs a positive exact mark;
the collateral's own mark must equal one. Missing, duplicate, extra, non-finite
or floating-point marks fail. No cross-currency conversion is inferred from a
symbol, and no conversion rate is presented as the venue's settlement rule.

For each quoted fee, the model retains its native currency and amount. It
includes `trade_fee` and `open_fee` once each, and `extend_fee` exactly the
requested number of times. This is an explicit fixed-amount scenario, not proof
of how a live account would be charged. The native totals are then marked at
the supplied scenario rates. Revaluing an entry fee in BTC is an opportunity-cost
valuation; it is not an extra settlement debit. The quoted `trade_fee` does not
establish complete round-trip trading costs. Exit and other unquoted costs,
net P&L, settlement conversion, liquidation and survival remain unknown or
unverified in every report. There is no break-even or profitability claim.

The [Wallex terms](https://developers.wallex.ir/docs/margin-types) describe
four-hour renewals, charging activation/renewal costs on closing, and a maximum
age of 21 days. The calculator supplies interval and age in its response.
The tool deliberately accepts a renewal **count**, not a holding duration:
actual partial-period billing and close-at-boundary ordering are unverified.
`count * quoted_interval` is only nominal elapsed renewal time. A count reaching
or exceeding quoted expiry is rejected; the model does not extend expired
positions or assume they survive an adverse price scenario. Future fee changes
are not inferred from one quote.

The bounded fresh probe used one public BTC/USDT book and two public dry-run
requests, without keys, retries or position creation. Both sides used synthetic
100 USDT collateral, native ratio 5 and the observed ask of 64,935.07. These are
research inputs, not owner settings. For counts 0, 6 and 42, each response was
valued at 0.5, 1 and 1.5 times that price. At count 42 and the unchanged mark,
known fees totaled 4.6900000975 USDT for long and 4.4889612916 USDT for short.
For the short, the same native fees were worth 2.86948064580 to 6.10844193740
USDT across the three marks. None of these numbers is net trading loss or P&L.
[Evidence and hashes](../research/evidence/margin-cost-v1-summary.json).

## Conflicting Nobitex lifecycle descriptions

The official [API source](https://raw.githubusercontent.com/nobitex/docs-api/master/source/includes/_position.md)
describes `Expired` as a maximum of 30 days from the initial order and
`extensionFee` as an end-of-day amount. The current
[pricing page](https://nobitex.ir/pricing/) instead describes renewal every eight
hours for up to 60 days, with the rate at renewal time applied. It also describes
a pool share after a profitable close for each renewed day. These observations
were checked on 2026-11-16. The downloaded API source hash is retained in the
evidence summary. The daily rate label alone does not specify an eight-hour
charge formula or resolve its tiers and account scope.

No 30/60-day expiry, fixed historical renewal rate, inferred rate division or
profit-sharing basis is selected for a simulation. These require clarification
or account-level position evidence before a faithful Nobitex margin replay.
Current spot hourly candles cannot resolve them.

## Next evidence

1. Reconcile account read results after local hidden credential entry. Preserve
   unknown units rather than importing venue lookalikes' assumptions.
2. Resolve expiry and renewal-source differences, exit costs, partial-period
   billing, settlement currency conversion and liquidation/expiry formulas
   against official specifications and observed calculations, including gaps
   and outages. Fixed-quote sensitivity does not close these gaps.
3. Obtain point-in-time underlying/mark/index prices and product rules. Spot
   candles alone cannot validate leveraged liquidation or funding performance.
4. Add leveraged replay/paper only for a product whose necessary terms are known.
   Keep unmodeled products visible in the capability registry.

Versland discovery was bounded to the official site, official-domain searches,
and two Chrome navigation attempts. Chrome tab inventory was accessible; both
panel navigations timed out. No browser order or authenticated action was taken.
This is unresolved integration evidence, not a claim that Versland lacks an API.
