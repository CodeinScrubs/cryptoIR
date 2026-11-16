# Registered research changes

Plans are immutable after performance evaluation. Data failures and rejected
trials remain in the record. New plans cannot make previously viewed outcomes
unseen, even if a new filename is used.

## 2026-11-15: external Toman reporting-reference collection

Outcome: all nine requests completed, yielding 3,720 hours and 155 daily sessions.
Five dates failed exact OHLCV reconciliation, so the reference was rejected before
any IRT return calculation. No date trimming, source patching, tolerance or retry
followed. [All failures](evidence/fx-history-v1-summary.json) are retained.

`fx-history-v1.json` freezes a bounded Wallex USDT/IRT download for 2025-07-30
through 2026-01-01 exclusive: eight hourly requests and one daily request, with
no automatic retry. Its current public catalog explicitly identifies USDTTMN's
quote as Toman; the raw catalog and unit mapping are hash-pinned. This mapping
is confined to historical reporting and does not enable fiat execution adapters.

Two bounded Nobitex USDTIRT history requests (minute/hourly) returned HTTP 403.
The official public price page also showed a temporarily unavailable chart;
neither observation establishes permanent API absence or historical price units.
The Wallex one-day availability probe returned 24 hourly bars, but no full-window
FX return or IRT portfolio return has been computed. Exact daily reconciliation
must pass before registering an attribution evaluation. Cross-venue basis,
conversion liquidity and fees remain separate unknowns; no prior gate changes.

## 2026-11-15: shared-cash relative-momentum rotation registration

Outcome after registration `1e35e7c` and implementation `39317f2`: base/stress
returns were -4.9882%/-5.7324%, with 13.0482%/13.4428% maximum hourly-close
drawdown and six completed exits. Both BTC benchmarks were beaten, but the
candidate failed profit, drawdown and exit-count gates. Evaluation stopped at
discovery; no 2026 performance opened and no parameters changed. [Results](RESULTS.md).

`rotation-v1.json` freezes one new portfolio hypothesis: every seven days choose
the highest positive fourteen-day return among BTC, ETH and SOL, otherwise hold
USDT. Decisions use completed hourly closes; exits must realize cash before a
separately delayed entry. The total entry allocation remains 25% of synthetic
100-USDT capital. Existing base/stress costs, ten completed exits and 10% drawdown
gates remain; both matched-allocation and full-capital BTC holds are benchmarks.

This uses the existing pinned v2 histories and v4 stage boundaries. Discovery
prices and prior single-asset results have been observed; relative-rotation
performance has not. It is exploratory, with one fixed lookback and schedule and
no parameter search. No 2026 performance opens unless the preceding stage passes.
Current per-asset increments and an explicitly hypothetical 5-USDT minimum are
fixed before calculation. No live order, wallet allocation or download is needed.

## 2026-11-15: deterministic broader triangular universe, exploratory reuse

Outcome after registration in `b3dd298`: 220 assets had valid comparisons in both
directions; 28 had missing book/depth coverage. Each scenario had 8,800 valid
comparisons. Four were positive without costs; none were positive in base or
stress. No route met the frozen follow-up rule. [All results](RESULTS.md) retain
the incomplete coverage and no parameters changed after this observation.

`triangular-v2-universe.json` expands membership using the same pinned catalog:
every active, scale-one native asset with both USDT and IRT markets, excluding
fiat/USDT cycle identities. This selects 248 assets and 497 books, with nine
scaled and ten inactive assets excluded. It includes venue-specific wrapped or
tokenized assets; membership is not underlying ownership or account eligibility.
No price, return or liquidity ranking influences universe selection.

The already recorded v1 all-market responses contain these books. This plan pins
both that recording and its original capture-plan hash before evaluating the
additional assets. BTC/ETH/SOL outcomes are already known; the other 245 cycle
outcomes have not been computed at registration. This reuse is explicitly
retrospective exploration, never new prospective validation. Capital, depth
participation, costs, freshness and order minimums remain unchanged.

Every direction is retained, including exclusions and missing coverage. Only
routes with at least ten valid comparisons and at least three positive estimates
in both base and stress can qualify for a separately registered fresh observation.
At most five qualify for follow-up priority, ranked by stress positive count,
then base positive count, then asset/route lexically. This is a resource limit,
not a statistical or profitability gate. No threshold or cost relaxation follows
inspection. No download, real order or paper-capital mutation is required.

## 2026-11-15: single-venue triangular feasibility registration

Outcome after the registered capture: all twenty samples completed. All six
routes had twenty valid comparisons in each of the three scenarios, with zero
positive estimates even without fees/reserves. No changed window, parameter,
cost threshold or retry followed; [results and evidence](RESULTS.md) are retained.

`triangular-v1.json` freezes both USDT -> asset -> IRR -> USDT and reverse
cycles for BTC, ETH and SOL at Nobitex. One availability-only public setup probe
established the seven book schemas and source timestamps; no cycle return was
computed. Current public metadata is pinned by hash, including distinct rial
and USDT minimum notionals. Native IRT suffixes retain rial book prices.

The plan fixes twenty all-market samples separated by at least thirty seconds,
a synthetic 10-USDT starting budget per comparison, ten-percent displayed-depth
participation, and a five-second source/receive freshness budget. It includes a
zero-cost diagnostic, public base taker fees and doubled fees with additional
received-asset reserves. Intermediate dust is preserved but valued at zero.
Starting-USDT residue remains cash. No personal balance is used.

This is a different economic hypothesis from rejected long-only hourly signals
and cross-venue spreads. It avoids external transfer assumptions but still needs
three asynchronous executions. No paper/live order or profitability acceptance
is inferred from a snapshot cycle. Failed/incomplete capture remains evidence;
there is no automatic retry, window search or threshold revision.

## 2026-11-15: v4 fixed post-gap discovery extension

Outcome after registration: all nine candidates had negative base and stress
discovery returns and were rejected. November-December 2025 is now observed for
all three assets. No 2026 strategy or benchmark return was calculated; no window
or gate revision followed. The results and artifact hashes are in
[the research ledger](RESULTS.md).

`baseline-v4-post-gap.json` reuses the three v2 datasets after a fresh raw-response
replay: each has 482 complete daily matches, 11,568 hourly bars, no interior gap
and usable reported volume under the existing guard. Both manifest and normalized
hashes are frozen in the plan; replacement data must fail before any trial.
No new download or performance calculation precedes this registration.

The prior discovery interval was too short for the BTC trend and breakout
hypotheses to meet the unchanged ten-exit gate. The earlier-calendar v3 attempt
failed source acceptance. This plan fixes discovery at August-December 2025,
validation at January-April 2026 and a gated holdout at May-October 2026.
All three candidates and assets, signal parameters, allocation, fees, delay,
quantity assumptions and gates remain unchanged. This is one calendar extension,
not a search across windows. Rejection must stop advancement, without another
date revision or a reduced exit threshold under this plan.

The v2 August-October results already informed this decision; discovery is
exploratory. Its former November-December validation is now discovery, and its
January-April holdout becomes validation if opened. Those old roles cannot be
claimed afterward. May-October remains unopened for any asset rejected at an
earlier gate. Reusing observations is not independent replication, and even a
screen pass would need execution calibration and prospective evidence.

## 2026-11-15: prospective BTC cross-venue feasibility diagnostic

`arbitrage-v1.json` freezes a single 900-second public observation, three cost
scenarios, gross buy size, independent synthetic capacity ceilings and observed
market increments before collecting or evaluating the new recording. The older
ten-minute comparison is already known and is not reused as independent evidence.

The new diagnostic accounts for buy fees in received base and rounds down the
sell quantity without consuming aggregate base inventory. Residual base is
reported, not credited as quote profit. Missing source timestamps or minimum
notional blocks the strict comparison. Unknown account fees, fee assets and
other filters remain explicit; Wallex/Tabdeal rates are hypotheses. Zero-cost
results are an optimistic diagnostic, never a deployment case. Comparisons
reuse capacity ceilings and cannot be summed into a portfolio return.

There is no profitability-acceptance gate here. Positive estimates under base
and stressed costs would justify testing asynchronous legs and actual inventory
accounting. A failed or partial recording remains evidence; no automatic retry
or favorable window search is permitted by this plan.

## 2026-11-15: v3 source rejection and volume acceptance guard

The registered v3 download completed for all three assets but failed daily
OHLC reconciliation. Every retained hourly candle also reported zero volume.
A two-request diagnostic reproduced one BTC conflict exactly; no full retry,
replacement, date trimming or gate relaxation followed. No strategy return was
computed. See [the results ledger](RESULTS.md) and its linked raw-derived audit.

Version 0.11.1 rejects registered research before any trial when reported volume
is entirely zero or a zero-volume candle has a price range. Daily reconciliation
still reports exact equality separately. This is a source acceptance correction,
not strategy tuning. The prior v2 datasets pass the additional guard; their
returns were not recalculated. No new strategy or window is registered here.

## 2026-11-14: baseline-v1 blocked before performance evaluation

`baseline-v1.json` was published in commit `6509250` before strategy evaluation.
The requested Nobitex BTC/USDT history (2025-01-01 through 2026-11-01 UTC) failed
reconciliation. Thirteen daily records were missing from 2025-06-17 20:30 UTC
through 2025-06-29 20:30 UTC, and the following session had only seven hourly
bars. The normalized stream had a 329-hour gap. No strategy performance was
evaluated, and no held-out performance was inspected. The cause of the missing
exchange records has not been established by this dataset.

`baseline-v1b.json` changes only the data start to 2025-07-02 and discovery start
to 2025-08-01. It retains the same candidate strategies, costs, gates, validation
and held-out intervals. This is a conditional study of the continuous period
after the gap. It excludes a material outage and cannot support whole-history
or outage-resilience claims. Do not fill the missing prices or call that history
complete. This revision is registered before evaluating strategy returns.

## 2026-11-14: baseline-v1b observations and fee-model correction

BTC completed the discovery screen; all three candidates failed the original
gate. No validation or held-out returns were calculated. ETH and SOL failed
data acceptance before strategy evaluation: ETH's first session had ten hours;
SOL lacked two initial daily records and its first present session had six
hours. Their remaining daily aggregates matched. These incomplete records were
not filled in or excused.

`baseline-v2.json` starts the download on July 6, after those known partial
sessions. Discovery and later evaluation dates, candidate signal parameters,
allocation, volume cap and advancement gates stay fixed. Discovery BTC outcomes
are now known; this revision's discovery is exploratory, not an unseen test.
Validation and held-out strategy performance remain uninspected at registration.

The [official current fee page](https://nobitex.ir/pricing/) checked on
2026-11-14 specifies a base USDT-market taker fee of 0.13% and a fee in the
received asset. The new scenario uses that rate (stress: double), and the model
deducts buy fees in base units, retains unsellable dust and values it at zero for
estimated liquidation. This is a **current-cost counterfactual**, not a verified
historical fee schedule or account tier. Earlier 0.4%/0.8% quote-fee results stay
valid only under their stated hypothetical assumptions; they do not estimate
this current schedule. No signal parameter is changed to improve those results.

## 2026-11-15: baseline-v3 pre-gap robustness extension

All nine baseline-v2 discovery candidates were rejected. BTC trend and breakout
had positive stressed returns but only eight and four completed sells; the
ten-exit threshold is retained. The new `baseline-v3-pre-gap.json` fixes a longer
calendar window before any return calculation: download 2023-01-01 through
2025-06-01, discovery 2023-02 through 2023-12, validation 2024-01 through 2024-06,
and a separately gated historical holdout 2024-07 through 2025-05. Daily session
boundary alignment remains unchanged.

The same three assets, signals, costs, delay, capital assumptions, allocation
and advancement gates are retained. No parameter sweep, relaxed gate, automatic
gap trimming or performance-dependent date search is allowed. At most one full
bounded download per asset is attempted; failed coverage blocks that asset's
performance evaluation and is retained as evidence.

The end date precedes the already documented June 2025 source gap. Excluding that
outage is an explicit limitation, not evidence of outage resilience. Later 2025
discovery outcomes have already influenced the decision to extend this study,
so even a pass is retrospective robustness evidence, **not independent temporal
confirmation**. None of this new plan's strategy returns has been evaluated at
registration. The original baseline-v2 validation and 2026 holdout are not opened
by this plan. Fresh independent validation and prospective evidence remain needed.

## 2026-11-16: fixed daily relative-reversal counter-hypothesis

`rotation-v2-reversal.json` registers one long-only daily selector before its
performance is calculated. It chooses the lowest negative 24-hour return among
BTC, ETH and SOL, otherwise cash. Decisions occur every 24 completed hours from
the first evaluation close; execution still waits at least one full hour and
switches require cash from a separately delayed exit. No other signal/filter is
added. The prior weekly-momentum plan remains unchanged.

The three existing hash-pinned datasets, synthetic capital, total entry cap,
base/stress costs, allocation-matched/full BTC comparisons, split dates and all
advancement gates remain unchanged. This is a further exploratory trial on the
already seen August-December 2025 discovery window. It does not produce new
independent evidence by renaming old data. Failure stops before 2026 performance;
no automatic parameter grid or new data requests are authorized by this plan.

The [daily-return literature](https://www.sciencedirect.com/science/article/pii/S1057521921002349)
motivates a test, not an expected return. A
[primary working paper](https://wp.ffu.vse.cz/pdfs/wps/2023/01/03.pdf) specifically
warns that size and liquidity matter and reports momentum for large/liquid coins.
The available three-asset survivor universe is a limitation; this is a cheap
counter-hypothesis screen, not replication of a published long-short portfolio.

## 2026-11-16: register maker-width feasibility before calculation

`maker-spread-v1.json` fixes one new strategy-family diagnostic after daily
reversal failed. It reuses the exact twenty-sample all-market recording and the
248 active, unscaled, dual-quote metadata universe from triangular-v2, considering
only USDT books. No maker-width result was calculated before this registration.
The recording is previously observed exploratory data, not fresh validation.

Hypothetical bid/ask round trips retain native ticks/minimums, received-asset
fees, quantity rounding and dust. Zero, base and stress scenarios are fixed.
Displayed depth limits quote footprint only; it cannot establish that either
passive order fills. Current public maker fees come from the official Nobitex
pricing page; adverse-selection reserves are explicit assumptions.

The preregistered follow-up rule selects at most three markets only after
adequate fresh-source observations and positive cost-bearing width. It permits
a separately registered public book/trade observation, never paper acceptance
or live orders. All exclusions and nonpositive results must remain visible.
There is no parameter grid, new network request, cumulative profit claim or
threshold relaxation in this screen.

## 2026-11-16: register fresh maker book/trade activity observation

`maker-observation-v1.json` freezes RDNT, G and SKL from the pinned maker-width
selection before requesting fresh selected-market data. One current public
options read must confirm active unscaled markets and unchanged native rules.
One 900-second public WebSocket connection then subscribes to both book and
trade channels for each asset. Raw frames and clocks are preserved, with a
64-MiB logical cap, 512-MiB disk reserve and no reconnection/retry.

Source freshness, detected sequence/clock breaks, subscription acceptance,
positive-volume trade publications and fresh quote-width sensitivity determine
only eligibility for another separately registered measurement. Public trade
types are not guessed aggressor flags; no stable trade IDs or unique-trade
counts are assumed. Prior-book price comparisons do not simulate our fills.
Quiet markets stay quiet in the report; no asset replacement or time extension
is allowed after results. This is not paper strategy performance.


## 2026-11-16: maker activity observation completed without qualification

The registered 900-second run ended normally. All six subscriptions were
accepted; G/SKL yielded 90/59 fresh book publications, RDNT yielded none, and
no selected trade publication arrived. Raw audit and source-commit verification
passed. All valid stressed widths remain positive, but no asset satisfies the
unchanged trade-activity gate. No retry, longer window, substitution or private
operation occurred. Silence remains a bounded observation; a separately
registered reference-market trade-feed control is still needed before claiming
that lack of observed trades describes venue activity rather than route limits.
