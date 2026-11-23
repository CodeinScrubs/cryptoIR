import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptoir.domain import DataError
from cryptoir.forward import ForwardConfig, ForwardEngine, NS
from cryptoir.history import HOUR
from cryptoir.paper_replay import candle_url, closed_candles, replay
from cryptoir.runner import ClockChanged, SessionClock, run_paper
from cryptoir.runtime import OwnerLock
from cryptoir.stream_audit import audit_stream
from cryptoir.streams import ConsumerFailure


def response(config,last,now):
    close=(now//NS-120)//HOUR*HOUR
    times=list(range(close-200*HOUR,close,HOUR))
    payload={"s":"ok","t":times,"o":[100]*200,"h":[100]*200,"l":[100]*200,"c":[100]*200,"v":[100]*200}
    raw_body=json.dumps(payload)
    return {"requested_start":times[0],"requested_end":close,"response":{
        "kind":"snapshot","received_ns":now,"payload":payload,"raw_body":raw_body,
        "url":candle_url(config,times[0],close) if config is not None else None,
        "body_sha256":hashlib.sha256(raw_body.encode()).hexdigest()}}


class RunnerDataTests(unittest.TestCase):
    def test_closed_filter_excludes_active_hour(self):
        now=2000*HOUR*NS+120*NS
        record=response(None,None,now)
        self.assertEqual(len(closed_candles(record)),200)
        # Add a bar that has opened but hasn't closed at the observation cutoff.
        record["requested_end"]+=1
        record["response"]["payload"]["t"].append(2000*HOUR)
        for key in ("o","h","l","c","v"):
            record["response"]["payload"][key].append(100)
        raw_body = json.dumps(record["response"]["payload"])
        record["response"].update(raw_body=raw_body, body_sha256=hashlib.sha256(raw_body.encode()).hexdigest())
        self.assertEqual(len(closed_candles(record)),200)

    def test_clock_jump_detected_before_processing(self):
        with patch("cryptoir.runner.time.time_ns",side_effect=[100*NS,102*NS]), patch("cryptoir.runner.time.monotonic_ns",side_effect=[NS,NS+10]):
            clock=SessionClock()
            with self.assertRaises(ClockChanged):
                clock.now()

    def test_os_releases_owner_lock_after_abrupt_process_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"owner.lock"
            code="import os,sys; from pathlib import Path; from cryptoir.runtime import OwnerLock; lock=OwnerLock(Path(sys.argv[1])); lock.__enter__(); os._exit(0)"
            result=subprocess.run([sys.executable,"-c",code,str(path)],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            with OwnerLock(path):
                pass


class RunnerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from websockets.asyncio.server import serve
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)/"paper"
        config_path=Path(__file__).resolve().parents[1]/"examples"/"paper-reference.json"
        self.config=ForwardConfig.from_dict(json.loads(config_path.read_bytes()))

        async def handler(ws):
            try:
                await ws.recv()
                await ws.send('{"id":1,"connect":{}}')
                await ws.recv()
                await ws.send('{"id":2,"subscribe":{"offset":0}}')
                offset=0
                while True:
                    offset+=1
                    message={"push":{"channel":"public:orderbook-BTCUSDT","pub":{"offset":offset,"data":{
                        "lastUpdate":time.time_ns()//1_000_000,"bids":[[99,100]],"asks":[[100,100]]}}}}
                    await ws.send(json.dumps(message))
                    await asyncio.sleep(0.2)
            except Exception:
                return

        self.server=await serve(handler,"127.0.0.1",0)
        port=self.server.sockets[0].getsockname()[1]
        self.urls=patch.dict("cryptoir.streams.URLS",{"nobitex":f"ws://127.0.0.1:{port}"})
        self.poll=patch("cryptoir.runner.candle_response",side_effect=response)
        self.urls.start()
        self.poll.start()

    async def asyncTearDown(self):
        self.urls.stop()
        self.poll.stop()
        self.server.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def test_live_loopback_session_resume_and_raw_audit(self):
        first=await run_paper(self.config,self.directory,5)
        self.assertEqual(first["reason"],"duration_completed")
        self.assertGreater(first["counts"]["nobitex:book"],1)
        self.assertEqual(first["counts"]["nobitex:candle_accepted"],1)
        self.assertEqual(first["warmup_bars"],168)
        self.assertEqual(first["orders"],[])  # An hour delay cannot fill in 5 seconds.
        second=await run_paper(self.config,self.directory,5)
        self.assertEqual(second["forward"]["session_count"],2)
        self.assertEqual(second["balances"],first["balances"])
        self.assertFalse(second["live_execution"])
        for path in self.directory.glob("market-*.gz"):
            self.assertTrue(audit_stream(path)["ended_cleanly"])
        paths = [self.directory/("market-"+run["session_id"]+".jsonl.gz") for run in (first, second)]
        audit = replay(self.config, paths, self.directory/"replay.json")
        self.assertTrue(audit["ledger_parity"])
        self.assertEqual(audit["paper"]["forward"], second["forward"])
        self.assertEqual(audit["sessions"][-1]["closing_ledger_sha256"], second["paper_closing_ledger_sha256"])
        with self.assertRaisesRegex(DataError, "full account session chain"):
            replay(self.config, paths[1:], self.directory/"incomplete.json")

    async def test_consumer_failure_is_terminal_not_a_network_retry(self):
        with patch.object(ForwardEngine,"on_book",side_effect=RuntimeError("accounting failure")):
            with self.assertRaises(ConsumerFailure):
                await run_paper(self.config,self.directory,30)
        status=json.loads((self.directory/"status.json").read_bytes())
        self.assertEqual(status["reason"],"failed")
        self.assertEqual(status["risk"]["halt"],"failed")
        self.assertEqual(status["counts"]["nobitex:session_start"],1)

    async def test_stop_marker_latches_and_prevents_restart(self):
        task=asyncio.create_task(run_paper(self.config,self.directory,30))
        await asyncio.sleep(0.5)
        (self.directory/"STOP").touch()
        async with asyncio.timeout(5):
            report=await task
        self.assertEqual(report["reason"],"manual_stop")
        self.assertEqual(report["risk"]["halt"],"manual_stop")
        with self.assertRaises(DataError):
            await run_paper(self.config,self.directory,5)

    async def test_full_transcript_at_manual_stop_latches_once_without_checkpoint(self):
        from cryptoir.paper import PaperLedger
        from cryptoir.runner import PaperWriter
        from cryptoir.streams import RecordingLimit
        original = PaperWriter.record_input

        def write(writer, action, at, **kwargs):
            if action == "stop":
                raise RecordingLimit("injected log cap")
            return original(writer, action, at, **kwargs)

        with patch.object(PaperWriter, "record_input", write):
            task = asyncio.create_task(run_paper(self.config, self.directory, 30))
            await asyncio.sleep(0.5)
            (self.directory/"STOP").touch()
            async with asyncio.timeout(5):
                report = await task
        self.assertEqual(report["reason"], "manual_stop")
        self.assertEqual(report["risk"]["halt"], "manual_stop")
        self.assertIsNone(report["paper_closing_ledger_sha256"])
        ledger = PaperLedger(self.directory/"paper.sqlite")
        try:
            ended = ledger.db.execute("SELECT count(*) FROM journal WHERE event='forward_session_end'").fetchone()[0]
            self.assertEqual(ended, 1)
        finally:
            ledger.close()
        recording = next(self.directory.glob("market-*.gz"))
        with self.assertRaisesRegex(DataError, "closing ledger"):
            replay(self.config, [recording], self.directory/"failed-replay.json")

    async def test_irt_funded_session_reports_matched_benchmarks(self):
        from dataclasses import asdict
        from decimal import Decimal as D
        from cryptoir.domain import Book,Level
        from cryptoir.runtime import atomic_json
        from cryptoir.streams import dumps
        from cryptoir.valuation import ALL_BOOKS,ConversionRules,create_capital_receipt
        import hashlib
        now=time.time_ns()
        fx=Book("nobitex","USDT/IRR",(Level(999,10000),),(Level(1000,10000),),now,0,now-1_000_000)
        spot=Book("nobitex","BTC/USDT",(Level(99,10000),),(Level(100,10000),),now,0,now-1_000_000)
        spot_rules=ConversionRules(self.config.rules.quantity_step,self.config.rules.minimum_notional,
                                   self.config.rules.fee_rate,self.config.risk.depth_participation)
        receipt=create_capital_receipt(fx,spot,10000,ConversionRules("0.01",1,"0.0025","0.1"),spot_rules,
                                       now_ns=now,max_age_ns=5*NS,source_sha256="fixture")
        config=asdict(self.config)
        config.update(capital_basis="paper_irt_conversion",capital_receipt_sha256=receipt["receipt_sha256"])
        config["risk"]["initial_quote"]=receipt["conversion"]["net_base"]
        self.directory.mkdir()
        atomic_json(self.directory/"capital-receipt.json",receipt)

        def public(url):
            self.assertEqual(url,ALL_BOOKS)
            stamp=time.time_ns()
            payload={"status":"ok"}
            for symbol,bid,ask in (("USDTIRT",999,1000),("BTCUSDT",99,100)):
                payload[symbol]={"lastUpdate":stamp//1_000_000,"bids":[[bid,10000]],"asks":[[ask,10000]]}
            body=dumps(payload)
            return {"schema":1,"kind":"snapshot","url":url,"received_ns":stamp,"duration_ns":0,"payload":payload,
                    "raw_body":body,"body_sha256":hashlib.sha256(body.encode()).hexdigest()}

        with patch("cryptoir.runner.public_response",side_effect=public):
            report=await run_paper(ForwardConfig.from_dict(config),self.directory,5)
        marked=report["matched_valuation"]["last_valuation"]
        self.assertEqual(set(marked["benchmarks"]),{"usdt_hold","btc_hold","irt_cash"})
        self.assertLess(D(marked["portfolio"]["net_return_irt"]),0)
        self.assertEqual(D(marked["excess_return_irt_vs_usdt_hold"]),0)
        self.assertFalse(report["live_execution"])
        for path in self.directory.glob("market-*.gz"):
            audit=audit_stream(path)
            self.assertTrue(audit["ended_cleanly"])
            self.assertEqual(audit["counts"]["portfolio_valuation"],1)


if __name__=="__main__":
    unittest.main()
