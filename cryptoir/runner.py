"""Bounded public-data forward paper sessions; never authenticated trade calls."""

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .domain import DataError
from .feeds import public_response
from .forward import ForwardConfig, ForwardEngine, NS
from .history import HISTORY, HOUR, parse_udf
from .paper import PaperLedger
from .runtime import OwnerLock, atomic_json
from .stream_audit import book_from_dict
from .streams import ConsumerFailure, EventWriter, RecordingLimit, capture_venue


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
    url=HISTORY[config.venue]+"?"+urlencode({"symbol":config.pair.replace("/",""),
        "resolution":"60","from":start,"to":end-1})
    return {"requested_start":start,"requested_end":end,"response":public_response(url)}


def closed_candles(record: dict):
    response=record["response"]
    if response["kind"]!="snapshot":
        raise DataError("public candle request failed")
    rows=parse_udf(response["payload"])
    if any(not record["requested_start"]<=bar.time<record["requested_end"] for bar in rows):
        raise DataError("candle endpoint returned an unexpected timestamp")
    cutoff=min(record["requested_end"],response["received_ns"]//NS-120)
    return [bar for bar in rows if bar.time+HOUR<=cutoff]


class PaperWriter(EventWriter):
    def __init__(self,path,max_bytes,engine,clock):
        super().__init__(path,max_bytes)
        self.engine,self.clock=engine,clock

    def write(self,venue,event):
        record=super().write(venue,event)
        if abs(record["clock_error_ns"])>NS:
            raise ClockChanged("recording clock discontinuity")
        try:
            kind=record["kind"]
            if kind=="book":
                self.engine.on_book(book_from_dict(record["book"]),now_ns=self.clock.now())
            elif kind in ("gap","session_start","book_unavailable"):
                self.engine.invalidate(self.clock.now(),record.get("reason",kind))
        except ConsumerFailure:
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
        try:
            if not ledger.db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                ledger.seed({(config.venue,config.pair.split("/")[1]):config.risk.initial_quote})
            engine=ForwardEngine(ledger,config)
            engine.start(clock.now())
            writer=PaperWriter(directory/("market-"+session_id+".jsonl.gz"),max_mb*1024*1024,engine,clock)
            writer.write("all",{"kind":"recording_start","pair":config.pair,"venues":[config.venue],
                                "duration_seconds":seconds,"purpose":"experimental_forward_paper",
                                "live_execution":False,"max_mb":max_mb})

            def status(state="running"):
                atomic_json(directory/"status.json",{"schema":1,"session_id":session_id,"pid":os.getpid(),
                    "updated_ns":clock.safe_now(),"session_status":state,"reason":reason if state!="running" else None,
                    "counts":dict(writer.counts),"uncompressed_log_bytes":writer.size,**engine.report()})

            async def poll_candles():
                failures=0
                while True:
                    last=engine.signal.bars[-1].time if engine.signal.bars else None
                    try:
                        record=await asyncio.to_thread(candle_response,config,last,clock.now())
                        writer.write(config.venue,{"kind":"candle_response","pair":config.pair,**record})
                        rows=closed_candles(record)
                        engine.apply_candles(rows,received_ns=record["response"]["received_ns"],now_ns=clock.now())
                        if engine.state()["candles_ok"]:
                            writer.write(config.venue,{"kind":"candle_accepted","last_bar":engine.signal.bars[-1].time})
                        failures=0
                    except DataError as exc:
                        engine.candle_failure(clock.now(),str(exc))
                        failures+=1
                        if failures>=5:
                            raise ConsumerFailure("five consecutive candle failures") from exc
                    await asyncio.sleep(60)

            async def supervise():
                start=time.monotonic()
                saved=0.0
                while time.monotonic()-start<seconds:
                    now=clock.now()
                    engine.advance(now)
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
                    engine.stop(clock.safe_now(),permanent=reason in ("manual_stop","clock_changed","failed"),reason=reason)
                if writer is not None:
                    # The terminal record must not invoke a failed downstream clock.
                    try:
                        EventWriter.write(writer,"all",{"kind":"recording_end","reason":reason})
                    except RecordingLimit:
                        pass
                    writer.close()
                if engine is not None:
                    report={"schema":1,"session_id":session_id,"pid":os.getpid(),"updated_ns":clock.safe_now(),
                            "session_status":"stopped","reason":reason,**engine.report()}
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
