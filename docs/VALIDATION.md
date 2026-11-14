# Initial validation record

Versions: 0.1.0 through 0.3.0. Date: 2026-11-14.

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
