# IRT-funded paper capital and matched benchmarks (0.8.0)

Implemented for **Nobitex BTC/USDT plus USDT/IRR**. This models local paper money,
not an account deposit, exchange order, withdrawal or actual conversion.

```powershell
# Use your chosen local paper amount; the amount below is an example only.
uv run python -m cryptoir paper-fund --template examples/paper-reference.json --initial-irt 1000000 --fx-fee 0.0025 --directory data/irt-paper
uv run python -m cryptoir paper-run --config data/irt-paper/config.json --directory data/irt-paper --seconds 120 --max-mb 32
uv run python -m cryptoir paper-status data/irt-paper
```

The generated configuration and receipt contain capital details. Keep the
directory ignored/private. Funding refuses an existing directory; partial
failure preserves source evidence and never overwrites or refills an account.
Resume the same generated configuration and directory to preserve its capital.

## Source units and observations

Nobitex uses the native market symbol `USDTIRT`, but its book prices are in
**rial**. The canonical data pair is therefore `USDT/IRR`, not `USDT/IRT`.
The adapter retains exact raw rial prices; reports divide them by ten only when
converting an explicit rial value to toman. The official
[WebSocket documentation](https://github.com/nobitex/docs-api/blob/master/source/includes/_websocket.md)
describes the native IRT-suffixed book as rial-denominated, and
[system options](https://apidocs.nobitex.ir/options/get-system-options) identifies
the `rls` currency as Rial. Suffix spelling alone is not the normalization rule.

On 2026-11-14 a direct USDTIRT book request returned HTTP 403, and a short
WebSocket subscription produced no books. The separately documented
[`/v3/orderbook/all`](https://apidocs.nobitex.ir/market_data/دریافت-دفتر-سفارشات-نسخه-۳)
returned both required source-timed books. Funding and matched valuation use
that public endpoint. Unknown, stale, crossed, malformed or insufficient-depth
books prevent a conversion/valuation; market-stat best prices alone are not
substituted for depth.

The same observation's public system options reported USDT minimum notional
`0.5`, rial minimum `500000`, USDTIRT amount increment `0.01`, and BTCUSDT amount
increment `0.000001`. An older official trading documentation example stated
larger minimums. Funding records the actual options response and uses its
current values, while leaving account eligibility and order acceptance unknown.
These values are not hardcoded as permanent venue rules.

## Initial capital receipt

Funding performs these **conditional snapshot calculations**:

1. Convert the supplied IRT budget to IRR exactly, then walk the permitted
   fraction of the USDT asks. Round purchased USDT down to the observed quantity
   increment; retain all unspent IRR. Deduct the explicit buy fee in received
   USDT. The quote budget cannot be exceeded.
2. Preserve that same post-conversion USDT amount for the USDT-holding benchmark.
3. For the BTC-holding benchmark, spend that amount against the simultaneous
   BTC/USDT asks with the same participation/minimum/fee assumptions; preserve
   residual USDT. These are independent counterfactual portfolios, not competing
   claims to the same depth and not real benchmark trades.
4. Keep IRT cash unchanged as a third reference. Seal the receipt's book data,
   source-response hash, rules, fee assumptions and balances. Reconstruct the
   receipt before accepting it; a changed number with a repaired hash must still
   agree with the modeled conversion.

The generated strategy configuration seeds exactly the receipt's net USDT and
derives a finite order bound from that capital and its template exposure limit.
The same fee, lot, minimum and participation assumptions must apply to the
strategy and holding comparisons. Residual IRR stays in the capital receipt,
outside the USDT strategy ledger, and is included in every whole-account mark.

## Marks, returns and attribution

Once a minute, the runner records a new all-market response. It first estimates
BTC liquidation into USDT, then USDT liquidation into IRR, with sell fees and
displayed-depth participation on both legs. Insufficient depth makes valuation
unavailable. Untradeable dust stays owned and reported, with zero assumed
liquidation value. Neither an indicative mark nor a halted process is a sale.

The receipt and every mark use the midpoint **only as a reporting numeraire**:

`initial_USDT_reference = initial_IRR / initial_mid_IRR_per_USDT`

`current_USDT_numeraire = current_IRR_liquidation_mark / current_mid_IRR_per_USDT`

Thus `(1 + IRT return) = FX_mid_ratio × (1 + USDT-numeraire return)`. The report
includes the arithmetic identity error, FX movement, both currency returns and
excess IRT return versus USDT and BTC holding. Entry conversion costs and
estimated final exit costs remain included. A rise in USDT/IRR alone is not
reported as profitable crypto selection.

These whole-account marks are separate from the risk guard's USDT spot equity.
The guard's loss fractions do not cap IRT exchange-rate exposure or conversion
costs. No cross-currency risk hedge or guarantee is implemented.

The report includes the observation time. After five seconds, or after ledger
balances change, `available` becomes false while the last observed valuation
remains visible as historical evidence. `paper-status` also applies that age
check at read time. One-minute polling does not promise a continuously fresh
portfolio mark. The status file is not an executable quote.

Current public fee rates remain assumptions until read-only account verification.
Historical fees, fee rounding, taxes, external operating costs, two-leg execution
latency, outages during conversion and fill probability are not established by
these marks. A funded paper receipt does not establish profitability or approve
live trading.
