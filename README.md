# cryptoIR

پایهٔ پژوهش و ساخت ربات معامله‌گر برای صرافی‌های ایرانی؛ شروع با دادهٔ عمومی، سپس بک‌تست و paper trading.

**وضعیت فعلی: نسخهٔ 0.24.0؛ غربال‌گری ۲۰ بازار تتری با توجه به فعالیت و هزینه‌ها کامل شد. از ۱۸۷ مقایسهٔ معتبر، ۱۳ مورد تحت فرض هزینهٔ سخت‌گیرانه مثبت بود، اما هیچ بازار همهٔ معیارهای ازپیش‌ثبت‌شده را نگرفت. HUMA در ۹ نمونه مثبت بود و یک نمونهٔ فاقد حداقل اندازه داشت؛ معیار برای قبول آن تغییر نکرد. هنوز استراتژی پذیرفته‌شده یا سودآوری تأییدشده نداریم و معاملهٔ واقعی غیرفعال است.**

This repository collects public spot order books over REST and WebSocket from Nobitex, Wallex and Tabdeal, audits their provenance, and replays conditional cross-exchange spread estimates.

It also includes public market metadata collection, audited hourly history, preregistered strategy cost screens, bounded experimental forward paper sessions on Nobitex with a durable spot ledger and risk guard, and an interactive read-only account assessment client. It has **no live order submission, withdrawal, transfer, accepted trading strategy or complete backtest engine**. An estimate is not an executable opportunity or realized profit.

## Run

Python 3.11 or later; run from the repository root. `uv run` installs locked dependencies. Without uv, install with `python -m pip install --require-hashes -r requirements.txt`, then replace `uv run python` with `python`. Dependencies are locked: `websockets` for streams and `cryptography` for Nobitex Ed25519 signing. Accounting and HTTP transport use the standard library.

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m cryptoir record --pair BTC/USDT --samples 3 --interval 30 --output data/session.jsonl
uv run python -m cryptoir audit data/session.jsonl
uv run python -m cryptoir estimate data/session.jsonl --quantity 0.0001 --fee 0.004 --reserve-bps 10
uv run python -m cryptoir catalog --output data/catalog.jsonl
uv run python -m cryptoir products --tabdeal-book BTC/USDT --output data/products.jsonl
uv run python -m cryptoir products-audit data/products.jsonl
uv run python -m cryptoir paper-demo --output data/paper-demo.sqlite
uv run python -m cryptoir risk-demo --output data/risk-demo.sqlite
uv run python -m cryptoir stream --seconds 600 --output data/stream.jsonl.gz
uv run python -m cryptoir stream-audit data/stream.jsonl.gz
uv run python -m cryptoir paper-run --config examples/paper-reference.json --directory data/paper-reference --seconds 120
uv run python -m cryptoir paper-status data/paper-reference
uv run python -m cryptoir dashboard data/paper-reference --seconds 3600
```

The fee and reserve in this example are **illustrative assumptions**, not verified account fees. The estimate assumes quote-currency fees and prepositioned inventory at both venues. It does not check minimum order size, asset identity across exchanges, available balances, rounding rules, transfer costs, queue position, cancellation or actual fills. Every result has `executable: false`.

Use a new output filename for each recording. Existing files are never overwritten. Sampling is bounded to 120 batches, uses at least 30 seconds between batches and stops on a source error. This is a diagnostic REST collector, not a continuous live-market service. Do not run overlapping collectors against the same venue.

The bootstrap book collector accepts explicit `BASE/USDT` spot pairs; only BTC/USDT has been smoke-tested against all three venues. Nobitex BTC/ETH/SOL/USDT fiat books have explicit IRR mappings through the all-market endpoint; paper funding and valuation remain scoped to BTC/USDT plus USDT/IRR. Catalog collection preserves broader native market lists. Unified asset identity, other fiat adapters, contract multipliers and other instruments remain planned. This is an implementation boundary, not a restriction on the eventual research universe.

## Current checks

- Decimal arithmetic, explicit IRR/IRT conversion utility, depth-weighted cost calculations.
- Reject empty/crossed/unordered books, duplicated price levels, non-finite values and invalid timestamps.
- Preserve public raw responses, SHA-256 hashes, request duration, receive time and available exchange time.
- Audit corruption and per-stream ordering; replay respects global receive order and invalidates a venue after an error.
- Missing source timestamps stay unknown. REST gaps, latency, fees and inventory prevent these snapshots from proving execution feasibility.
- Local tests and CI on Windows/Linux; no exchange keys or network requests in CI.

## Roadmap and evidence

See [project scope and architecture](docs/PROJECT.md), [exchange capabilities](docs/EXCHANGES.md), [leveraged-product observations](docs/LEVERAGE.md), [read-only account setup](docs/ACCOUNTS.md), [local credential storage](docs/CREDENTIALS.md), [stream semantics](docs/STREAMS.md), [paper model and limits](docs/PAPER.md), [persistent paper risk](docs/RISK.md), [forward operation](docs/FORWARD.md), [IRT capital and benchmarks](docs/VALUATION.md), [local dashboard](docs/DASHBOARD.md), [history and research](docs/RESEARCH.md) and [validation record](docs/VALIDATION.md).

The [registered cross-venue diagnostic](docs/ARBITRAGE.md) also checks received-base
buy fees, independent inventory ceilings and observed minimums. It retains
unknown fields and source-time exclusions. Snapshot estimates cannot be summed
into realized profit and do not model asynchronous two-leg execution.

The [single-venue triangular diagnostic](docs/TRIANGULAR.md) separately replays
both USDT/crypto/IRR directions with three received-asset costs, bounded depth,
native increments and retained residuals. It also remains a snapshot estimate.

The [shared-cash rotation screen](docs/ROTATION.md) tests separately registered
weekly-momentum and daily-reversal portfolios across BTC, ETH and SOL. It requires delayed exits before
new entries, preserves residual holdings and compares the same initial cash
against both matched-allocation and full-capital BTC holding.

The [historical Toman-reference audit](docs/HISTORICAL_FX.md) preserves explicit
Wallex quote-unit metadata and rejects the first FX dataset for five daily
disagreements. Historical IRT portfolio returns remain unavailable.

The [maker quote-width screen](docs/MAKER.md) separates positive quoted spreads
from actual passive fills. It preserves fees, dust, minimums, quote-size limits
and every exclusion; its three selected markets require fresh public observation.

The intended progression is collection -> faithful replay -> out-of-sample strategy evaluation -> forward paper trading -> separately authorized, limited live evaluation. No stage establishes guaranteed future profitability. Public documentation must distinguish planned, implemented, tested and account-verified capabilities.

## Private information

Keep credentials, account exports, personal risk settings and recordings outside version control (`.local/`, `data/` and `.env*` are ignored). Do not paste keys into issues, chats or notebooks. Ignoring files is only one precaution: inspect every staged diff before publication. Public collectors and paper trading need no credentials. The optional `account-check` command reuses this checkout's saved Windows Credential Manager entry, or prompts invisibly if none exists. Only the explicit `--save-credentials` option saves/replaces a key in the native Windows vault; there is no plaintext storage fallback. Normalized private reports under `.local/accounts/` contain no credentials and are separate from the vault. See [setup, replacement and removal](docs/CREDENTIALS.md).
