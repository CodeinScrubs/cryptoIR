# cryptoIR

پایهٔ پژوهش و ساخت ربات معامله‌گر برای صرافی‌های ایرانی؛ شروع با دادهٔ عمومی، سپس بک‌تست و paper trading.

**وضعیت فعلی: نسخهٔ 0.1.0، زیرساخت اولیهٔ داده و برآورد هزینه. ربات سودآور یا آمادهٔ معاملهٔ واقعی نیست.**

This repository currently contains a dependency-free Python foundation for collecting public spot order-book snapshots from Nobitex, Wallex and Tabdeal, auditing their provenance, and replaying conditional cross-exchange spread estimates.

It has **no authenticated API client, live order submission, withdrawal, transfer, paper portfolio or complete backtest engine**. An estimate is not an executable opportunity or realized profit.

## Run

Python 3.11 or later; run from the repository root. With an installed Python, replace `uv run python` with `python`. No runtime third-party packages are required.

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m cryptoir record --pair BTC/USDT --samples 3 --interval 30 --output data/session.jsonl
uv run python -m cryptoir audit data/session.jsonl
uv run python -m cryptoir estimate data/session.jsonl --quantity 0.0001 --fee 0.004 --reserve-bps 10
```

The fee and reserve in this example are **illustrative assumptions**, not verified account fees. The estimate assumes quote-currency fees and prepositioned inventory at both venues. It does not check minimum order size, asset identity across exchanges, available balances, rounding rules, transfer costs, queue position, cancellation or actual fills. Every result has `executable: false`.

Use a new output filename for each recording. Existing files are never overwritten. Sampling is bounded to 120 batches, uses at least 30 seconds between batches and stops on a source error. This is a diagnostic REST collector, not a continuous live-market service. Do not run overlapping collectors against the same venue.

The bootstrap collector accepts explicit `BASE/USDT` spot pairs; only BTC/USDT has been smoke-tested against all three venues. Full market discovery, fiat adapters, contract multipliers and other instruments remain planned. This is an implementation boundary, not a restriction on the eventual research universe.

## Current checks

- Decimal arithmetic, explicit IRR/IRT conversion utility, depth-weighted cost calculations.
- Reject empty/crossed/unordered books, duplicated price levels, non-finite values and invalid timestamps.
- Preserve public raw responses, SHA-256 hashes, request duration, receive time and available exchange time.
- Audit corruption and per-stream ordering; replay respects global receive order and invalidates a venue after an error.
- Missing source timestamps stay unknown. REST gaps, latency, fees and inventory prevent these snapshots from proving execution feasibility.
- Local tests and CI on Windows/Linux; no exchange keys or network requests in CI.

## Roadmap and evidence

See [project scope and architecture](docs/PROJECT.md), [exchange capability inventory](docs/EXCHANGES.md) and [initial validation record](docs/VALIDATION.md).

The intended progression is collection -> faithful replay -> out-of-sample strategy evaluation -> forward paper trading -> separately authorized, limited live evaluation. No stage establishes guaranteed future profitability. Public documentation must distinguish planned, implemented, tested and account-verified capabilities.

## Private information

Keep credentials, account exports, personal risk settings and recordings outside version control (`.local/`, `data/` and `.env*` are ignored). Do not paste keys into issues, chats or notebooks. Ignoring files is only one precaution: inspect every staged diff before publication. The current code needs no credentials.
