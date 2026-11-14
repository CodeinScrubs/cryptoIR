# Public live data recording

Version 0.3.0 records public WebSocket books from Nobitex, Wallex and Tabdeal.
It does not authenticate, trade or recover missing historical messages.

```powershell
uv run python -m cryptoir stream --pair BTC/USDT --seconds 600 --max-mb 64 --output data/session.jsonl.gz
uv run python -m cryptoir stream-audit data/session.jsonl.gz
```

Use a new filename. The default duration is 60 seconds; the explicit maximum is
24 hours. Limits apply to logical uncompressed bytes, including raw and derived
events. A `.gz` extension enables compression. A minimum 512 MiB of free disk is
retained. Resource limits end the run and never remove existing data.

Each connection subscribes to one requested USDT pair. Every disconnect creates
an invalidation boundary. Reconnection uses bounded backoff and fresh state; five
consecutive failures stop a venue. The run reports venues that produced no book.
Cancellation closes sockets. No daemon or task scheduler is installed.

## Venue-specific semantics

- [Nobitex](https://apidocs.nobitex.ir/websocket/websocket-orderbook-channel): full books without delta negotiation; millisecond source time and observed publication offsets. Subscription position seeds offset checks. Offset jumps are recorded; reconnection starts a new segment and does not claim recovery.
- [Wallex](https://developers.wallex.ir/docs/socket-intro): Centrifugo v2 publishes separate full sides without depth-history recovery or publication sequence. Production data observed on 2026-11-14 wraps the documented side list in `data.message`; both shapes are handled. Two sides are combined only with at most two seconds of receive-time skew and an uncrossed book. This is **not an atomic exchange snapshot**; source time stays unknown. Paper fills remain blocked by the existing strict source-time policy.
- [Tabdeal](https://docs.tabdeal.org/): the public spot depth channel publishes every two seconds. Its event timestamp is retained; no exchange sequence guarantee is inferred. These periodic snapshots do not include every matching-engine event.

The WebSocket transport uses the pinned `websockets` package. JSON monetary
numbers are parsed as Decimal. Centrifugo application-level ping/pong is handled
in addition to transport ping/pong. Unknown channels, deltas, malformed books and
regressed offsets invalidate a stream instead of generating favorable assumptions.

## Evidence and auditing

The log has a schema version, run ID, monotonic record sequence, wall and
monotonic timestamps, raw text and SHA-256 hashes, connection/gap events and a
terminal reason. A large local clock change invalidates affected frames. Audit
replays original frames, ignoring stored derived-book claims; it reports source
time quality, counts, missing venues and maximum intervals. Missing end records
remain interrupted runs. Initial connections are replay invalidation boundaries,
not evidence of a missing publication inside an established connection.

A clean recording means the file completed its requested bounded observation.
It does not prove complete history, executable liquidity, account access or
profitability. Low-activity markets can legitimately emit no update for a while;
frame heartbeats establish connection liveness, not current executable prices.
