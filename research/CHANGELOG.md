# Registered research changes

Plans are immutable after performance evaluation. Data failures and rejected
trials remain in the record. New plans cannot make previously viewed outcomes
unseen, even if a new filename is used.

## 2026-11-14: baseline-v1 blocked before performance evaluation

`baseline-v1.json` was published in commit `6509250` before strategy evaluation.
The requested Nobitex BTC/USDT history (2025-01-01 through 2026-11-01 UTC) failed
reconciliation. Thirteen daily records were missing from 2025-06-17 20:30 UTC
through 2025-06-29 20:30 UTC, and the following session had only seven hourly
bars. The normalized stream had a 329-hour gap. No strategy performance was
evaluated, and no held-out performance was inspected. The cause of the missing
exchange records has not been established by this dataset.

`baseline-v1b.json` changes only the data start to 2025-07-02 and discovery start
to 2025-08-01. It retains the same candidate strategies, costs, gates, validation
and held-out intervals. This is a conditional study of the continuous period
after the gap. It excludes a material outage and cannot support whole-history
or outage-resilience claims. Do not fill the missing prices or call that history
complete. This revision is registered before evaluating strategy returns.
