# Local paper monitoring (0.9.0)

Implemented and locally tested: a Persian RTL, read-only dashboard for one paper
experiment. It shows recorded capital, balances and reservations, matched IRT
and USDT benchmarks, paper risk limits, delayed decisions, the latest ten depth
levels per side, and the last fifty simulated orders with their total count.
It does not create orders, start/resume sessions, reset risk or contact exchanges.

After running a paper experiment, open another terminal in the checkout:

```powershell
uv run python -m cryptoir dashboard data/irt-paper --port 8765 --seconds 3600
```

Open `http://127.0.0.1:8765/`. Keep the process running while viewing it. The
duration is bounded to 5..86400 seconds; Ctrl+C stops this dashboard server only.
The paper runner remains a separate bounded process; use its documented
`paper-stop` command when a persistent paper risk halt is intended. Closing a
browser tab or pausing page refresh does not stop the runner or sell holdings.

## What a status means

- **Running** requires both the OS owner lock and a running status heartbeat no
  more than fifteen seconds old. PID values are not used as liveness evidence.
- **Stopped** means the last session ended and no owner holds its lock.
- **Interrupted/unconfirmed** includes a disappeared owner without final status,
  a stalled heartbeat, missing lock evidence or a future heartbeat timestamp.
- A source-timed book must satisfy the configured age limit to be labeled fresh.
  Top ten displayed levels are a view of the runner snapshot, not the complete
  exchange order flow or an execution quote.
- Whole-account valuations expire after five seconds. Marks also require the
  same ledger balances, an active owner and a running heartbeat. Old marks stay
  visible with their observation time and a historical label.
- Browser network failure, paused refresh and elapsed freshness budgets remove
  fresh labels even if a previously downloaded value remains on screen.

The runner writes status approximately every five seconds. The page reads that
local snapshot every two seconds. Fiat valuation is sampled once per minute;
it will usually be historical between samples. This is monitoring of recorded
observations, not a promise of continuous current fiat valuation. Reports from
older versions may lack limits and depth until a new compatible run records them.

Timestamps are displayed in Tehran time with a Persian calendar. Money remains
Decimal in the engine and decimal strings in JSON; percentage scaling happens
on the server. The page rounds only for display. It never sends displayed
numbers into accounting or execution.

## Local access boundary

The server binds only `127.0.0.1`. It validates Host and browser origin metadata,
refuses cross-origin financial JSON, sets no-store/CSP headers and offers only
an explicit asset allowlist and `/api/status`. It provides no filesystem browser,
credential route, POST action, telemetry, external fonts, CDN or hosted service.
Opening the HTML shell by following a link is allowed; it contains no account
data until a same-origin status request succeeds.

This is not authentication against other local processes or users. Do not expose
the server through a public tunnel or reverse proxy. Financial status, receipts
and screenshots stay in ignored local directories. Credentials are not needed.

## Verification and remaining scope

Offline tests cover owner/heartbeat disagreement, stale/future timestamps,
changed balances, receipt mismatch, read limits, order pagination, cross-origin
and Host rejection, path traversal, generic error responses and refusal of writes.
Browser checks cover desktop and a 390px viewport, keyboard-accessible scroll
regions, page-refresh pause/resume, simulated network failure and recovery.
These checks are not a full screen-reader or WCAG conformance audit.

This dashboard monitors the single Nobitex experiment. A unified multi-exchange
market terminal, authenticated account reconciliation, charts over sufficiently
long prospective results, remote notifications and leveraged positions remain
planned. No market-data read or UI check establishes a profitable strategy.
