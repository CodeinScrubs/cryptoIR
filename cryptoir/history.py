"""Public hourly history with exchange-day reconciliation and immutable manifests."""

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from .domain import DataError, number
from .feeds import endpoint, public_response
from .streams import dumps

HISTORY = {"nobitex": "https://apiv2.nobitex.ir/market/udf/history",
           "wallex": "https://api.wallex.ir/v1/udf/history"}
HOUR, DAY = 3600, 86400


@dataclass(frozen=True)
class Candle:
    time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self):
        if type(self.time) is not int or self.time <= 0:
            raise DataError("invalid candle epoch")
        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, number(getattr(self, name)))
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise DataError("invalid OHLCV values")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise DataError("invalid OHLC boundaries")


def parse_udf(payload) -> list[Candle]:
    if payload.get("s") == "no_data":
        return []
    if payload.get("s") != "ok":
        raise DataError("historical endpoint returned an error")
    keys = ("t", "o", "h", "l", "c", "v")
    arrays = [payload[key] for key in keys]
    if any(not isinstance(value, list) for value in arrays) or len({len(a) for a in arrays}) != 1:
        raise DataError("unequal OHLCV arrays")
    rows = [Candle(*values) for values in zip(*arrays)]
    if any(a.time >= b.time for a, b in zip(rows, rows[1:])):
        raise DataError("duplicate or unordered source candles")
    return rows


def reconcile(hourly: list[Candle], daily: list[Candle], start: int, end: int) -> dict:
    """Daily timestamps define boundaries; never assume all venues use UTC days."""
    if any(a.time >= b.time for rows in (hourly, daily) for a, b in zip(rows, rows[1:])):
        raise DataError("duplicate or unordered reconciliation input")
    index = {bar.time: bar for bar in hourly}
    table, selected = [], []
    phases = {bar.time % DAY for bar in daily}
    if len(phases) > 1:
        raise DataError("daily session alignment changed; split dataset before reconciliation")
    phase = next(iter(phases)) if phases else None
    first = ((start-phase+DAY-1)//DAY)*DAY+phase if phase is not None else end
    expected_days = list(range(first, end-DAY+1, DAY))
    if any(first <= t < first+len(expected_days)*DAY and (t-first) % HOUR for t in index):
        raise DataError("unaligned hourly candle inside complete-session coverage")
    daily_index = {bar.time: bar for bar in daily}
    complete_days = [daily_index[t] for t in expected_days if t in daily_index]
    previous_day = None
    for day in complete_days:
        if previous_day is not None and day.time - previous_day < DAY:
            raise DataError("overlapping daily candles")
        previous_day = day.time
        expected = [day.time + i * HOUR for i in range(24)]
        rows = [index[t] for t in expected if t in index]
        item = {"day_open_epoch": day.time, "hour_count": len(rows), "deltas": None, "matched": False}
        if len(rows) == 24:
            aggregate = {"open": rows[0].open, "high": max(x.high for x in rows),
                         "low": min(x.low for x in rows), "close": rows[-1].close,
                         "volume": sum((x.volume for x in rows), Decimal(0))}
            item["deltas"] = {key: str(value - getattr(day, key)) for key, value in aggregate.items()}
            item["matched"] = all(number(delta) == 0 for delta in item["deltas"].values())
        selected.extend(rows)
        table.append(item)
    selected.sort(key=lambda bar: bar.time)
    gaps = [{"after": a.time, "before": b.time, "missing_hours": (b.time-a.time)//HOUR-1}
            for a, b in zip(selected, selected[1:]) if b.time-a.time != HOUR]
    missing_daily = [t for t in expected_days if t not in daily_index]
    return {"table": table, "bars": selected, "gaps": gaps, "missing_daily_epochs": missing_daily,
            "day_phase_utc_seconds": phase,
            "excluded_edge_or_unaligned_bars": len(hourly) - len(selected),
            "reconciled": bool(table) and all(day["matched"] for day in table) and not gaps and not missing_daily}


def download_history(output: Path, venue: str, pair: str, start: int, end: int) -> dict:
    if venue not in HISTORY:
        raise DataError("hourly history is currently documented for Nobitex and Wallex")
    endpoint(venue, pair)
    if not pair.endswith("/USDT"):
        raise DataError("historical fiat price units are not yet independently verified")
    if type(start) is not int or type(end) is not int or not 0 < start < end <= int(time.time()):
        raise DataError("require an explicit completed UTC interval")
    if end-start > 3 * 366 * DAY:
        raise DataError("use a bounded interval of at most three years")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": 1, "dataset": "hourly_spot_candles", "venue": venue, "pair": pair,
                "start": start, "end_exclusive": end, "resolution_seconds": HOUR,
                "status": "incomplete", "raw_files": [], "retrieved_ns": time.time_ns(),
                "fees_verified": False, "execution_ready": False}
    data = {"hourly": {}, "daily": {}}
    # At most 480 hourly candles or 365 daily candles per request.
    requests = [("hourly", "60", 20*DAY), ("daily", "D" if venue == "nobitex" else "1D", 365*DAY)]
    last_request = 0.0
    try:
        for name, resolution, window in requests:
            left = start
            while left < end:
                pause = 1.1 - (time.monotonic() - last_request)
                if pause > 0:
                    time.sleep(pause)
                right = min(left + window, end)
                url = HISTORY[venue] + "?" + urlencode({"symbol": pair.replace("/", ""),
                    "resolution": resolution, "from": left, "to": right-1})
                last_request = time.monotonic()
                record = public_response(url)
                raw_path = output / f"raw-{len(manifest['raw_files']):04d}.json"
                encoded = dumps(record).encode("utf-8")
                raw_path.write_bytes(encoded)
                manifest["raw_files"].append({"name": raw_path.name, "sha256": hashlib.sha256(encoded).hexdigest()})
                if record["kind"] != "snapshot":
                    raise DataError("history download failed; preserve partial evidence")
                rows = parse_udf(record["payload"])
                for bar in rows:
                    if not left <= bar.time < right:
                        raise DataError("historical endpoint returned out-of-window candles")
                    previous = data[name].get(bar.time)
                    if previous is not None and previous != bar:
                        raise DataError("conflicting overlapping historical candles")
                    data[name][bar.time] = bar
                left = right
        hourly = sorted(data["hourly"].values(), key=lambda row: row.time)
        daily = sorted(data["daily"].values(), key=lambda row: row.time)
        result = reconcile(hourly, daily, start, end)
        bars = result.pop("bars")
        normalized = "".join(dumps(asdict(bar)) + "\n" for bar in bars).encode()
        (output / "hourly.jsonl").write_bytes(normalized)
        (output / "reconciliation.json").write_text(dumps(result), encoding="utf-8")
        manifest.update(status="downloaded", bars=len(bars), raw_hourly_bars=len(hourly),
                        raw_daily_bars=len(daily), reconciled=result["reconciled"],
                        normalized_sha256=hashlib.sha256(normalized).hexdigest(),
                        gap_count=len(result["gaps"]),
                        covered_start=bars[0].time if bars else None,
                        covered_end=bars[-1].time+HOUR if bars else None)
    except Exception as exc:
        manifest.update(error_type=type(exc).__name__)
        if isinstance(exc, DataError):
            manifest["reason"] = str(exc)
        raise
    finally:
        (output / "manifest.json").write_text(dumps(manifest), encoding="utf-8")
    return manifest


def audit_history(directory: Path) -> tuple[dict, list[Candle]]:
    """Recompute from original response bodies; manifests are not a trust flag."""
    manifest_bytes = (directory / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest.get("schema") != 1 or manifest.get("dataset") != "hourly_spot_candles"
            or manifest.get("status") != "downloaded" or manifest.get("resolution_seconds") != HOUR):
        raise DataError("incomplete or unsupported historical dataset")
    venue, pair, start, end = (manifest[key] for key in ("venue", "pair", "start", "end_exclusive"))
    endpoint(venue, pair)
    if not pair.endswith("/USDT"):
        raise DataError("historical fiat price units are not yet independently verified")
    if venue not in HISTORY or any(type(t) is not int for t in (start, end)) or not 0 < start < end:
        raise DataError("invalid historical manifest")
    expected = []
    for name, resolution, window in (("hourly", "60", 20*DAY), ("daily", "D" if venue == "nobitex" else "1D", 365*DAY)):
        for left in range(start, end, window):
            right = min(left+window, end)
            expected.append((name, left, right, HISTORY[venue] + "?" + urlencode({
                "symbol": pair.replace("/", ""), "resolution": resolution, "from": left, "to": right-1})))
    if len(manifest["raw_files"]) != len(expected):
        raise DataError("historical request coverage differs from manifest")
    data = {"hourly": {}, "daily": {}}
    for i, (file, (name, left, right, url)) in enumerate(zip(manifest["raw_files"], expected)):
        if file["name"] != f"raw-{i:04d}.json" or not re.fullmatch(r"raw-\d{4}.json", file["name"]):
            raise DataError("invalid historical raw filename")
        encoded = (directory / file["name"]).read_bytes()
        if hashlib.sha256(encoded).hexdigest() != file["sha256"]:
            raise DataError("historical raw file hash mismatch")
        record = json.loads(encoded, parse_float=Decimal)
        if record.get("kind") != "snapshot" or record.get("url") != url or record.get("status_code") != 200:
            raise DataError("historical source provenance mismatch")
        raw = record["raw_body"]
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != record["body_sha256"]:
            raise DataError("historical body hash mismatch")
        original = json.loads(raw, parse_float=Decimal)
        if dumps(original) != dumps(record["payload"]):
            raise DataError("historical payload differs from original body")
        for bar in parse_udf(original):
            if not left <= bar.time < right or bar.time in data[name]:
                raise DataError("historical request overlap or out-of-range timestamp")
            data[name][bar.time] = bar
    result = reconcile(sorted(data["hourly"].values(), key=lambda b: b.time),
                       sorted(data["daily"].values(), key=lambda b: b.time), start, end)
    bars = result.pop("bars")
    normalized = (directory / "hourly.jsonl").read_bytes()
    if hashlib.sha256(normalized).hexdigest() != manifest["normalized_sha256"]:
        raise DataError("normalized history hash mismatch")
    if normalized != "".join(dumps(asdict(bar)) + "\n" for bar in bars).encode():
        raise DataError("normalized history differs from original source")
    if json.loads((directory / "reconciliation.json").read_bytes()) != result:
        raise DataError("stored reconciliation differs from source replay")
    expected_fields = {"bars": len(bars), "raw_hourly_bars": len(data["hourly"]),
                       "raw_daily_bars": len(data["daily"]), "reconciled": result["reconciled"],
                       "gap_count": len(result["gaps"]), "covered_start": bars[0].time if bars else None,
                       "covered_end": bars[-1].time+HOUR if bars else None}
    if any(manifest.get(key) != value for key, value in expected_fields.items()):
        raise DataError("historical manifest disagrees with source replay")
    return {"kind": "historical_reconciliation", "venue": venue, "pair": pair,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "normalized_sha256": manifest["normalized_sha256"], **expected_fields, **result}, bars
