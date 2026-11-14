# Experimental forward paper sessions (0.7.0 onward)

The runner connects completed hourly observations, the same baseline `Signal`
class used in screening, public Nobitex depth, the durable paper ledger and the
risk guard. It submits **local simulated orders only**. There is no authenticated
client or real order route. No candidate has passed the registered research gates.

## Run and inspect

```powershell
uv run python -m cryptoir paper-run --config examples/paper-reference.json --directory data/paper-reference --seconds 3600 --max-mb 64
uv run python -m cryptoir paper-status data/paper-reference
uv run python -m cryptoir paper-stop data/paper-reference
```

The example uses **synthetic 100 USDT reference capital**. It is not a conversion
of personal IRT capital and is not a production risk configuration. Its current
fee, market increments, minimums and liquidity participation remain explicit
research assumptions, not account-verified historical terms. Keep personal
settings in an ignored local configuration, never in a public example.

Version 0.8 additionally supports a generated IRT-funded paper configuration and
matched whole-account benchmarks. Use the separate [funding workflow](VALUATION.md)
to derive and seal capital from recorded public depth; do not manually change
the reference capital and label it a verified conversion.

The duration is bounded to 5..86,400 seconds; the uncompressed event budget is
1..512 MiB. The recorder compresses logs and preserves every run. It stops when
the cap or free-disk reserve is reached; it never deletes old observations.
There is no installed background service, task scheduler, auto-launch or
automatic restart. A laptop sleep or program exit interrupts observation.

`paper-status` reads the last atomic status file and probes the OS owner lock.
It reports heartbeat age separately. A stale status or a PID alone does not
establish that a process is alive. The lock is enforced by the OS and releases
on process exit even when Python cleanup does not execute. Its leftover filename
does not prevent a legitimate resume. One experiment has one writer.

## Decisions and fills

- Public hourly history is polled once per minute, with a two-minute allowance
  after nominal close. An initial bounded 240-hour request warms up the signal;
  subsequent requests overlap two hours. Open candles are discarded. Gaps,
  insufficient warmup or stale candle coverage block new decisions.
- Recent candles are provisional forward observations. They are not labeled
  independently daily-reconciled history. A changed stored closed candle in the
  overlapping response latches a stop. Revisions outside the fetched overlap
  are not detected by this loop; independent subsequent dataset QA remains
  necessary.
- Historical warmup updates the signal only. It creates no historical trades.
  A fresh target's due time begins at the actual processing time, at least one
  full hour later. Catch-up after downtime generates at most a current decision,
  never trades at missed historical prices.
- At dispatch, size uses the latest known closed-hour volume, current cash,
  current book, minimum/step constraints and the risk guard's entry budget.
  This book-based execution model intentionally differs from the preliminary
  candle-open screen; it still requires prospective calibration.
- Orders use a finite marketable limit, explicit latency and a short TTL.
  Matching occurs on a later source-timed snapshot, with configured depth
  participation. Processing delay counts against freshness and TTL; a queued
  old book cannot become fresh merely because it is processed now.
- A durable decision ID ties the signal to its order. A crash after reservation
  and before decision acknowledgement cannot create a second reservation.

## Restart, interruption and limits

The engine preserves balances, holdings, signal history, risk high-water marks
and latches. Restart cancels outstanding local orders and delayed decisions,
then waits for fresh candle/book observations. It neither refills capital nor
simulates fills during downtime. Stored signal state preserves stateful rules
such as breakout exits; it is not reconstructed as a new flat strategy.

Configuration and executable-source fingerprints are sealed. A different source
or configuration is refused on resume. Old experiments and their cash/inventory
remain preserved; starting another reference experiment is not evidence that
the old portfolio was closed. `paper-upgrade-empty` permits an explicit source
upgrade only for an unchanged account with no orders, no unexpected inventory
and no risk halt. It first makes a consistent SQLite backup and journals the
identity change without resetting capital, signal state or history. Accounts
that have traded require a separately reviewed migration; they cannot use this
shortcut.

`paper-stop` creates a persistent `STOP` file. On the next timer tick the runner
latches a manual stop, cancels local pending orders and preserves positions.
The marker is never removed automatically. Removing the marker alone does not
clear the durable risk latch. A normal duration expiry cancels pending orders
but does not add a manual-loss latch, so the unchanged experiment can resume.

Transport reconnects are bounded. A strategy/accounting consumer exception is
terminal and cannot be mistaken for a network retry. Clock discontinuities stop
the session. Final reports distinguish stopped sessions, data failures and live
balances; stopping is never reported as liquidation or successful performance.

The current runner covers one Nobitex USDT spot pair per experiment. The funded
BTC mode adds conditional IRT conversion/attribution and full-capital BTC/USDT
holding benchmarks. A [read-only local dashboard](DASHBOARD.md) is implemented
from 0.9.0. Combined multi-venue portfolios, leveraged accounting and external
alert channels remain unimplemented. Reports, candles,
original stream frames and journals stay local in the
experiment directory. Short reception/restart tests establish engineering
behavior only, not positive out-of-sample paper returns.
