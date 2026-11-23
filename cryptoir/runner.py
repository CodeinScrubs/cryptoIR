"""Bounded public-data forward paper sessions; never authenticated trade calls."""

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .domain import DataError
from .feeds import public_response
from .forward import ForwardConfig, ForwardEngine, NS, source_fingerprint
from .history import HOUR
from .paper import PaperLedger
from .paper_replay import (FEED_ACTIONS, INPUT_VERSION, REPLAYABLE_STOPS, apply_input, candle_url,
                           config_digest, ledger_digest)
from .runtime import OwnerLock, atomic_json
from .streams import ConsumerFailure, EventWriter, RecordingIOError, RecordingLimit, capture_venue
from .valuation import ALL_BOOKS, matched_valuation, snapshot_pair, verify_receipt


class ClockChanged(ConsumerFailure):
    pass


class SessionClock:
    def __init__(self):
        self.wall, self.mono = time.time_ns(), time.monotonic_ns()
        self.latest = self.wall

    def safe_now(self):
        return max(self.latest,self.wall+time.monotonic_ns()-self.mono)

    def now(self):
        wall = time.time_ns()
        projected = self.wall+time.monotonic_ns()-self.mono
        if abs(wall-projected)>NS or wall<self.latest:
            raise ClockChanged("local wall clock changed")
        self.latest=wall
        return wall


def candle_response(config: ForwardConfig, last_epoch: int | None, now_ns: int) -> dict:
    # Two-minute completion allowance is a research assumption; revisions halt.
    end=now_ns//NS-120
    start=end-240*HOUR if last_epoch is None else last_epoch-2*HOUR
    if end-start>480*HOUR:
        raise DataError("downtime exceeds bounded candle recovery window")
    url=candle_url(config,start,end)
    return {"requested_start":start,"requested_end":end,"response":public_response(url)}


class PaperWriter(EventWriter):
    def __init__(self,path,max_bytes,engine,clock):
        super().__init__(path,max_bytes)
        self.engine,self.clock=engine,clock
        self.candle_failure_reason = None

    def input(self, action, *, source=None, reason=None, now_ns=None):
        at = self.clock.now() if now_ns is None else now_ns
        result = apply_input(self.engine, action, at, source=source, reason=reason)
        self.record_input(action, at, source=source, reason=reason)
        return result

    def record_input(self, action, at, *, source=None, reason=None):
        # Capture the clock at dispatch rather than before an extra log flush.
        # A crash between settlement and transcript persistence leaves an
        # incomplete tape, which cannot pass the offline closing checkpoint.
        event = {"kind": "paper_input", "action": action, "now_ns": at}
        if source is not None:
            event["source_seq"] = source["seq"]
        if reason is not None:
            event["reason"] = reason
        row = super().write("all", event)
        if abs(row["clock_error_ns"]) > NS or at > row["observed_ns"]:
            raise ClockChanged("recording clock discontinuity")

    def write(self,venue,event):
        record=super().write(venue,event)
        if abs(record["clock_error_ns"])>NS:
            raise ClockChanged("recording clock discontinuity")
        try:
            kind=record["kind"]
            if kind in FEED_ACTIONS:
                failure = self.input(kind, source=record)
                if kind == "candle_response":
                    self.candle_failure_reason = failure
        except (ConsumerFailure, RecordingLimit, RecordingIOError):
            raise
        except Exception as exc:
            raise ConsumerFailure("forward consumer failed: "+type(exc).__name__) from exc
        return record


async def run_paper(config: ForwardConfig, directory: Path, seconds: int, *, max_mb: int=64):
    if type(seconds) is not int or not 5<=seconds<=86400 or type(max_mb) is not int or not 1<=max_mb<=512:
        raise DataError("bounded paper duration 5..86400 seconds and log cap 1..512 MiB required")
    directory.mkdir(parents=True,exist_ok=True)
    with OwnerLock(directory/"owner.lock"):
        if (directory/"STOP").exists():
            raise DataError("STOP marker exists; it is never removed automatically")
        if shutil.disk_usage(directory).free<1024*1024*1024:
            raise DataError("paper runner requires at least 1 GiB of free disk")
        ledger=PaperLedger(directory/"paper.sqlite")
        writer=None
        engine=None
        clock=SessionClock()
        tasks=[]
        reason="interrupted"
        failure=None
        session_id=uuid.uuid4().hex
        capital_receipt=None
        valuation={"available":False,"reason":"not_requested_for_synthetic_reference"}
        replay_checkpoint = None
        try:
            if config.capital_basis=="paper_irt_conversion":
                capital_receipt=json.loads((directory/"capital-receipt.json").read_bytes())
                verify_receipt(capital_receipt)
                from .domain import number
                if (capital_receipt["receipt_sha256"]!=config.capital_receipt_sha256
                        or number(capital_receipt["conversion"]["net_base"])!=config.risk.initial_quote):
                    raise DataError("paper capital differs from sealed conversion receipt")
                if (config.rules.buy_fee_mode!="received_base" or config.pair!=capital_receipt["pair"]
                        or any(number(capital_receipt["spot_rules"][key])!=getattr(config.rules,key)
                               for key in ("quantity_step","minimum_notional","fee_rate"))
                        or number(capital_receipt["spot_rules"]["participation"])!=config.risk.depth_participation):
                    raise DataError("paper execution and benchmark cost/rule assumptions differ")
                valuation={"available":False,"reason":"awaiting_fresh_valuation"}
            if not ledger.db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                ledger.seed({(config.venue,config.pair.split("/")[1]):config.risk.initial_quote})
            code_sha = source_fingerprint()
            engine=ForwardEngine(ledger,config,source_id=code_sha)
            writer=PaperWriter(directory/("market-"+session_id+".jsonl.gz"),max_mb*1024*1024,engine,clock)
            writer.write("all",{"kind":"recording_start","pair":config.pair,"venues":[config.venue],
                                "duration_seconds":seconds,"purpose":"experimental_forward_paper",
                                "live_execution":False,"max_mb":max_mb,"paper_input_version":INPUT_VERSION,
                                "source_sha256":code_sha,"config_sha256":config_digest(config),
                                "opening_ledger_sha256":ledger_digest(ledger)})
            writer.input("start")

            def status(state="running"):
                atomic_json(directory/"status.json",{"schema":1,"session_id":session_id,"pid":os.getpid(),
                    "updated_ns":clock.safe_now(),"session_status":state,"reason":reason if state!="running" else None,
                    "counts":dict(writer.counts),"uncompressed_log_bytes":writer.size,**engine.report(),
                    "matched_valuation":valuation,"irt_performance_available":valuation["available"]})

            async def poll_valuation():
                nonlocal valuation
                while True:
                    record=await asyncio.to_thread(public_response,ALL_BOOKS)
                    writer.write(config.venue,{"kind":"valuation_response","response":record})
                    try:
                        usdt=sum(ledger.balance(config.venue,"USDT"))
                        btc=sum(ledger.balance(config.venue,"BTC"))
                        observed=clock.now()
                        value=matched_valuation(capital_receipt,snapshot_pair(record,"USDT/IRR"),snapshot_pair(record,"BTC/USDT"),
                                                usdt,btc,now_ns=observed,max_age_ns=5*NS)
                        valuation={"available":True,"last_valuation":value,"marked_balances":{"usdt":str(usdt),"btc":str(btc)}}
                        writer.write(config.venue,{"kind":"portfolio_valuation","valuation":value})
                    except (DataError,KeyError,TypeError) as exc:
                        valuation={"available":False,"reason":str(exc) if isinstance(exc,DataError) else type(exc).__name__,
                                   "last_valuation":valuation.get("last_valuation")}
                        writer.write(config.venue,{"kind":"valuation_unavailable","reason":valuation["reason"]})
                    await asyncio.sleep(60)

            async def poll_candles():
                failures=0
                while True:
                    last=engine.signal.bars[-1].time if engine.signal.bars else None
                    try:
                        record=await asyncio.to_thread(candle_response,config,last,clock.now())
                    except DataError as exc:
                        writer.input("candle_failure", reason=str(exc))
                        failures+=1
                    else:
                        writer.write(config.venue,{"kind":"candle_response","pair":config.pair,**record})
                        if engine.state()["candles_ok"]:
                            writer.write(config.venue,{"kind":"candle_accepted","last_bar":engine.signal.bars[-1].time})
                        failures=failures+1 if writer.candle_failure_reason else 0
                    if failures>=5:
                        raise ConsumerFailure("five consecutive candle failures")
                    await asyncio.sleep(60)

            async def supervise():
                start=time.monotonic()
                saved=0.0
                while time.monotonic()-start<seconds:
                    now=clock.now()
                    writer.input("advance", now_ns=now)
                    if valuation["available"]:
                        current={"usdt":str(sum(ledger.balance(config.venue,"USDT"))),"btc":str(sum(ledger.balance(config.venue,"BTC")))}
                        if now-valuation["last_valuation"]["observed_ns"]>5*NS or current!=valuation["marked_balances"]:
                            valuation.update(available=False,reason="valuation_stale_or_balances_changed")
                    if (directory/"STOP").exists():
                        return "manual_stop"
                    if time.monotonic()-saved>=5:
                        status()
                        saved=time.monotonic()
                    await asyncio.sleep(1)
                return "duration_completed"

            transport=asyncio.create_task(capture_venue(config.venue,config.pair,writer))
            candles=asyncio.create_task(poll_candles())
            supervisor=asyncio.create_task(supervise())
            tasks=[transport,candles,supervisor]
            if capital_receipt is not None:
                tasks.append(asyncio.create_task(poll_valuation()))
            done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            # Observe errors before treating a coincident duration expiry as success.
            for task in done:
                task.result()
            reason=supervisor.result() if supervisor in done else "feed_worker_stopped"
        except RecordingLimit:
            reason="storage_limit"
        except ClockChanged as exc:
            reason="clock_changed"
            failure=exc
        except Exception as exc:
            reason="failed"
            failure=exc
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            try:
                if engine is not None:
                    stopped = False
                    if writer is not None and reason in REPLAYABLE_STOPS:
                        try:
                            at = clock.now()
                            apply_input(engine, "stop", at, reason=reason)
                            stopped = True
                            writer.record_input("stop", at, reason=reason)
                            replay_checkpoint = ledger_digest(ledger)
                        except ClockChanged as exc:
                            reason, failure = "clock_changed", exc
                        except RecordingLimit:
                            # Preserve manual-stop semantics when the transcript is full.
                            if reason != "manual_stop":
                                reason = "storage_limit"
                        except RecordingIOError as exc:
                            failure = exc
                            if reason != "manual_stop":
                                reason = "failed"
                    if not stopped:
                        engine.stop(clock.safe_now(),permanent=reason in ("manual_stop","clock_changed","failed"),reason=reason)
                    elif reason in ("clock_changed", "failed"):
                        engine.guard.halt(now_ns=clock.safe_now(), reason=reason)
                if writer is not None:
                    # The terminal record must not invoke a failed downstream clock.
                    try:
                        EventWriter.write(writer,"all",{"kind":"recording_end","reason":reason,
                                          "closing_ledger_sha256":replay_checkpoint})
                    except (RecordingLimit, RecordingIOError):
                        replay_checkpoint = None
                    finally:
                        writer.close()
                if engine is not None:
                    report={"schema":1,"session_id":session_id,"pid":os.getpid(),"updated_ns":clock.safe_now(),
                            "session_status":"stopped","reason":reason,**engine.report(),"matched_valuation":valuation,
                            "irt_performance_available":valuation["available"],
                            "paper_closing_ledger_sha256":replay_checkpoint}
                    if writer is not None:
                        report.update(counts=dict(writer.counts),uncompressed_log_bytes=writer.size)
                    if failure is not None:
                        report["error_type"]=type(failure).__name__
                    atomic_json(directory/"status.json",report)
                    atomic_json(directory/("session-"+session_id+".json"),report)
            finally:
                ledger.close()
        if failure is not None:
            raise failure
        return report
