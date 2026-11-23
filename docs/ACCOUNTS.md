# Read-only account assessment

Introduced in 0.10.0, updated in 0.14.1: **implemented and locally tested;
Nobitex profile/balance and Wallex fee/balance reads account-verified**. Tabdeal's
signed account read returns a partial assessment because duplicate asset balances
remain ambiguous. Its separate funding-wallet list was account-verified without
duplicate rows in the observed response; it does not replace or resolve the
account endpoint. All three venues were verified using saved Windows credentials,
without another prompt. Successful reads do not establish trading entitlement,
key permissions or order acceptance.

## راه‌اندازی روی لپ‌تاپ

PowerShell را در پوشهٔ پروژه باز کنید و هر دستور را جدا اجرا کنید:

```powershell
uv run python -m cryptoir account-check --venue nobitex
uv run python -m cryptoir account-check --venue wallex
uv run python -m cryptoir account-check --venue tabdeal
```

اگر کلید ذخیره شده باشد، این دستورها آن را خودکار از مخزن ویندوز می‌خوانند.
برای ذخیرهٔ یک‌باره، گزینهٔ `--save-credentials` را به هر دستور اضافه کنید؛
[راهنمای ذخیره و مدیریت کلیدها](CREDENTIALS.md) را ببینید.
کلیدها را فقط در همان پنجره، هنگام نمایش درخواست ورودی مخفی وارد کنید.
هنگام تایپ یا چسباندن چیزی نمایش داده نمی‌شود. کلید یا secret را در چت،
خط فرمان، فایل تنظیمات یا GitHub ننویسید. برای این بررسی، کلید دارای دسترسی
خواندن کافی است؛ مجوز معامله یا برداشت را برای اجرای این دستور فعال نکنید.

نوبیتکس به‌طور پیش‌فرض کلید عمومی API و کلید خصوصی Ed25519 با قالب
URL-safe Base64 را درخواست می‌کند. اگر اعتبارنامهٔ موجود شما **توکن قدیمی**
نوبیتکس است، به‌جای دستور اول از این دستور استفاده کنید:

```powershell
uv run python -m cryptoir account-check --venue nobitex --legacy-token
```

والکس فقط API key و تبدیل API key به‌همراه API secret می‌خواهند. زمان سیستم
باید درست باشد. در صورت خطای احراز هویت، این برنامه مجوزها را تغییر نمی‌دهد
و درخواست را خودکار تکرار نمی‌کند.

خروجی ترمینال فقط نتیجهٔ هر درخواست و نام گزارش خصوصی را نشان می‌دهد.
گزارش در `.local/accounts/` ذخیره می‌شود؛ موجودی و کارمزد آن خصوصی‌اند.
برای اعلام نتیجه در چت، نام صرافی و وضعیت `accepted` یا کد خطا کافی است؛
فایل گزارش یا کلیدها را ارسال نکنید. `accepted` یعنی پاسخ آن درخواست خوانده
و پردازش شده؛ کارمزد نامشخص همچنان نامشخص می‌ماند.

## Fixed operations and authentication

Reviewed against official sources on 2026-11-15. No caller-supplied URL, endpoint,
body, trading method, cancellation, transfer, withdrawal or key-management route
is exposed by this client.

| Venue | Allowed operation | Authentication |
| --- | --- | --- |
| Nobitex | `GET https://apiv2.nobitex.ir/users/profile` | Ed25519 key, signature, Unix seconds; explicit legacy Token alternative |
| Nobitex | `POST https://apiv2.nobitex.ir/users/wallets/list`, exact body `{"type":"spot"}` | Same; this is a documented **read-only POST** |
| Wallex | `GET https://api.wallex.ir/v1/account/fee` | `X-API-Key` |
| Wallex | `GET https://api.wallex.ir/v1/account/balances` | `X-API-Key` |
| Tabdeal | `GET https://api1.tabdeal.org/r/api/v1/account` | `X-MBX-APIKEY`, HMAC-SHA256 of the exact timestamp query in milliseconds |
| Tabdeal | `GET https://api1.tabdeal.org/r/api/v1/asset/get-funding-asset`, no asset filter | Same signed GET; documented list of all assets |

Nobitex signing follows its [API-key guide](https://apidocs.nobitex.ir/api_key/api-key-guide):
`timestamp + uppercase method + full path including query + exact raw body`.
Its [current user-data OpenAPI](https://apidocs.nobitex.ir/openapi/user_data.yaml)
documents READ permission and recommends POST for the wallet-list read. Legacy
profile fee fields come from the venue's [official legacy reference](https://github.com/nobitex/docs-api/blob/master/source/includes/_user_data.md).

Wallex documents [API-key permissions](https://developers.wallex.ir/docs/create-api-key),
[account fees](https://developers.wallex.ir/docs/basic-services-account-fee) and
[main-wallet balances](https://developers.wallex.ir/docs/basic-services-main-wallet).
Tabdeal documents its authentication and spot account response in its
[official API reference](https://docs.tabdeal.org/). Its `TRADE` security label
describes signed authentication for this GET; the client does not submit a trade.

## Interpretation boundaries

- Nobitex legacy `fee` / `feeUsdt` and Tabdeal `makerCommission` /
  `takerCommission` retain their observed numeric values. Units and sufficient
  maker/taker interpretation are not established here, so normalized rates stay
  null. No zero-fee fallback, percentage guess or copied Binance convention.
- Wallex maker/taker fields are documented ratios; the charged asset remains
  unverified. The fee result may contain additional entries without either fee
  field. Explicit market rows are parsed and those other entries are counted as
  unparsed; their values and labels are discarded. A row with only one fee field,
  an invalid rate or a duplicate native market still fails. No market coverage,
  missing-market rate or default fee is inferred. A reported ratio is not a
  complete fee model or historical tier.
- Nobitex reported total/free/blocked balances are checked for internal equality
  when all three exist. Missing or inconsistent components remain flagged.
- Wallex `value` and `locked` are retained, but whether `value` includes locked
  funds is not established. Spendable and total balances stay null.
- Tabdeal documented `free` and `freeze` are summed exactly within each row.
  Case-insensitive duplicate native assets are grouped as unresolved observations:
  their usable `free`, `locked` and `total` remain null. Identical observations,
  including all-zero duplicates, are not selected, deduplicated or added together.
  Unique rows and other allowlisted fields are retained. Any duplicate group marks
  the operation `partial / ambiguous_duplicate_balances`, with
  `balance_data_complete: false` and a nonzero exit status. Official documentation
  has not established how to resolve these duplicates. Negative or invalid
  amounts still reject normalization.
- Tabdeal's separately documented funding-wallet list is retained under
  `wallet_type: funding_endpoint`. Its array response is accepted only for that
  exact operation, with the same amount and duplicate safeguards. The observed
  read was accepted without duplicate assets. This is a separate timed snapshot;
  equivalence to the account endpoint, completeness of trading funds and the
  cause of account duplicates remain unverified. No values are merged or used
  to override an ambiguous account entry. Either read may fail independently;
  `account-check` preserves the other result and returns nonzero if either is
  partial or unavailable.
- Native asset/market identifiers stay venue-specific. No cross-venue asset
  identity, fiat conversion, portfolio allocation or balance transfer is inferred.
- Account capability flags are observations, not verified API-key permissions.
  An accepted read does not prove permission to trade or absence of withdrawal
  permissions. No write operation is used to test permissions.
- These are independent, timestamped snapshots, not an atomic portfolio across
  endpoints. No balances or fee settings are applied automatically to paper or
  live trading. Order reconciliation and leveraged accounts remain planned.

## Private output and failure behavior

Credentials come from hidden terminal prompts or this checkout's native Windows
Credential Manager entries. An input path that would echo them is refused.
Explicit saving uses the Windows vault, never a plaintext project file or log;
see [storage and its limits](CREDENTIALS.md). Python strings cannot guarantee
memory zeroization. Raw responses, bank details, names, emails and websocket
tokens are not retained. The profile response is read in memory because that
endpoint supplies the legacy fee observations.

Only allowlisted normalized fields, response hashes, timing, parser hash and
client version go into a new private report. The output location is fixed under
the checkout's ignored `.local/accounts/`; no arbitrary CLI output path. Files
are exclusively created, directory links/junctions are refused, and POSIX
permissions are owner-only for newly created directories/files. On Windows the
directory inherits the user's existing ACL; this is not an encrypted vault.

If fee normalization fails after a response was received, the private report
also retains a bounded structural diagnostic: value types, container lengths,
counts and allowlisted field labels only. Scalar values and arbitrary keys are
never copied; even a private value used as a JSON key is redacted. This permits
schema debugging without storing the raw response or printing account details.

Verified HTTPS, a ten-second socket timeout, a two-MiB response cap and no
redirect following apply. There are no retries. Duplicate JSON keys, non-finite
numbers, oversized responses and malformed schemas fail closed. HTTP/network
errors expose fixed diagnostic codes, not response bodies, signed URLs or
exception details. A partial assessment stays partial; exit status is nonzero
when any requested read is unavailable or partial. Exit zero does not resolve
unknown fees. Credential persistence is reported independently of read acceptance.

The paper runner remains independent of this optional command. Its source seal
includes Python modules, so changing versions requires the documented upgrade
procedure or continuing the previously frozen runtime; never reset capital to
work around a source mismatch.


## Nobitex spot order reconciliation (0.27.0)

`order-check` uses the saved Windows credential for three authenticated GETs on
BTC/USDT. The official [API key guide](https://apidocs.nobitex.ir/api_key/api-key-guide)
specifies READ permission for these routes and signing the full path including
the query. See the official [spot trade section](https://apidocs.nobitex.ir/spot_trade/معاملات-اسپات)
for the order and fill endpoints.

```powershell
uv run python -m cryptoir order-check
```

It checks page 1 with a requested size of ten for open spot orders, recent
spot orders across statuses and recent spot fills. These three GETs run in
sequence. The bounded report carries the pagination flag when present and the
timing of each accepted page. Missing pagination stays unknown. No subsequent
page, retry or alternate market is requested by this command.

The report compares overlapping order states and sums observed fill quantities
by venue order ID using Decimal. A changed order, side disagreement, missing
order or incomplete quantity produces a distinct result. Matching quantities
describe only the rows observed at those separate request times. Empty pages
produce no fill evidence. Fees are preserved as reported amounts with unknown
fee assets and are excluded from settlement. This reconciliation covers the
venue's own records. Local live intents and balances are outside its scope.

The terminal reports operation statuses and a file name. Normalized account
observations stay under the ignored `.local/accounts/` directory. The client
does not retain raw bodies, raw client-order IDs or credential material.
The three requests were **account-verified for endpoint acceptance** with
a saved Ed25519 key. Populated order and fill rows and quantity matching were
**locally tested with synthetic fixtures**. Trading requires a separate
authorization and prospective strategy evidence.
