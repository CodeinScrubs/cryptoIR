# cryptoIR development instructions

- Read `README.md` and `docs/PROJECT.md` before extending the system. Label capabilities as planned, implemented, locally tested or account-verified.
- Current authorization is research and paper development. Live trading is not enabled. User withdrawals and transfers between exchanges are manual.
- Never put credentials, private account responses, personal capital/risk settings or local machine paths in tracked files, logs or public issues. Inspect staged changes before pushing.
- Prefer official venue documentation and bounded public probes. Do not guess trading endpoints, invent exchange capabilities or equate documentation with execution evidence.
- Use exact monetary arithmetic and explicit units. Unknown fees, asset identity, order state or data continuity must not silently become favorable assumptions.
- A backtest must not use data that was unavailable at decision time. Never infer a limit fill merely because a candle touched its price.
- Keep dependencies and infrastructure modest. Implement one testable milestone at a time; do not build exhaustive algorithm catalogs before testing feasibility.
- Run meaningful tests with `uv run python -m unittest discover -s tests -v`. CI uses synthetic fixtures and local loopback sockets, never exchange requests or credentials.
- Preserve untracked recordings and private configuration. Do not delete user data as a cleanup step.
