"use strict";
const $ = id => document.getElementById(id);
const formatters = new Map();
function amount(value, digits = 4) {
  if (value === null || value === undefined) return "—";
  if (!formatters.has(digits)) formatters.set(digits, new Intl.NumberFormat("fa-IR", {maximumFractionDigits: digits}));
  // Intl formats the decimal string; never use these display values for accounting.
  return formatters.get(digits).format(value);
}
const date = ms => ms ? new Intl.DateTimeFormat("fa-IR", {timeZone: "Asia/Tehran", dateStyle: "short", timeStyle: "medium"}).format(new Date(ms)) : "—";
const pct = value => value === undefined || value === null ? "—" : amount(value, 3) + "٪";
const text = (id, value) => { $(id).textContent = value; };
function cell(row, value, header = false, tone = null) {
  const el = document.createElement(header ? "th" : "td");
  el.textContent = value;
  if (header) el.scope = "row";
  if (tone) el.className = String(tone).startsWith("-") ? "negative" : "positive";
  row.append(el);
}
function empty(id, columns, label) {
  const row = document.createElement("tr");
  const item = document.createElement("td"); item.colSpan = columns; item.textContent = label;
  row.append(item); $(id).replaceChildren(row);
}
function badge(id, label, fresh = false) { text(id, label); $(id).dataset.tone = fresh ? "ok" : "stale"; }
const reasons = {duration_completed: "مدت اجرا پایان یافته", manual_stop: "توقف دستی", failed: "خطای اجرا", clock_changed: "تغییر ساعت دستگاه", storage_limit: "سقف فضای ثبت داده", feed_worker_stopped: "توقف دریافت داده"};
const orderStates = {PENDING: "در انتظار", FILLED: "پرشده", PARTIAL: "بخشی پرشده", CANCELED: "لغوشده", EXPIRED: "منقضی", REJECTED: "ردشده"};
let latest = null, receivedAt = 0, online = false, paused = false, busy = false, timer;

function freshness() {
  if (!latest) return;
  const elapsed = (performance.now() - receivedAt) / 1000;
  const process = latest.process_state;
  const alive = process === "running" && latest.heartbeat_age_seconds + elapsed <= 15;
  let label;
  let tone = "warning";
  if (paused) label = "تازه‌سازی صفحه متوقف است؛ این کار اجرای ربات را متوقف نمی‌کند. مقادیر زیر آخرین گزارش‌اند.";
  else if (!online) { label = "ارتباط با گزارش محلی قطع است؛ داده‌های نمایش‌داده‌شده قدیمی‌اند."; tone = "error"; }
  else if (latest.risk?.halt) { label = "توقف پایدار ریسک: " + (reasons[latest.risk.halt] || latest.risk.halt) + " · دارایی‌ها همچنان نگهداری می‌شوند."; tone = "error"; }
  else if (alive) { label = "اجرای paper فعال است · سفارش‌ها فقط شبیه‌سازی می‌شوند."; tone = "ok"; }
  else if (process === "stopped") label = "اجرای paper متوقف است · " + (reasons[latest.reason] || "پایان جلسه") + " · موجودی حفظ شده است.";
  else { label = "فعال بودن اجرا تأیید نمی‌شود؛ وضعیت اتصال، ساعت یا پاسخ‌گویی فرایند را بررسی کنید."; tone = "error"; }
  if ($("connection").textContent !== label) text("connection", label);
  $("connection").dataset.tone = tone;
  const v = latest.matched_valuation || {};
  const current = online && !paused && alive && v.available && v.age_seconds !== null && v.age_seconds + elapsed <= 5;
  const book = online && !paused && alive && latest.book_current && latest.book_age_seconds !== null && latest.book_age_seconds + elapsed <= (latest.risk_limits?.max_feed_age_ns || 5e9) / 1e9;
  badge("valuation-chip", current ? "مشاهدهٔ تازه · مشروط" : v.last_valuation ? "دادهٔ منقضی · تاریخی" : "بدون ارزش‌گذاری", current);
  text("valuation-state", v.last_valuation ? (current ? "مشروط به عمق و کارمزد مدل · " : "تاریخی؛ قیمت اکنون نیست · ") + date(v.last_valuation.observed_ms) : "ارزش‌گذاری تومانی در دسترس نیست");
  badge("book-chip", book ? "مشاهدهٔ تازه" : latest.last_book ? "دفتر سفارش منقضی" : "بدون دفتر سفارش", book);
  badge("risk-chip", latest.risk?.halt ? "توقف پایدار" : latest.risk_current && book ? "کنترل با دادهٔ تازه" : "آخرین وضعیت ثبت‌شده", latest.risk_current && book && !latest.risk?.halt);
}

function render(data) {
  latest = data; receivedAt = performance.now();
  text("updated", date(data.updated_ms)); $("updated").dateTime = new Date(data.updated_ms).toISOString();
  text("initial", data.initial_irt == null ? amount(data.risk?.initial_quote) + " تتر" : amount(data.initial_irt, 2) + " تومان");
  text("basis", data.capital_basis === "paper_irt_conversion" ? "تبدیل تومانی شبیه‌سازی‌شده" : "سرمایهٔ مرجع مصنوعی");
  const quote = data.balances?.find(x => x.asset === "USDT");
  const baseAsset = (data.pair || "BTC/USDT").split("/")[0];
  const base = data.balances?.find(x => x.asset === baseAsset);
  text("usdt", amount(quote?.free ?? "0", 8) + " تتر آزاد");
  text("reserved", "رزرو سفارش: " + amount(quote?.reserved ?? "0", 8) + " تتر");
  text("base", amount(base?.free ?? "0", 8) + " " + baseAsset);
  text("position", "رزرو سفارش: " + amount(base?.reserved ?? "0", 8) + " " + baseAsset + " · توقف به معنی فروش نیست");
  const v = data.matched_valuation?.last_valuation;
  text("liquidation", v ? amount(v.portfolio.irt_liquidation_mark, 2) + " تومان" : "—");
  if (v) {
    const rows = [["حساب paper", v.portfolio], ["نگهداری تتر", v.benchmarks.usdt_hold], ["نگهداری بیت‌کوین", v.benchmarks.btc_hold], ["نگهداری تومان", v.benchmarks.irt_cash]];
    $("benchmarks").replaceChildren(...rows.map(([label, p]) => {
      const row = document.createElement("tr"); cell(row, label, true);
      cell(row, amount(p.irt_liquidation_mark, 2)); cell(row, pct(p.net_return_irt_percent), false, p.net_return_irt_percent);
      cell(row, amount(p.usdt_numeraire_mark, 6)); cell(row, pct(p.net_return_usdt_numeraire_percent), false, p.net_return_usdt_numeraire_percent); return row;
    }));
  } else empty("benchmarks", 5, "هنوز ارزش‌گذاری ثبت نشده است.");
  const r = data.risk || {}, limits = data.risk_limits || {};
  text("drawdown", pct(r.drawdown_fraction_percent) + " / " + pct(limits.drawdown_fraction_percent));
  text("daily", pct(r.daily_loss_fraction_percent) + " / " + pct(limits.daily_loss_fraction_percent));
  text("exposure", pct(r.gross_exposure_fraction_percent) + " / " + pct(limits.max_exposure_fraction_percent));
  text("order-cap", amount(limits.maximum_order_notional, 8) + " تتر");
  text("halt", r.halt ? (reasons[r.halt] || r.halt) : "فعال نشده");
  text("market", (data.venue === "nobitex" ? "نوبیتکس" : data.venue || "نامشخص") + " · " + (data.pair || "نامشخص"));
  text("strategy", data.strategy || "—");
  text("warmup", amount(data.warmup_bars, 0) + " ساعت");
  text("target", data.forward?.long ? "نگهداری دارایی پایه" : "بدون موقعیت هدف");
  text("decision", data.forward?.decision ? "از " + date(data.forward.decision.due_ns / 1e6) : "ندارد");
  text("sessions", amount(data.forward?.session_count, 0));
  const book = data.last_book;
  for (const side of ["bids", "asks"]) {
    if (!book) { empty(side, 2, "هنوز دفتر سفارش ثبت نشده است."); continue; }
    $(side).replaceChildren(...book[side].map(level => { const row = document.createElement("tr"); cell(row, amount(level.price, 8), true); cell(row, amount(level.quantity, 8)); return row; }));
  }
  text("book-time", book ? "زمان منبع: " + date(book.exchange_ns / 1e6) + " · زمان دریافت: " + date(book.received_ns / 1e6) + " · حداکثر ۱۰ ردیف" : "در انتظار ثبت دفتر سفارش در یک اجرای جدید");
  text("order-count", amount(data.order_count, 0) + " سفارش در این حساب");
  if (data.orders.length) {
    $("order-rows").replaceChildren(...data.orders.map(o => { const row = document.createElement("tr"); cell(row, o.id, true); cell(row, o.side === "buy" ? "خرید" : "فروش"); cell(row, orderStates[o.status] || o.status); cell(row, amount(o.filled, 8)); cell(row, amount(o.notional, 8)); cell(row, amount(o.fee, 8) + " " + o.fee_asset); return row; }));
  } else empty("order-rows", 6, "هنوز سفارشی ثبت نشده است؛ بدون سفارش بودن به معنی سودآوری نیست.");
  text("counts", "دورهٔ اخیر: " + amount(data.counts?.["nobitex:book"] ?? 0, 0) + " مشاهدهٔ دفتر سفارش · " + amount(data.journal_events, 0) + " رویداد حساب");
  freshness();
}

async function poll() {
  clearTimeout(timer);
  if (paused || busy) return;
  busy = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch("/api/status", {cache: "no-store", credentials: "omit", signal: controller.signal});
    if (!response.ok) throw new Error("Unavailable");
    const value = await response.json();
    if (value.schema !== 1 || value.mode !== "paper" || value.live_execution !== false) throw new Error("Unsupported");
    online = true; render(value);
  } catch {
    online = false;
    if (!latest) { text("connection", "گزارش محلی قابل خواندن نیست. صفحه دوباره تلاش می‌کند."); $("connection").dataset.tone = "error"; }
    freshness();
  } finally { clearTimeout(timeout); busy = false; if (!paused) timer = setTimeout(poll, 2000); }
}
$("pause").addEventListener("click", () => {
  paused = !paused; $("pause").setAttribute("aria-pressed", String(paused));
  text("pause", paused ? "ادامهٔ تازه‌سازی صفحه" : "توقف تازه‌سازی صفحه");
  if (paused) clearTimeout(timer); else poll();
  freshness();
});
setInterval(freshness, 1000);
poll();
