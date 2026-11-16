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


## Reference trade-feed control (0.23.0)

Implemented and locally tested; public results are reported separately. The
[registered control](../research/trade-feed-control-v1.json), committed in
`43aba35`, fixes BTCUSDT and USDTIRT for a single 180-second connection and two
trade subscriptions. One current options read checks active, unscaled native
membership. One recent-trades REST read per symbol before and after capture
provides a bounded reference: five public GETs total, one WebSocket connection,
no retries, recovery, extra symbols or adaptive time extension. Native currency
metadata is not order-placement acceptance.

```powershell
uv run python -m cryptoir trade-feed-control --plan research/trade-feed-control-v1.json --directory data/trade-feed-control-v1
uv run python -m cryptoir trade-feed-audit data/trade-feed-control-v1 --output data/trade-feed-control-v1/report.json
```

Use an exclusive new directory and no overlapping venue collector. The fixed
scope is a diagnostic following silent trade channels in the maker observation;
it does not replace that study's selected assets or relax its activity gate.
Raw REST bodies and WebSocket frames are retained with hashes and clocks. The
shared session audit verifies commands, acknowledgements, source time, offsets,
termination and clock continuity. Errors remain failures, not successful empty
lists. Missing before/after evidence makes the control incomplete.

The official [WebSocket trade contract](https://raw.githubusercontent.com/nobitex/docs-api/master/source/includes/_websocket.md)
and [recent-trades REST contract](https://raw.githubusercontent.com/nobitex/docs-api/master/source/includes/_market_data.md)
were checked on 2026-11-16. Exact decimal time/price/volume/raw-type fingerprints
are compared as multisets only within one native symbol. Repeated identical
values remain separate occurrences; a fingerprint is not an exchange trade ID.
Prices undergo no fiat conversion and buy/sell labels imply no aggressor side.
The comparison uses the acknowledged capture interval minus five seconds at
each edge. It preserves unmatched occurrences and finite recent-list coverage.
A match cannot establish complete history; an unmatched REST occurrence cannot
by itself identify whether exchange, transport, client or semantics caused it.

Fresh positive-volume WebSocket payload observation is reported separately
from complete capture and from REST agreement. No suitable in-window evidence
remains inconclusive; no route, unique-trade identity, liquidity, fills or profit
is invented. No account, order, portfolio, withdrawal or transfer is involved.
