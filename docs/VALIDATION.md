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

## Version 0.12.0 additions (2026-11-15)

- All 248 local tests passed. Eighteen added arbitrage scenarios cover received-base and
  quote buy fees, residual inventory and rounding, depth-weighted costs, both-leg
  reserves, independent cash/base ceilings, known minimums, unknown/stale/future
  timestamps, invalid costs, causal replay, invalidation, corrupt source frames,
  pinned catalog/rule mismatch, exclusive output, absent routes and truncated observations.
- Registered rule values were independently reconstructed from the current
  public catalog bodies. Catalog SHA-256:
  `7af65da7fd08e350b3cb8b8fc6a3541433248b109fcce8229a42198392252ef4`.
  Wallex's spot minimum remains unknown; an OTC minimum is not substituted.
- The new command is a conditional capacity diagnostic. It does not simulate
  orders, sum overlapping estimates, mutate balances or establish profitability.
  See [its assumptions and boundaries](ARBITRAGE.md).
- Five additional account scenarios cover bounded value-free schema diagnostics,
  redaction of arbitrary JSON keys, mixed Wallex fee-result entries, incomplete
  fee rows, invalid ratios and duplicate native markets. Fixtures are synthetic;
  CI contains no private responses, credentials, account rates or balances.
- A local interactive account check initially accepted Wallex balances but
  rejected the fee schema. Redacted structural evidence identified additional
  result entries without fee fields. After the compatibility correction, both
  reads were accepted with the current parser hash. Charged fee assets, spendable
  balance semantics, key permissions and order acceptance remain unverified.
- The registered 900-second public recording completed and reconstructed 6,008
  books across all three venues. SHA-256:
  `1dd3cc737fce1105c5f43a45017879702c9dec809871e78b952c5c16a32cf040`.
  Compressed size: 7,088,063 bytes. All 3,426 strict directional comparisons were
  nonpositive under the registered base/stress costs; 139 were positive only in
  the zero-cost diagnostic. Wallex routes were excluded for unknown source time.
  Full counts, stale-book exclusions and estimates are in the
  [public result](../research/evidence/arbitrage-v1-summary.json). No real or paper
  order, account allocation or transfer was performed.

## Version 0.13.0 additions (2026-11-15)

- All 266 local tests passed. Four additional account scenarios retain unique
  Tabdeal rows while quarantining identical, conflicting and case-variant
  duplicates, validate every amount, preserve exact sums and return a partial
  assessment with a nonzero exit status. Duplicate funds are never chosen,
  deduplicated, added together or silently interpreted as zero.
- Fourteen credential scenarios cover native vault serialization, checkout and
  venue isolation, malformed payloads, platform limits, temporary native buffer
  clearing, exact-target removal, hidden entry, explicit persistence, automatic
  reuse, saved authentication schemes and redacted failures. Existing account
  unit tests explicitly block access to the developer's real vault. CI uses only
  synthetic fixtures and local loopback sockets.
- A separate native Windows probe wrote a unique synthetic credential, read it
  in a second process and removed only that probe's entry. No actual account
  credentials were used in the probe.
- Following explicit local entry, all three venue credentials were stored in
  Windows Credential Manager. Fresh normal CLI processes retrieved them without
  prompts: Nobitex profile/balance and Wallex fee/balance reads were accepted;
  Tabdeal returned `partial / ambiguous_duplicate_balances`. Secret values were
  not printed or written into project files. [Storage details](CREDENTIALS.md).
- Tabdeal's duplicate observations remain unresolved even when the numbers
  match. Its read is not marked fully accepted, and none of these account
  observations allocates funds or establishes order permissions, a complete
  cost model or profitability. Live trading remains disabled.

## Version 0.14.0 additions (2026-11-15)

- All 268 local tests passed. New research tests reject absent, malformed,
  duplicate, incomplete and out-of-universe pins; even otherwise accepted data
  with a changed manifest or normalized hash cannot start a trial or inspect
  later-stage performance. The existing sequential-gate test also verifies a
  matching pinned dataset advances to discovery and stops there on rejection.
- The fixed v4 plan and input-hash enforcement were published in `86de86d`
  before performance calculation. Each of three preserved v2 datasets replayed
  482 complete days with exactly zero OHLCV deltas, 11,568 hours, no interior gap
  and accepted reported-volume quality. Complete date-by-date audit tables were
  regenerated locally without downloading or overwriting any source data.
- Three runs completed and all nine candidates were rejected at discovery for
  negative returns under both base and stressed costs. Thirty artifacts include
  benchmarks and cost scenarios. Their hashes, plan hash and every Python source
  hash were checked against the registered code. [Public evidence](../research/evidence/baseline-v4-summary.json).
- No validation or held-out performance was calculated. November-December 2025
  was consumed as discovery; no 2026 strategy or benchmark return was inspected.
  No parameters, gates, account capital, live permission or existing paper
  configuration changed after observing these outcomes.

## Version 0.14.1 additions (2026-11-15)

- All 272 local tests passed. Four additional scenarios cover the funding-wallet
  array response only on its documented route, distinct wallet scope, exact
  amounts, duplicate quarantine, malformed/negative values and an independent
  read failure preserving an accepted account response. Signing tests verify
  the exact fixed GET path and timestamp query for both Tabdeal reads.
- One bounded account assessment reused saved Windows credentials and requested
  the documented account and funding-wallet endpoints. The former remained
  `partial / ambiguous_duplicate_balances`; the latter was accepted without
  duplicate rows. Neither raw response, secret nor actual balance is published.
- These are separate observations. No implicit wallet equivalence, duplicate
  resolution, balance allocation or trading permission was inferred. The normal
  Tabdeal account check now retains both results and still exits nonzero for an
  incomplete assessment. [Official routes and boundaries](ACCOUNTS.md).

## Version 0.15.0 additions (2026-11-15)

- All 289 local tests passed. Seventeen triangular scenarios cover explicit
  rial mappings, both cycle directions, multi-level depth, displayed participation,
  fee/reserve propagation, retained starting cash and intermediate dust,
  minimums, insufficient exit depth, stale/future/unknown source time, wrong
  venue/units/ticks, pinned catalog rules, bounded plans, corrupt raw/payload
  evidence, duplicate JSON, capture spacing, missing books, incomplete footer,
  absent routes, exclusive outputs and stopping after a source error.
- Plan `triangular-v1.json` was registered in `68b6b1c`. Implementation commit
  `d6de259` passed Windows/Linux CI on Python 3.11 and 3.13 before capture ended.
  A preceding availability-only public probe returned the seven required books
  with known source timestamps; it was excluded from prospective evaluation.
- The registered catalog hash is
  `875757f2572d8bbce15df846cf01110480175a96370c21b79b0f548973ba033e`.
  Native scale, currency flags, quantity increments, price ticks and both quote
  minimums were reconstructed from its original public response. This establishes
  the recorded metadata, not market/account order acceptance.
- The implementation retains exact decimal units across three conversions,
  charges each received-asset fee and reserve before sizing the next leg,
  preserves unspent inputs and gives intermediate dust zero assumed USDT value.
  Source freshness and shared HTTP reception do not establish atomic execution.
  It submits no orders, changes no portfolio and does not add estimates into P&L.
- The new capture ended at twenty samples after 587.858 seconds. Compressed
  recording SHA-256:
  `660caff10fa050e15299ff5d89dd2e0fb07904090ac951556678ebc1aba4846a`.
  Sizes: 5,565,941 compressed and 18,879,255 logical bytes. All 140 books passed
  raw reconstruction, source/receive freshness and native price-tick checks.
- Both cycle directions for every registered asset had twenty valid comparisons
  under zero/base/stress costs: 120 per scenario, all nonpositive. No cycle was
  rejected for missing or stale data in this observation. Capture and evaluation
  source hashes both matched `d6de259`; plan, catalog, recording and full report
  hashes are retained in the [public evidence](../research/evidence/triangular-v1-summary.json).
- No performance acceptance, real order or paper-ledger mutation occurred.
  The observation neither establishes profitability nor rules out other periods.

## Version 0.16.0 additions (2026-11-15)

- All 295 local tests passed. Six additional scenarios cover deterministic
  metadata membership and exclusions, complete-catalog enforcement, single-letter
  and digit-prefixed native symbols without global aliases, pinned exploratory
  reuse of both recording and capture plan, rejection of fresh capture from a
  replay-only plan, and invalid completion flags after corrupt trailing evidence.
- Registration and evaluation code `b3dd298` passed all four Windows/Linux,
  Python 3.11/3.13 CI jobs in run `43742029110`. The broader plan was published
  before its first performance calculation. No new account or venue call was made.
- Replay verified the unchanged v1 recording, raw bodies, original capture-plan
  hash and pinned catalog. All evaluation source hashes matched `b3dd298`.
  Capture source hashes remain the original `d6de259`; reuse is explicitly labeled
  `exploratory_existing_recording`, not fresh independent evidence.
- All eighteen shared BTC/ETH/SOL route/scenario summaries exactly match v1,
  including detailed leg amounts and residuals. All 1,488 broader summaries
  reconcile to the registered sample counts, including rejected comparisons.
- Of 248 eligible assets, 220 had both routes evaluated at all twenty samples.
  Twenty-seven lacked usable books; RDNT lacked sufficient displayed exit depth.
  The report retains `capture_complete: true`, `universe_coverage_complete: false`
  and `incomplete_observation` rather than presenting partial coverage as complete.
- Each scenario had 8,800 valid comparisons, 1,080 missing-market rejections and
  forty exit-depth rejections. Four zero-cost estimates were positive; none in
  base or stress were positive. No route met the preregistered follow-up rule.
  Counts are dependent snapshots, not transactions or independent statistical trials.
- [Public evidence](../research/evidence/triangular-v2-universe-summary.json)
  includes every route and rejection, immutable input pins, source hashes and the
  full-report hash. No new recording, order, account allocation, paper mutation,
  tuning after outcomes, live activation or profitability acceptance occurred.

## Version 0.17.0 additions (2026-11-15)

- All 313 local tests passed. Eighteen new rotation scenarios cover deterministic
  past-window selection, cash/ties, clock alignment and warmup, shared capital,
  delayed switches, partial exits, previous-volume caps, received-asset fees,
  dust and cost basis, P&L identity, minimums and lot rounding, late price changes,
  future-data invariance, fixed decision cadence, obsolete intent cancellation,
  input pins, data acceptance and sequential discovery/validation/holdout gates.
- Plan `rotation-v1.json` was published in `1e35e7c` before implementation or
  portfolio-performance calculation. Implementation `39317f2` passed all four
  Windows/Linux Python 3.11/3.13 CI jobs in run `43754599269`.
- All three existing histories reconstructed from raw responses: 482 daily
  sessions and 11,568 synchronized hourly candles each, zero OHLCV deltas on every
  date, no gap and accepted reported-volume quality. Full audit JSON and explicit
  date-by-date delta tables remain local, with hashes in public evidence. No new
  market download, source replacement or performance calculation was needed for
  this quality audit, including its 2026 source rows.
- One registered shared-cash candidate was evaluated alongside cash and two BTC
  allocations, under both cost scenarios. All eight trial hashes, the three audit
  hashes, three delta-table hashes and journal hash were verified. Every model
  source hash matches `39317f2`; the plan hash matches the registered plan.
- Modeled trades respected at least one complete hour after decision, quantities
  and cash stayed nonnegative, and realized/unrealized P&L reconciled. These checks
  establish model invariants, not exchange fills or executable liquidity.
- Base/stress returns -4.9882%/-5.7324%, maximum hourly-close drawdowns
  13.0482%/13.4428% and six completed exits failed the unchanged gates. Both BTC
  comparisons were beaten but no candidate advanced. All 2026 performance stayed
  unopened. No tuning, paper mutation, account allocation or live action occurred.
  [Metrics and artifact hashes](../research/evidence/rotation-v1-summary.json).

## Version 0.18.0 additions (2026-11-15)

- All 318 local tests passed. Four new history scenarios cover source-declared
  Toman identity, strict venue/pair scope, raw-price preservation, copied unit
  provenance, absent metadata before any network/output, changed multipliers,
  raw/payload tampering and duplicate metadata. One new research scenario blocks
  fiat or mixed-quote histories from being modeled as USDT strategy capital.
- Collection registration/implementation `6f3d97e` passed all four Windows/Linux
  Python 3.11/3.13 CI jobs in run `43755622467`. The current public Wallex catalog
  explicitly identifies USDTTMN's quote as Toman; it is copied into the dataset,
  hash-sealed and reconstructed during audit. No magnitude-based scaling is used.
- The nine registered requests all returned HTTP 200, with minimum start spacing
  1.100306 seconds. Raw replay retained 3,720 hours and 155 daily sessions, with
  no gaps/missing days and positive reported volume in each hour.
- Exact daily reconciliation failed on five dates; 150 matched with zero deltas.
  All failed dates have 24 hours. Three have price disagreements and all five
  have volume disagreements. Complete per-date delta tables and original raw
  evidence remain local. Relevant audit-module bytes match `6f3d97e`.
- Two separate bounded Nobitex USDTIRT history probes returned HTTP 403; a public
  trade request and the one-day Wallex availability probe succeeded. No permanent
  service-availability conclusion or alternate-host workaround was inferred.
- [Public evidence](../research/evidence/fx-history-v1-summary.json) preserves
  every source attempt, failed date, input/artifact hash and unit interpretation.
  No IRT return, window trimming, delta tolerance, source repair, retry, strategy
  trial, 2026 performance inspection, account action or paper mutation followed.

## Version 0.19.0 additions (2026-11-16)

- All 328 local tests passed. Ten new fee-sensitivity tests cover exact native
  long/short costs, mark-price effects, zero renewals, incremental renewal
  accounting, expiry boundaries, absent/extra/duplicate/inexact marks, ambient
  Decimal precision, tampered quotes/reports, source replacement and exclusive
  writes. Unknown costs, survival and execution cannot become verified through
  this calculation. Existing product and quote tests still pass.
- Quote audit now reads source bytes once and shares its reconstruction routine
  with fee sensitivity, avoiding separate reads for validation and hashing.
  Both calculation and audit are offline; the CLI summary omits scenario amounts.
- A bounded public probe made exactly three requests: one Wallex BTC/USDT book
  and one dry-run for each side. All returned HTTP 200. The price was selected
  once from the observed ask and was not changed/retried. Synthetic collateral
  and ratio are clearly distinguished from owner settings. Two quote artifacts
  and eighteen cost scenarios reconstructed successfully from raw responses.
- [Public evidence](../research/evidence/margin-cost-v1-summary.json) pins the
  source book, quotes, probe assumptions and all scenario artifacts. It records
  native fee currencies and hypothetical valuations, never realized returns.
  Exit fees, other unquoted costs, net P&L and venue settlement conversion remain
  unknown. No historical strategy, account or paper balance was changed.
- Official Nobitex API/pricing descriptions disagree on 30/60-day expiry and
  describe renewal timing differently. This is unresolved specification evidence,
  not permission to select favorable assumptions. Wallex's documented four-hour
  interval does not establish partial-period or boundary-time billing rules.

## Version 0.20.0 additions (2026-11-16)

- All 334 local tests passed. Six new reversal scenarios cover percentage-loss
  ranking and ties, zero/positive-return cash, fixed daily direction/horizons,
  unchanged costs and gates, separately delayed cash-funded switches, fee/P&L
  identities, prior-volume capacity, future-data isolation and failed-discovery
  stopping. The existing eighteen momentum tests continue to pass.
- Registration commit `e8daee0` passed all four CI jobs in run `43785187735`.
  Implementation/evaluation source `81f71f1` also passed all four Windows/Linux
  Python 3.11/3.13 jobs in run `43785524124`. All eight previous momentum
  discovery artifacts reproduced with exact byte equality after the extension.
- Each raw-derived dataset audit passed 482 complete daily comparisons and
  11,568 contiguous hourly bars, with no missing day, nonzero OHLCV delta or
  unusable-volume flag. Complete Tehran-date delta tables remain local. No new
  exchange download or performance outside the registered discovery was needed.
- All eight new trial hashes, source-module hashes, registered plan hash and
  journal hash were checked. The two candidate trials each contain 153 scheduled
  decisions, no trade sooner than its one-hour delay, nonnegative shared cash
  and quantities, and reconciled realized/unrealized P&L.
- Daily reversal failed unchanged gates: base/stress returns -14.7889%/-22.6397%,
  drawdowns 17.7795%/26.4956%, with 74 completed exits each. Base also failed the
  allocation-matched BTC comparison. Only discovery ran; no 2026 strategy or
  benchmark performance, threshold tuning, account action or paper mutation
  occurred. [Evidence](../research/evidence/rotation-v2-reversal-summary.json).

## Version 0.21.0 additions (2026-11-16)

- All 343 local tests passed. Nine new maker-width scenarios cover exact
  received-asset costs, quote/cash conservation, reserves, dust, post-fee minimums,
  footprint limits, Decimal context, positive gross but negative net width,
  unknown/stale/skewed/wrong-venue/tick data, frozen scope/cost ordering,
  registration hashes, raw tampering, sample order/spacing, records after the
  footer, interrupted capture, exclusions and fixed follow-up selection.
- Plan registration is `3e55ae7`; implementation/evaluation source is `f4b28f9`.
  All four Windows/Linux Python 3.11/3.13 CI jobs passed in run `43818616029`.
  The screen made no new exchange request. Every source-module hash matches that
  implementation commit, and the evaluated plan hash matches the registered bytes.
- Across three scenarios there were 14,880 sample comparisons: 12,606 valid and
  2,274 explicitly excluded. Cash/base/fee/dust and cost-reserve identities were
  checked on every valid row. Per-sample and report hashes are retained in the
  [public evidence](../research/evidence/maker-spread-v1-summary.json).
- All twenty source samples were reconstructed. Universe coverage is partial:
  217 markets have some valid comparisons, 181 have all twenty, and 31 have none.
  Each scenario retains 540 missing/invalid-book and 218 insufficient-footprint
  exclusions. Base/stress had 3,026/1,898 positive conditional quote widths,
  respectively; these are not trades or independent observations.
- RDNT, G and SKL meet the unchanged observation-selection rule. Their stressed
  widths are positive in all twenty samples, but quoted footprints differ and
  passive fills remain unknown. No strategy acceptance, portfolio-return claim,
  account access, paper mutation or real order occurred.


## Version 0.22.0 implementation checks (2026-11-16)

- All 355 local tests passed. Twelve new observation tests cover pinned scope,
  current metadata and native rules, one-character catalog symbols, exact trade
  decimals, repeated publication values, raw buy/sell labels, offsets, source
  regressions, duplicate JSON keys, delta rejection and command ordering.
- Freshness is measured at persistence/availability; a delayed receive or a
  future/stale source cannot become fresh through replay. Only causal fresh
  prior books enter descriptive trade-price comparisons. A zero-volume trade
  does not satisfy positive activity, and unique-trade identity remains unknown.
- Raw audit tests cover altered hashes, sequences, commands, clocks, receive
  times, false completion, missing terminal records and source protocol failure.
  Actual loopback WebSocket tests exercise six subscriptions, heartbeat replies,
  quiet channels, early disconnect, protocol rejection and handshake timeout.
  The collector never reconnects. Synthetic fixtures make no exchange requests.
- Registration: `4a2ab36`. Implementation: `1fcf67a`. All four Windows/Linux
  Python 3.11/3.13 jobs passed in CI run `43898787087`. Public observation and
  financial acceptance remain separate from these software checks.
- The fresh public observation completed 900.0869162 seconds with all six
  subscriptions accepted and no detected offset/source-time/clock failure.
  There were 90 G and 59 SKL fresh book publications, all with observed offsets;
  RDNT books and all three trade channels had zero received publications.
  All 233 raw records replayed; captured source bytes match `1fcf67a` exactly.
- There are 438 valid conditional-width comparisons and nine minimum/footprint
  exclusions across three scenarios. None is an executed trade. No asset meets
  the unchanged fresh-trade activity gate. Trade payloads, aggressor semantics,
  unique-trade IDs and matching-engine completeness remain unverified.
  No private account, portfolio, order, withdrawal or transfer changed.


## Version 0.23.0 checks (2026-11-17)

- All 364 local tests passed. Nine new cases cover fixed native reference scope,
  current metadata, no implicit fiat conversion, exact values and duplicate
  occurrences, malformed/hash-mismatched/unsorted/future REST data, guarded
  multiset comparisons, stale/zero/missing evidence, phase order and omissions,
  exclusive outputs, the exact five-GET/one-socket budget, loopback reception
  on both channels, and zero/positive-volume conflicts without repair.
- The original maker observation replay matches every original field except
  the current audit-code hashes after sharing its raw session verifier.
  The trade control's diagnostic replay preserves every original match count,
  status and field except added diagnostic details and current audit hashes.
- Registration `43aba35`, capture `10afc65`, diagnostic replay `3e512b0`.
  Captured and audited source bytes match their respective commits; registered
  plan bytes match the preregistration. Implementation CI `43906524128` passed
  all four Windows/Linux Python 3.11/3.13 jobs; diagnostic CI `43951601842`
  also passed all four jobs after adding the volume-conflict regression case.
- The bounded real control completed 180.0969701 seconds: 13 BTCUSDT and 160
  USDTIRT publications, 172 fresh positive volumes and one zero volume. Native
  values remain unconverted. All 13 guarded BTCUSDT fingerprint occurrences
  match REST; USDTIRT matches 15 of 16 guarded REST occurrences. All 135 other
  unmatched WS occurrences predate the twenty-row post-REST list.
- USDTIRT's remaining REST occurrence has a same-time/price/type WS candidate
  with zero volume versus REST 0.01. Trade identity and root cause remain
  unknown, and no automatic correction, order fill or P&L is inferred.
  Capture completeness is not cross-source agreement or whole-venue coverage.


## Version 0.24.0 implementation checks (2026-11-17)

- All 372 local tests passed. Eight activity-screen cases cover frozen scope
  and quote-cost pins; active/unscaled/open metadata and exact Decimal volume
  ranking; twenty-market cap and exclusions; duplicate, zero, stale and future
  recent rows; raw replay and later activity availability; insufficient valid
  quotes/activity; hash, selection, request-order, spacing, clock and footer
  tampering; interrupted capture; exclusive outputs; and the exact maximum
  thirty-two-GET request/sleep budget with no source-error retry.
- Registration `56bf5f6`; implementation `3848c93`. All four Windows/Linux
  Python 3.11/3.13 jobs passed in CI run `43954978305`. No exchange request or
  credential is used in CI. The screen reuses the existing tested quote-width
  accounting and preserves its pinned base/stress assumptions.
- All thirty-two registered public GETs completed. Raw replay verified
  deterministic top-twenty selection, 10 books, 20 recent lists, 35 journal
  records, sequence, response hashes, monotonic spacing and terminal count.
  Both captured/audited source hashes match `3848c93`; plan bytes match `56bf5f6`.
- All 561 valid quote rows passed exact cash/base/fee/dust/reserve identities.
  There are 39 minimum/footprint exclusions across costs. Per scenario: 187
  valid comparisons; zero/base/stress positives are 187/18/13. Seven markets
  meet the fixed activity threshold and sixteen have ten valid quotes per cost,
  but no market meets every follow-up condition. No threshold was relaxed.
- HUMA's nine positive valid widths and one failed footprint remain nine,
  rather than being rounded up to the required ten. Recent duplicate/zero
  rows are not invented unique trades or fills. No account, portfolio, order,
  withdrawal or transfer changed. Complete capture is not strategy acceptance.


## Version 0.25.0 paper foundation (2026-11-23)

- All 381 local tests passed. Nine regression cases cover source ordering
  through restart, venue isolation, equal-timestamp conflicts, exact decimal
  duplicate detection, recovery after
  a gap, feed expiry from exchange time, legacy snapshot cutoff, stale decision
  replay and atomic post-fill rollback.
- The process death test exits the child process after a synthetic fill has
  changed its SQLite transaction but before the post-fill risk update and
  commit. Reopening shows the original pending order, reservation, risk state
  and journal. A subsequent valid replay commits one fill and one journal row.
- Replayed 187 accepted Nobitex book messages and two connection boundaries
  from two existing public-stream recordings into disposable ledgers with
  synthetic cash. The exercise used receive times and generated zero orders.
  The recording SHA-256 values are
  `2a92fb72c0f3c4263db1ae4476bcad1f4fbc3a5f7224f825eae07a3852c22053` and
  `00a40b6ecca714bab756342e7f186c40b71d5074831f86ffbc6a719c567e729a`.
  This checks admission of recorded payloads. Fill and process-death checks
  use synthetic inputs and loopback sockets.


## Version 0.26.0 deterministic paper replay

- All 384 local tests passed. The existing loopback test ran and resumed
  the forward engine, then rebuilt both sessions in order. It also verified
  that a resumed recording alone fails the opening checkpoint.
- The CLI regression runs the recorder and engine on a controlled clock using
  original frame format and a raw hourly candle response. No intent appears
  before its decision delay. A book inside the order latency leaves the
  order pending. A later book settles one fill. Offline replay reproduces
  the final balances, orders and durable ledger digest. A simulated storage
  cap during manual stop commits the stop once and leaves the replay
  checkpoint unavailable.
- Replay refuses changed raw-frame hashes, derived books, early processing
  times, changed candle payloads, incorrect code fingerprints, incorrect
  closing digests and omitted footers. Older tapes lack the required actions.
- One 24-second public Nobitex BTC/USDT smoke session used synthetic capital.
  It accepted one hourly candle response and warmed up 168 bars. The
  capture contained 32 book messages, 60 engine inputs and zero orders.
  The CLI verified the closing ledger digest offline.
  Recording SHA-256: `9f5ffe8ef6895406d73afff51bb7050ab165a7c431351fb08b2a5ff96dede394`.
  This observed reception and verified replay parity. Fill behavior was
  checked with synthetic inputs. Calibration against venue executions remains open.


## Version 0.27.0 Nobitex order reads

- All 390 local tests passed. Six new cases verify exact query signatures,
  GET-only routing and rejection of other venues and mutation routes, partial
  fills, different order states, side and quantity conflicts, bounded pages,
  duplicate IDs, unknown units, missing evidence and redacted output.
- The three bounded BTC/USDT reads authenticated and passed the account-side
  response envelope and page parser via the saved Ed25519 credential. This
  verifies endpoint acceptance. Populated row handling and quantity matching
  were tested with synthetic fixtures. Account data stayed local.


## Version 0.28.0 order recovery rehearsal

- All 398 local tests passed.
- The offline CLI demonstrated an uncertain attempt across reopen, an open
  order, a terminal partial order waiting for fills and resolution after
  exact quantity reconciliation. One attempt was committed.
- Eight new tests cover an actual child-process exit after the synthetic
  venue receipt, two concurrent SQLite writers, partial cancellation, missing
  and stale evidence, bad identity/limit/time/quantity, non-atomic status
  changes, rollback on journal failure and exclusive CLI output.
- The account parsers retain optional reported limit price and creation time
  for identity checks. Their populated-row behavior remains fixture-tested.
  No private account or real order was used in this rehearsal.
