# Local credential storage

Version 0.13.0: **implemented, locally tested and account-verified** on Windows.
The user explicitly chooses whether to save keys. No credentials belong in Git,
chat, command-line arguments, environment examples or plaintext configuration.

## یک بار ذخیره، سپس استفادهٔ خودکار

در ترمینال محلی، هر دستور را جدا اجرا کنید و کلیدها را فقط در ورودی مخفی وارد کنید:

```powershell
uv run python -m cryptoir account-check --venue nobitex --save-credentials
uv run python -m cryptoir account-check --venue wallex --save-credentials
uv run python -m cryptoir account-check --venue tabdeal --save-credentials
```

نوبیتکس کلید API و کلید خصوصی Ed25519، والکس فقط API key و تبدیل API key
و secret را می‌خواهد. برای توکن قدیمی نوبیتکس، هنگام ذخیره `--legacy-token`
را هم اضافه کنید. نوع اعتبارنامه همراه کلید ذخیره می‌شود.

از این پس گزینهٔ ذخیره را حذف کنید؛ اجرای معمولی دوباره کلید نمی‌پرسد:

```powershell
uv run python -m cryptoir account-check --venue nobitex
uv run python -m cryptoir account-check --venue wallex
uv run python -m cryptoir account-check --venue tabdeal
```

خروجی `credential_source: windows_credential_manager` بازیابی از مخزن را نشان
می‌دهد. `credentials_stored: true` مستقل از وضعیت پاسخ صرافی است؛ مثلاً
`partial` تبدیل به معنی شکست ذخیرهٔ کلید نیست. ذخیره پیش از درخواست شبکه
انجام می‌شود، پس حتی خطای شبکه یا احراز هویت آن را خودکار پاک نمی‌کند.

برای مشاهدهٔ وضعیت ذخیره، بدون نمایش کلید:

```powershell
uv run python -m cryptoir credentials-status --venue wallex
```

برای جایگزینی کلید، دستور همان صرافی را دوباره با `--save-credentials` اجرا
کنید. برای حذف فقط نسخهٔ ذخیره‌شدهٔ محلی، این دستور را با نام صرافی موردنظر
اجرا کنید:

```powershell
uv run python -m cryptoir credentials-forget --venue wallex
```

حذف محلی کلید را در صرافی باطل نمی‌کند. پس از حذف یا اگر هیچ کلیدی ذخیره
نباشد، اجرای معمولی دوباره ورودی مخفی می‌خواهد و خودش آن را ذخیره نمی‌کند.

## Storage boundary

The implementation uses native `CredWriteW` / `CredReadW` generic credentials,
protected by Windows Credential Manager for the current Windows user. Persistence
is `CRED_PERSIST_LOCAL_MACHINE`: later logons by the same user on the same
computer can reuse the entry. It does not request enterprise roaming.
See Microsoft's [credential structure and persistence contract](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw),
[write API](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)
and [read API](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw).

Entries are scoped to the checkout's resolved path hash and venue. The program
reads, replaces or removes only the requested entry; it never enumerates other
applications' credentials. Moving the checkout creates a different namespace,
leaving the old entry intact. Updating code in the same location retains access.

There is no plaintext fallback. A vault error stops the operation with a fixed
diagnostic. Outside Windows, explicit saving is refused; hidden ephemeral input
remains available. No third-party storage package or cloud service is required.

This protects persisted secrets through the Windows user vault; it is not
isolation from software running as that same user. Temporary native buffers are
cleared, but Python strings cannot guarantee memory zeroization. Private account
reports contain no credentials and remain separate, unencrypted local files.

Saving keys does not enable trading. The current account client has only fixed
read operations; public collection and paper trading remain independent of it.

## Verification

Synthetic tests exercise serialization, checkout isolation, invalid payloads,
native buffer cleanup, failures, explicit replacement and automatic reuse.
A separate native Windows probe verified cross-process retrieval and removed
only its own synthetic entry. Actual saved credentials were then used in fresh
CLI processes without prompts: Nobitex and Wallex reads were accepted; Tabdeal
returned the documented partial assessment. CI uses mocks, never user keys.
