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

## 2026-11-14: baseline-v1b observations and fee-model correction

BTC completed the discovery screen; all three candidates failed the original
gate. No validation or held-out returns were calculated. ETH and SOL failed
data acceptance before strategy evaluation: ETH's first session had ten hours;
SOL lacked two initial daily records and its first present session had six
hours. Their remaining daily aggregates matched. These incomplete records were
not filled in or excused.

`baseline-v2.json` starts the download on July 6, after those known partial
sessions. Discovery and later evaluation dates, candidate signal parameters,
allocation, volume cap and advancement gates stay fixed. Discovery BTC outcomes
are now known; this revision's discovery is exploratory, not an unseen test.
Validation and held-out strategy performance remain uninspected at registration.

The [official current fee page](https://nobitex.ir/pricing/) checked on
2026-11-14 specifies a base USDT-market taker fee of 0.13% and a fee in the
received asset. The new scenario uses that rate (stress: double), and the model
deducts buy fees in base units, retains unsellable dust and values it at zero for
estimated liquidation. This is a **current-cost counterfactual**, not a verified
historical fee schedule or account tier. Earlier 0.4%/0.8% quote-fee results stay
valid only under their stated hypothetical assumptions; they do not estimate
this current schedule. No signal parameter is changed to improve those results.
