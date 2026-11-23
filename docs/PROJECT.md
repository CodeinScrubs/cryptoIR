# Scope and acceptance contract

Status: initial design, 2026-11-14. This document describes the destination and gates, not completed capabilities.

## Agreed scope

- Iranian execution venues: Nobitex, Wallex, Tabdeal, and Versland subject to verified integration capabilities. External data may inform research; foreign exchange trading is outside scope.
- Public GitHub source. Personal capital, account details, keys and production settings remain private.
- Start on a Windows laptop, using free tools and data. Paid services and cloud compute are not prerequisites; request compute setup only after measured need.
- Evaluate strategy families incrementally. There is no requirement to enumerate every possible algorithm before producing useful evidence.
- Report net performance in IRT and USDT, and compare with holding USDT and BTC over matching periods and capital flows. Include transaction and operating costs; separate currency appreciation from strategy alpha.
- Forward paper evaluation lasts until sufficient evidence exists; no arbitrary deadline or fixed profit promise.
- High leverage, including 5/10/20/100 scenarios, belongs in research only where venue specifications and data support a faithful simulation. Final live leverage and loss limits are undecided.
- Live trading requires a later explicit user decision after reviewing results and concrete limits. Withdrawals and transfers between exchanges remain manual. Internal spot/margin wallet funding also remains manual until its scope is separately settled.

## Architecture

Begin with one Python application and independently testable modules; avoid services, distributed queues or GPU work until measurements justify them.

1. Capability registry: venue, spot/margin/derivative product, asset identity, quote unit, quantity multiplier, lot/tick/minimum rules, fee currency/tier, rate limits, margin/funding/expiry/liquidation behavior and allowed operations.
2. Ingestion: public REST bootstrap, WebSocket streams, immutable raw events, exchange and receive timestamps, monotonic durations, sequence/reset/gap detection and reconnect/resnapshot state.
3. Normalization and storage: explicit IRR versus IRT; exact decimals or integer units; UTC storage and Tehran display; versioned schemas, checksums and dataset manifests. Start with bounded JSONL; add partitioned columnar storage when needed.
4. Research: causal event replay and fast preliminary screens. Strategy interfaces receive only information available at the simulated decision time. Record every trial, including failures.
5. Simulation: the same strategy and risk interfaces in replay and forward paper mode. Separate cash/inventory by venue, reserve funds for open orders, simulate latency, partial fills, cancel races, conservative queue assumptions and both-leg failure in arbitrage.
6. Execution, later: durable order IDs, intent journal, idempotency, reconciliation against actual exchange state, restart recovery and independent risk checks. Unknown order submission outcome must reconcile before retrying.
7. Operations: local Persian dashboard, feed age, positions, P&L, risk, process health, alerts and audit trail. Notification channels are still to be chosen; local dashboard is the starting assumption.

## Backtest requirements

- Separate screening, training/validation, untouched out-of-sample tests and forward paper observations. Use walk-forward evaluation, purging/embargo when labels overlap, and account for repeated strategy searches.
- Preserve delisted markets and point-in-time symbol membership where available. News, fundamentals, on-chain and macro features use their actual availability timestamps, not later revisions.
- Reconcile historical candles against authoritative venue records when available. Report coverage and gaps explicitly. Never label sampled REST books as complete historical order flow or manufacture order books from candles.
- A limit price being touched does not prove a fill. Avoid assuming both stop and target fills from an ambiguous candle. Mark unavailable queue and intrabar evidence, use conservative scenarios, and calibrate with forward observations.
- Include both trading legs, spread, depth/impact, fees in the correct asset, funding/borrowing/renewal costs, liquidation rules, outages, rejected orders, capital locked at venues and eventual manual inventory rebalancing costs.
- Evaluate volatility regimes and stressed fees, latency, liquidity and outages. Report confidence intervals, maximum drawdown, tail loss, turnover, capacity and capital utilization alongside net returns.
- Freeze code version, data hash, parameters, seed, fee schedule and assumptions for each run. Changing parameters restarts evaluation on genuinely unseen data; the old holdout is no longer untouched.

## Strategy research queue

Order is provisional and determined by feasibility and evidence, not a commitment to deploy each family.

| Family | First question that can eliminate wasted work |
| --- | --- |
| Cross-venue / triangular arbitrage | Does sufficient spread survive realistic costs, inventory constraints and asynchronous execution? |
| Momentum / trend / breakout | Is there stable net out-of-sample behavior across regimes? |
| Mean reversion / relative value | Does the effect survive structural breaks and realistic turnover costs? |
| Market making / order-flow signals | Are timestamped depth, trades and queue assumptions adequate? |
| Carry / funding / basis | Is the relevant product and settlement mechanism actually accessible? |
| Fundamental / news / on-chain | Is free historical point-in-time data reliable and incremental? |
| Statistical / ML combinations | Is there demonstrable improvement over simple baselines after search bias? |

A strategy that finds no qualifying trade must remain idle. Research may conclude that no deployable edge exists under the available capital and costs.

## Release gates

| Gate | Evidence required | Current state |
| --- | --- | --- |
| G0: scope | Capital/risk stored privately; agreed trading permissions and venue scope | Scope agreed; live risk limits intentionally unset |
| G1: data | Verified metadata/units; live streams; reconnect/gap tests; coverage audit | Partial: REST, catalogs, three public WebSocket adapters and exact hourly/daily reconciliation; known historical gaps and venue discrepancies remain |
| G2: simulation | Portfolio accounting; venue-specific rules; causal fills; recovery/invariant tests | Partial: durable spot ledger, fee assets, source-ordered IOC model and atomic fill/risk updates |
| G3: research | Reproducible held-out results after all costs and stress scenarios | Preliminary hourly screening engine and registered experiments; no accepted strategy |
| G4: paper | Prospective net performance and operational reliability; calibrated fill assumptions | Experimental single-venue forward runner implemented; no performance acceptance |
| G5: limited live | User-approved finite limits; account permissions; reconciliation and shutdown exercises | Execution disabled. Nobitex read routes account-verified. Lost acknowledgement recovery locally rehearsed |
| G6: autonomous operation | Sustained observed live results and ongoing risk/operational monitoring | Not established |

Before G5, specify per-trade risk, per-venue and aggregate exposure, daily loss and drawdown limits, leverage, maximum order size and allowed markets. No unlimited spending or automatic capital replenishment. Stops can slip; finite loss limits cannot guarantee a maximum loss under gaps, liquidation or exchange failure.

Disconnects block new exposure. Managing existing exposure needs an explicit policy and exchange-native protection where supported. Stopping a process alone is not a safe position-management policy. Laptop sleep, loss of power/network and local clock changes require recovery tests before unattended operation.

## Next implementation milestone

Version 0.28.0 rehearses a lost acknowledgement with a durable attempt marker,
single outstanding intent and post-restart evidence matching. The synthetic
scenario stays blocked until a terminal observation and quantities agree.
Process termination, competing attempts, stale evidence and conflicting
identifiers are locally tested. Live intent linkage, fill settlement and a
controlled shutdown exercise with approved limits still lack venue evidence.

The Nobitex BTC/USDT `order-check` command provides read-only evidence for G5.
All three filtered reads were accepted by the venue using a saved Ed25519 key.
The quantity and state reconciliation is tested on synthetic populated pages.
The observations cover one page per request and do not represent a complete
or simultaneous account snapshot. Reconciliation of a local production intent,
an accepted strategy, calibrated fills, approved limits and authority to trade
remain open.

The offline forward replay in 0.26.0 exercises the same strategy, risk and
paper settlement code on a complete chain of recorded consumer inputs. Raw
feed evidence and before/after ledger digests are checked. This supplies a
shared path for causal book-and-candle replay and prospective paper execution.
The preexisting hourly screens retain their candle-based cost approximations.
Performance and live execution gates still require separate evidence.

Paper source ordering and settlement are now implemented and locally tested in
0.25.0. The ledger retains one accepted exchange timestamp per market across
restarts. The risk guard commits source admission, settlement and pre/post-fill
risk in a single SQLite transaction. Process-exit recovery is tested against a
real SQLite file with a pending synthetic order. This strengthens recovery
evidence for G2. Venue fill calibration, strategy acceptance, prospective
performance, and live execution remain open.

The registered fresh maker book/trade diagnostic completed its single
900-second connection: all six subscriptions accepted, 90 fresh G and 59 fresh
SKL book publications, no RDNT book publications, and no trade publications for
any selected asset. All observed offsets were contiguous and the raw audit
passed. Positive conditional widths remain, but none met the fixed activity
gate; no candidate progresses from this observation to queue/fill calibration.

The separate 180-second reference control now publicly observes both BTCUSDT
and USDTIRT trade routes: 13 and 160 publications, respectively, with 172 fresh
positive-volume publications and one zero-volume publication. All 13 guarded
BTCUSDT occurrences match REST. USDTIRT retains one guarded REST fingerprint
with no exact match: a same-time/price/type WebSocket candidate has volume zero
versus REST 0.01. Without stable trade IDs this is a candidate volume conflict,
not a proven revision or permission to repair the data. All 135 other unmatched
WebSocket occurrences predate the finite twenty-row post-REST list.

The activity-aware maker screen is now completed: 208 eligible USDT markets
from current metadata/statistics, with the top twenty fixed before new books or
recent trades. Ten all-book samples and twenty recent lists completed within
the registered thirty-two-GET budget. There are 187 valid comparisons per cost,
18 positive base widths and 13 positive stressed widths. Seven markets meet
the recent-activity threshold and sixteen have ten valid quotes per cost, but
none meets every fixed follow-up condition.

HUMA has nine positive stressed widths and nine recent distinct positive
fingerprints; its tenth quote fails the minimum/footprint condition. ONE has
only four valid positive widths and no recent qualifying fingerprint. No
near-pass asset is promoted and no threshold, market or observation window is
changed after results. This closes the fixed screen without a selected maker
candidate. The synthetic adverse-selection reserves are still assumptions,
not measured execution losses; negative results under them are not a proof
that all market making is unprofitable. Any later hypothesis must disclose the
known results and independently justify its scope and calibration evidence.

Reference-route acceptance does not establish matching-engine completeness,
trade identity, native fiat conversion, maker fills or inventory risk. Preserve
zero-volume conflicts and all source/minimum exclusions. No accepted candidate
or paper-performance result currently justifies a live execution route.

The fixed daily relative-reversal counter-hypothesis also failed discovery:
base/stress returns -14.7889%/-22.6397%, maximum hourly-close drawdowns
17.7795%/26.4956%, and 74 completed exits each. The daily switching costs did
not produce an accepted candidate. All old weekly-momentum trial artifacts
retain exact byte parity, and no 2026 performance was opened. Further search
must disclose reuse of the same observed discovery window and avoid an
unbounded horizon/filter sweep. Prospective evidence, historical IRT reporting
and execution calibration remain necessary even if another screen passes.

Offline Wallex fee sensitivity now preserves native fee amounts, explicit
renewal counts and hypothetical currency valuations. Two fresh public quotes
and eighteen scenarios reconstruct from raw evidence. This is conditional cost
accounting, not a leveraged P&L or survival model. Exit costs, settlement
conversion, liquidation and historical terms remain outstanding. Nobitex's
official API and pricing sources disagree on maximum age and describe renewal
timing differently; no one-source rule was promoted into a backtest.

Historical Toman reporting remains blocked on accepted FX evidence. A scoped
Wallex USDT/IRT collector now preserves source-declared currency metadata, but
five of the first 155 daily comparisons disagreed. Nobitex USDTIRT history probes
returned HTTP 403. No historical IRT returns or conversion fills were inferred.
These are evidence gaps in this reporting component, not overall project completion.

Shared-cash multi-asset screening is implemented and locally tested, including
separate delayed exits/entries, residual accounting and full-capital BTC holding
comparison. The registered relative-momentum trial failed profit, drawdown and
exit-count gates at discovery. No 2026 strategy/benchmark performance opened.
Historical IRT attribution, execution calibration and an accepted candidate
remain outstanding; passing software tests does not resolve those requirements.

Catalog-wide triangular exploration is implemented and locally tested. The
registered v2 reuse examined all 248 metadata-eligible assets in the existing
twenty-sample observation, with valid comparisons for 220 and incomplete coverage
for 28. No cost-bearing estimate was positive and no route met the fresh-follow-up
rule. This closes that registered screen without selecting an execution candidate;
it does not rule out other periods. Further execution work needs a justified
hypothesis or independent evidence rather than relaxed screening thresholds.

Extend prospective paper observation and calibrate execution, IRT valuation and matched holding benchmarks. No candidate has passed the registered screening gates; observation remains experimental. A local read-only Persian dashboard and bounded read-only account adapters are implemented and locally tested. Nobitex profile/balance and Wallex fee/balance reads have account acceptance; Tabdeal's signed account read remains partial because duplicate asset balances are ambiguous; its separate funding-wallet list has an accepted read without resolving those duplicates. Opt-in Windows Credential Manager storage and automatic reuse are account-verified for all three venues. Ambiguous fee assets/units and balance semantics remain unresolved. A registered cross-venue snapshot diagnostic now covers fee assets, quantity rounding and independent capacity ceilings; the first bounded BTC observation found no positive base/stress estimate among valid comparisons. A separate registered Nobitex three-leg diagnostic completed twenty fresh all-market samples; none of its six USDT/crypto/IRR cycles had positive zero/base/stress estimates. Asynchronous legs and persistent cross-venue inventory remain to be simulated when a hypothesis justifies that work. Public leveraged catalogs, a separate Tabdeal FAPI book observation and Wallex public pre-position calculations are implemented and publicly probed. Complete side-specific cost, liquidation and expiry rules plus historical inputs are still needed before leveraged simulation. Versland's documented API remains unestablished after bounded discovery; do not guess routes or place browser orders.
