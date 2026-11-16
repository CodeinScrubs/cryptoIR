# Shared-cash momentum and reversal research

Implemented in 0.17.0. This is a conditional hourly cost screen for one
preregistered spot portfolio hypothesis, not an execution service or accepted
strategy. The [plan](../research/rotation-v1.json) was published before calculation.

The selector ranks BTC, ETH and SOL by the change between completed closes
336 hours apart. At the first evaluation-bar close, then every 168 hours, it
targets the strongest positive return, or USDT cash if none is positive. Exact
ties use ascending asset name. It sees only completed aligned bars. Parameters
are fixed; there is no grid search or optimization after viewing performance.

The [Liu, Tsyvinski and Wu research](https://www.nber.org/system/files/working_papers/w25882/w25882.pdf)
studies momentum as a cross-sectional cryptocurrency risk factor. It motivates
testing a different portfolio hypothesis; it does not establish the chosen
parameters, local exchange costs or future profitability. This three-asset,
long-only experiment is not a replication of that paper's portfolios.

## One capital balance across assets

The model has one shared USDT account and separate owned quantities and cost
bases. It never gives each asset another copy of starting capital. Before buying
a different target, it sells any other tradeable holding. A buy must be separately
decided after the exit has produced cash, then wait at least one complete hour.
There is no same-open atomic sale-and-purchase assumption. A changed scheduled
target cancels an obsolete pending intent before its future execution.

Each hypothetical IOC fill is capped by the previous completed hour's volume,
cash, native quantity increment and a declared minimum notional. Partial remainder
is cancelled; later attempts have their own delay and costs. Exit retries retain
unsold positions. Buy fees are deducted from received base; sell fees are paid
from received USDT. Dust remains owned with its cost basis, counts toward gross
entry exposure, and has zero assumed liquidation value when unsellable. A dust
balance that becomes tradeable again must also be exited before switching assets.

The fixed 25% entry cap applies across all holdings. A tradeable target position
is not topped up, including after a partial entry; weights can drift after entry.
This is not a complete portfolio risk engine. Equity marks include hypothetical
exit costs without fabricating closing sales. Accounting identities reconcile
realized and unrealized P&L to the shared account's change in equity.

## Data, comparison and sequential gates

```powershell
uv run python -m cryptoir rotation-research --plan research/rotation-v1.json --dataset data/baseline-v2-nobitex-btc --dataset data/baseline-v2-nobitex-eth --dataset data/baseline-v2-nobitex-sol --output data/rotation-v1-result
```

Outputs are exclusive. Before any trial, all three dataset manifests and
normalized hashes must match their registered pins, raw responses must reconstruct
the stored data, and exact daily OHLCV reconciliation and reported-volume checks
must pass. Sessions and every hourly epoch must match across assets; the model
does not take an intersection, forward-fill gaps or silently drop an asset.
Per-asset audit reports retain every daily delta and source hash.

Each stage starts from the same synthetic 100 USDT. Cash, matched 25%-allocation
BTC holding and full-capital BTC holding use the same dates, execution delay,
fees and volume-cap proxy. Full BTC has a different exposure level; both BTC
comparisons are explicit. Historical IRT/USDT conversion remains unavailable, so
these returns do not claim whole-account IRT performance or personal funding.

The sole candidate must have positive return, at least ten completed exits and
at most 10% hourly-close drawdown in both base and stress scenarios, and exceed
both BTC benchmark returns in base. Failure ends the experiment before the next
stage's performance is calculated. Each model receives data truncated at its
opened stage's end. Validation and held-out segments reset capital and positions;
their returns cannot be concatenated into a continuous walk-forward portfolio.

August-December 2025 discovery and previous single-asset outcomes were already
observed. This trial is explicitly exploratory. January-April 2026 validation
and May-October held-out performance remain behind sequential gates. Renaming
an experiment never makes viewed data unseen.

The first registered run stopped at discovery: base/stress net returns were
-4.9882%/-5.7324%, hourly-close maximum drawdowns 13.0482%/13.4428%, with six
completed exits each. It beat both declining BTC benchmarks but failed the
profit, drawdown and exit-count gates. No 2026 performance was evaluated and no
rule changed. [All results and hashes](../research/RESULTS.md) retain the rejection.

## Fixed daily reversal extension (0.20.0)

The separately [registered v2 plan](../research/rotation-v2-reversal.json)
selects the lowest strictly negative 24-hour close-to-close return among the
same three assets, otherwise cash. Exact ties still use ascending asset name.
It decides at the first evaluation close and every 24 completed hours after
that. The window is aligned to those completed hourly closes, not relabeled as
midnight daily candles. No shorts, averaging down, off-schedule signal updates
or new execution assumptions were added.

The shared-cash execution, cost scenarios, synthetic capital, allocation cap,
dataset pins, split dates, benchmarks and gates are identical to the original
plan. Buy the loser is a counter-hypothesis, not an assumption that a declining
price must recover. Published findings depend on size and liquidity; see the
sources and limitations in the registered plan. Reusing the already inspected
2025 discovery window adds to the research search count and does not create an
independent confirmation. There is no parameter grid.

```powershell
uv run python -m cryptoir rotation-research --plan research/rotation-v2-reversal.json --dataset data/baseline-v2-nobitex-btc --dataset data/baseline-v2-nobitex-eth --dataset data/baseline-v2-nobitex-sol --output data/rotation-v2-reversal-result
```

All 482 complete days per asset again reconciled exactly, with no gaps and
usable reported volume. The new candidate failed discovery with base/stress
returns -14.7889%/-22.6397% and hourly-close drawdowns 17.7795%/26.4956%.
Each scenario completed 74 exits; base also failed to exceed matched-allocation
BTC holding. No 2026 strategy or benchmark performance opened. Daily targets
were evaluated 153 times; modeled trades retained the required separate delays.
No thresholds, costs or data window were changed after seeing this result.
[Result and evidence hashes](../research/evidence/rotation-v2-reversal-summary.json).

Extending the selector did not change old behavior: all eight original weekly-
momentum discovery artifacts, including benchmarks, reproduced byte for byte.
Synthetic tests additionally check the reversal ranking, exact-zero cash case,
direction/interval validation, delayed exits before entries, prior-volume
capacity, future-data isolation and sequential gates. This is implemented and
locally tested research software, not an accepted paper or live strategy.

## Limits of the model

Fees use a current-cost counterfactual from [Nobitex's public fee page](https://nobitex.ir/pricing/),
not historical tiers or account acceptance. Quantity increments match the preserved
current catalog, while the declared 5-USDT minimum is a restrictive research
assumption. Historical rule changes, volume units and source revisions remain
unresolved. Previous volume is a liquidity proxy, not actual depth; execution
can be overestimated during an outage. Adverse open-price costs are assumptions,
and hourly-close drawdown does not bound intrahour loss. Delistings, survivorship,
tax and external operating costs are not reconstructed. No statistical confidence,
paper acceptance, borrowing, liquidation, account action or live order is implied.
