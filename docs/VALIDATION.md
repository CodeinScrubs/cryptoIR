# Initial validation record

Versions: 0.1.0 through 0.7.0. Date: 2026-11-14.

## Evidence

- Windows, Python 3.11 and 3.13: all 23 release tests passed, including invalid-event-shape checks.
- One parallel public BTC/USDT snapshot request per venue: Nobitex, Wallex and Tabdeal returned parseable books accepted by the schema/order checks.
- The resulting three-record local file passed the provenance/ordering audit; two records lacked a source timestamp. Raw observations stay outside version control.
- Offline replay produced six directional conditional spread estimates using explicitly assumed 0.4% fees per leg and a 10 bp reserve on each leg's notional. All six were negative for the tested quantity of 0.0001 BTC. These assumptions are not account-verified; this tiny diagnostic is not a strategy evaluation.
- CI configuration covers Python 3.11 and 3.13 on Windows and Linux. CI results must be checked on the published commit, not inferred from local results.

## Scope of the tests

Decimal/fiat arithmetic, invalid values, malformed/empty/crossed/unordered books, source and receive time checks, depth weighting, insufficient liquidity, fee sensitivity, pair isolation, source errors, hash corruption, truncated records and causal replay ordering.

These checks do not establish market-history completeness, exchange solvency, queue position, account access, fill probability, profitability, a working paper portfolio or unattended trading readiness.

## Version 0.2.0 additions

- Three recording batches produced nine schema-valid spot snapshots, with six unknown source timestamps. This approximately one-minute probe does not establish continuous coverage.
- Public catalogs parsed 535 Nobitex market-precision entries, 469 Wallex entries and 1,027 Tabdeal entries in this observation. These counts are not a claim that every market is active or tradeable by an account.
- The synthetic paper demo produced a partial fill, released the unused reservation and persisted balances and journal records. It is not a market-performance result.
- All 54 local Python 3.13 tests passed, including catalog semantics and 23 paper-ledger scenarios.
- Version 0.1.0 CI passed all four Windows/Linux and Python 3.11/3.13 combinations on commit `3edafb15b0f0b986bcce1592a322223105fce237`.

## Version 0.3.0 additions (2026-11-14)

- All 72 local tests passed. Local WebSocket tests exercised application heartbeat, forced disconnect and resubscription; they do not connect to an exchange.
- A 60-second public BTC/USDT observation recorded 84 Nobitex books, 318 composed Wallex books and 30 Tabdeal books. No reconnect was observed in this short run. All 432 books were independently reconstructed from captured raw frames.
- All 318 Wallex composites retain unknown source timestamps and non-atomic-side status. The run establishes reception, not fill feasibility.
- File SHA-256: `eaff8a9ea35137e704914866355fc1fde8f726a92a8f4989feca2147ccdca8ac`. Uncompressed size: 3,915,945 bytes. Raw market data remains ignored by Git.
- Compressed logs, record/hash corruption, missing terminal records and disregard of fabricated derived-book fields are covered by offline tests.

## Version 0.4.0 additions (2026-11-14)

- All 105 local tests passed. Added exact daily reconciliation, raw-source replay,
  artifact-tampering checks, delayed execution, prior-volume-only sizing, future
  mutation invariance, spot accounting, and sequential research gate checks.
- A 600-second observation finished with 844 Nobitex, 3,291 Wallex and 170 Tabdeal
  reconstructed books. Its gzip SHA-256 is
  `83bf19d4ebacfcc345e554d9c8186ba25a5d327ef9d687df097c1ab6f6af60bf`;
  compressed size is 4,616,674 bytes. No reconnect was observed. This is short-run
  reception evidence, not unattended-operation acceptance.
- Wallex had 27 unusable intermediate side combinations and no source clock.
  Tabdeal had 109 books whose source time was over five seconds old; source age
  ranged from 0.144 to 59.015 seconds (median 13.840). No negative ages occurred.
  A new incoming message is not sufficient evidence of current executable depth.
- For the October/November BTC sample, Nobitex had 43 complete days with exact
  hourly/daily OHLCV equality. Wallex had 44 complete days but one discrepancy:
  2026-11-10 UTC aggregated open differed by +107.58 USDT and high by +107.57;
  low, close and volume matched. That Wallex dataset is not reconciled.
- The longer Nobitex request found a June/July 2025 gap and was rejected before
  performance evaluation. See the [registered amendment](../research/CHANGELOG.md).
- The replacement Nobitex BTC window has 11,664 hourly bars, 486 complete exchange
  days and no gaps. Every OHLCV delta was exactly zero. Raw window edges excluded
  24 hourly bars. The normalized file SHA-256 is
  `bca91a50f83c03d56d23d8adb38966a4d3016c5223754153b83024b20bb01611`.
  The command `history-audit` prints all 486 rows; the complete table and source
  records are preserved locally. Selected execution-table rows follow.

| Day open UTC | Tehran session date | Hours | Open delta | High delta | Low delta | Close delta | Volume delta | Match |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2025-07-02 20:30 | 2025-07-03 | 24 | 0 | 0 | 0 | 0 | 0 | yes |
| 2025-07-03 20:30 | 2025-07-04 | 24 | 0 | 0 | 0 | 0 | 0 | yes |
| 2025-07-04 20:30 | 2025-07-05 | 24 | 0 | 0 | 0 | 0 | 0 | yes |
| 2026-10-28 20:30 | 2026-10-29 | 24 | 0 | 0 | 0 | 0 | 0 | yes |
| 2026-10-29 20:30 | 2026-10-30 | 24 | 0 | 0 | 0 | 0 | 0 | yes |
| 2026-10-30 20:30 | 2026-10-31 | 24 | 0 | 0 | 0 | 0 | 0 | yes |

These are data and software checks. No strategy has been accepted for forward
paper or live trading by this release's validation record.

## Version 0.5.0 additions (2026-11-14)

- All 113 local tests passed, including received-asset fees, dust valuation,
  subsequent entries after dust-only exits, restart with partial base-fee fills,
  fee-mode idempotency conflicts, explicit schema migration, consistent backups,
  refusal to overwrite backups and preserved legacy order identities.
- Version 0.4.0 CI passed all four Windows/Linux and Python 3.11/3.13 jobs on
  commit `8aa9c8557974a65b83af35d0c01159c0e544404a`.
- The revised July 6, 2025 through November 1, 2026 request window produced
  11,568 hourly candles and 482 exactly reconciled complete sessions **for each**
  of BTC, ETH and SOL on Nobitex. No missing sessions or interior hourly gaps
  remained. Raw partial request-edge hours were excluded; no missing values
  were synthesized. Each local `audit-table.txt` contains all daily deltas.

| Pair | Normalized SHA-256 |
| --- | --- |
| BTC/USDT | `cafdb285307878a12193e8135be7f1fa3620edcab019cf0d351bedbad98f3c0a` |
| ETH/USDT | `0271ab4809c3a30214991f146acbcf579889dc47da8fe19ae564911dd03d8c82` |
| SOL/USDT | `735fa75b85abf03c5dfc6ef3bc20ee753af0c09b7c71ce7e5deaaf90c90a5080` |

Initial strategy results and data rejections are recorded in the
[research ledger](../research/RESULTS.md). Code checks, exact daily aggregates
and present-day public fee documentation do not verify historical execution or
historical fees.

## Version 0.6.0 additions (2026-11-14)

- All 131 local tests passed. Eighteen new risk scenarios cover immutable
  limits, capital mismatch, shared pending-order exposure, order/count caps,
  stale/unknown source data, timer expiry, gap cancellation, persistent loss
  latches, reductions during a halt, unknown inventory, depth-aware valuation,
  overnight gaps, atomic rejection and restart.
- The synthetic risk demo bought one gross unit at an invented price of 100,
  received 0.999 units after its hypothetical base fee, then observed an invented
  bid of 70. The daily-loss latch rejected the next entry and survived reopening
  the database. The remaining 0.999 units were still held and reported. This is
  a loss-path accounting test, not market performance.
- Version 0.5.0 and its research-results commit passed CI on Windows/Linux and
  Python 3.11/3.13 (`277998a` and `12c055a`).
- No continuous paper strategy, authenticated request or real trade was run.

## Version 0.7.0 additions (2026-11-14)

- All 152 local tests passed. Forward tests exercise future/partial/revised
  candles, warmup without past trades, decision delay and later-snapshot latency,
  missing hours, processing delay, sealed source/configuration, duplicate-intent
  crash recovery, OS lock recovery after abrupt process exit, STOP behavior,
  localhost transport/REST integration and same-account resume.
- A 120-second public Nobitex forward session received 167 source-timed books
  and two candle responses. The latest 168 completed hours warmed the trend
  signal. Its observed target was flat. No simulated order was generated.
- An unchanged 15-second resume received 20 books and another candle response.
  Both logs replayed cleanly; the account still had exactly its synthetic
  reference 100 USDT, no base holding and no order. Session count increased to
  two. This establishes short public-data orchestration and persistence, not
  filled-order performance or calibrated execution.
- Initial log SHA-256:
  `2a92fb72c0f3c4263db1ae4476bcad1f4fbc3a5f7224f825eae07a3852c22053`.
  Resume log SHA-256:
  `00a40b6ecca714bab756342e7f186c40b71d5074831f86ffbc6a719c567e729a`.
- The user's IRT capital has not been converted or allocated by this runner.
  It is an explicitly synthetic reference experiment, not the accepted owner
  paper account. No authenticated request or real trade was made.

## Version 0.8.0 additions (2026-11-15)

- All 170 local tests passed. New scenarios cover explicit IRR/IRT units,
  finite funding budgets, received-asset fees, depth participation, minimums,
  dust, matched independent holding benchmarks, FX attribution, deterministic
  receipt reconstruction, funded runner integration and backup-first source
  upgrades that preserve an untouched account.
- Public Nobitex metadata and the all-market order-book endpoint supplied
  source-timed BTC/USDT and USDT/IRR books. Native `USDTIRT` prices were retained
  in IRR. The separate short fiat WebSocket probe received acknowledgements
  but no books; that probe does not establish a usable fiat stream.
- An initial funded paper session exposed a log-envelope schema collision in
  portfolio valuation events. Its original log remains preserved and did not
  pass stream audit. The writer now rejects reserved envelope fields; valuation
  payloads are nested. No order was generated in the failed-log session.
- After a consistent backup and explicit source upgrade, a 15-second resume
  received 23 books, one candle response and one matched valuation. The corrected
  log replayed cleanly. Capital was unchanged, no order existed, and the session
  ended by its duration limit. Log SHA-256:
  `eaaf493fe142d5493157b3c7b55e7cb7ce67d6b3eed58ca9ff09feb4a6abda36`.
- The IRT conversion and exit marks are conditional paper calculations, not
  actual exchange fills. Fees remain assumptions, source freshness is bounded,
  and no accepted strategy or profitability evidence is established.

## Version 0.9.0 additions (2026-11-15)

- All 181 local tests passed. Eleven monitoring/HTTP scenarios cover freshness,
  owner/heartbeat disagreement, clock reversal, changed balances, receipt
  mismatch, oversized/corrupt reports, bounded order views, no writes, Host and
  cross-origin checks, path traversal and permitted navigation to the HTML shell.
- Version 0.8.0 passed all four Windows/Linux, Python 3.11/3.13 CI jobs at
  `5a05bf3716bd1ecd86ec392a7460bc029807d06b`.
- The Persian local dashboard was inspected in Chrome through the official
  browser connection. Desktop and a 390px viewport had no page-level horizontal
  overflow. Tables retained semantic captions/headers and keyboard-focusable
  scroll regions; Tab reached the benchmark region. Page refresh pause/resume,
  simulated offline failure, recovery and the final stopped state were observed.
  This is scoped browser QA, not full assistive-technology conformance.
- A 300-second public paper resume recorded 445 books, five accepted candle
  responses and five matched valuations. Its raw stream replay passed, the
  session ended by duration, and no risk halt or simulated order occurred.
  The existing account retained its balances. No account credentials were used.
- Log SHA-256:
  `e1b18fe6d1b72bf5d4f6e12570bd2b97314bd26fd987e21dfbde1d6801c45685`.
  Compressed size: 1,765,584 bytes. Replayed source ages ranged from 0.0359586 to
  0.3691446 seconds, with no future timestamps or ages over five seconds in that
  short sample. This does not establish complete market history or future uptime.
- The dashboard and runner were separate bounded processes. UI availability,
  simulated fault handling and short market reception do not prove profitability,
  venue order acceptance or unattended live readiness.

## Version 0.10.0 additions (2026-11-15)

- The account milestone initially passed 202 local tests, including 21 new scenarios: exact Ed25519
  method/path/body signing, HMAC timestamp units, explicit legacy authentication,
  endpoint and venue restrictions, hidden input, redirect refusal over loopback,
  response limits, redacted failures, unknown fee units, duplicate assets,
  unrounded Decimal totals and private output. CI uses only synthetic credentials.
- The first release CI exposed a Python 3.11 receive/cancel race during the paper
  STOP test; the stuck job was canceled and its log retained. A deterministic
  regression failed on the old transport: a completed receive could swallow
  cancellation and keep capture alive. The transport now uses `asyncio.timeout`
  around direct awaits instead of the affected `wait_for` pattern. See the
  [upstream issue](https://github.com/python/cpython/issues/86296) and
  [Python timeout documentation](https://docs.python.org/3.11/library/asyncio-task.html#timeouts).
  The stop test has a five-second deadline and CI jobs a five-minute cap.
- After the correction, all 203 tests passed locally on both Python 3.11.15 and
  3.13.15, including the new simultaneous receive/cancel regression.
- Read-only account adapters are implemented and locally tested. No actual
  account credential, authenticated exchange response, order or transfer was used.
  Account acceptance and actual fee semantics remain unverified.
- The preceding v0.9.0 release passed all four Windows/Linux, Python 3.11/3.13
  CI jobs at `1446e0657407109b309c7c73840c7bc75b23f8c6`.
- A frozen v0.9.0 runtime completed a two-hour public paper observation on Python
  3.13.15: 10,666 books, 120 accepted candle responses and 119 matched valuations.
  There were no simulated orders or risk halts. The raw stream replay passed,
  with a clean duration-limited end. No capital reset or account-key use occurred.
- Log SHA-256:
  `0bff051ba9fed72ba08597b975d3bb0200319a670a5ee7a85c7e94b9227114a2`.
  Compressed size: 42,095,472 bytes. Replayed source ages ranged from 0.0260917 to
  4.8225379 seconds, with no future source timestamps or ages over five seconds.
  The largest receive interval between books was 10.0083364 seconds. This is
  observed reception, not proof that no source events were missed.
- An earlier launch used an unintended interpreter and was forcibly interrupted.
  Its recording is preserved and is not counted as a clean session. The owner
  lock was verified released before the corrected launch. The two-hour account
  and frozen source remain preserved; the runner stopped at its duration limit.
- No strategy has passed the research gates; no-profitability and no-live-readiness
  boundaries remain unchanged. Longer observation with no orders cannot calibrate
  fill quality or establish profitable trading.

## Version 0.11.0 additions (2026-11-15)

- All 220 local tests passed. Seventeen new product/calculation tests cover
  native symbol resolution, product separation, ambiguous markets, absent rules,
  precision versus increments, strict payload/clock/hash checks, malformed books,
  exclusive artifacts, truncated recordings, missing requested books, partial
  failures, fixed calculation routes, fee currencies, quoted term mismatches,
  HTTP validation errors and offline reconstruction after tampering.
- Public Nobitex margin, Wallex margin and Tabdeal FAPI catalogs returned 131,
  36 and 96 markets. The observed Nobitex maximum was 5 for all returned markets;
  Wallex's maximum native credit ratio was 10. Tabdeal's catalog supplied no
  maximum leverage or maintenance-margin schedule. These are public observations,
  not account verification or evidence of 20x/100x availability.
- Tabdeal FAPI depth succeeded with catalog-native `BTC_USDT`, after the
  documentation example `BTCUSDT` returned HTTP 400. The accepted book had
  `E: 0`; source freshness and contract-unit semantics remain unverified.
- Final catalog/book artifact reconstructed all four observations. SHA-256:
  `5aee746a13166f96e4b7cd14f758e6bb9a6a529e8687e4559d4ac3bfa63fbb46`.
  An earlier development artifact without the final completeness envelope is
  retained separately and is not the final-format acceptance artifact.
- Wallex's documented public collateral calculation and two accepted pre-position
  calculations were probed without authentication. Initial dated-price requests
  returned 422; a separately submitted price within the reported range succeeded.
  Long and short responses retained different fee/loan currencies, a 14,400-second
  renewal interval and 1,814,400-second maximum age. No position was created.
- Both accepted quote artifacts replayed from raw responses. Long SHA-256:
  `d4180d46ac2ba8565cbeef8514a1e6a8ea97aeae0d3df17dcd9a65322313354b`;
  short SHA-256:
  `94c47704f06286842896546d4adb43ec63aa46fcb4399d06a324099fcd8e116c`.
  These are hypothetical inputs and current quoted terms, not actual account
  balances, historical cost schedules or realized performance.
- Versland public API discovery remains unresolved. Official-domain research did
  not establish a documented interface; Chrome tab inventory worked but two
  bounded navigations to the linked trading panel timed out. No browser order or
  authenticated action was performed. Lack of verification is not proof of absence.
- Leveraged simulation, account entitlements and profitability remain unproven.
  No paper capital, existing strategy configuration or live permission was changed.

## Version 0.11.1 additions (2026-11-15)

- All 225 local tests passed. Five additional scenarios distinguish exact daily
  agreement from volume availability, reject empty/all-zero evidence and
  zero-volume price ranges, preserve isolated flat zero-volume bars, and prove
  registered research cannot start a trial or inspect the holdout on such data.
- The volume assessment is computed from reconstructed source candles. Existing
  raw files, manifests and normalized-history hashes remain unchanged. Synthetic
  low-level screens retain conservative zero-volume behavior.
- The registered v3 historical attempt completed all three bounded downloads.
  Each contains 21,144 hours across 881 complete exchange days. BTC / ETH / SOL
  had 3 / 12 / 18 days with nonzero OHLC deltas and zero positive-volume hours.
  All three were rejected before strategy performance calculation.
- Two fresh, single-day BTC requests reproduced the original hourly and daily
  candles exactly, including the disagreement. No full retry or automatic
  replacement was performed. The source of the discrepancy remains unresolved.
- All three earlier v2 datasets pass the additional volume assessment and their
  full daily replay checks. No earlier return was recalculated and the original
  validation/2026 holdout remains uninspected. This adds no profitability evidence.
- [Public audit evidence](../research/evidence/baseline-v3-data-audit.json) records
  every failed day, manifest/data hashes, the fresh-request hashes and v2 checks.
  Complete daily tables and original public responses are preserved locally.
