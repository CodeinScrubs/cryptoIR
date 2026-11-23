import tempfile
import unittest
from dataclasses import asdict, replace
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from cryptoir.domain import Book, DataError, Level
from cryptoir.forward import ForwardConfig, ForwardEngine, NS
from cryptoir.history import Candle, HOUR
from cryptoir.paper import PaperLedger, Rules
from cryptoir.risk import RiskLimits
from cryptoir.runtime import OwnerLock, atomic_json


CONFIG=ForwardConfig("nobitex","BTC/USDT","hold",
                     Rules(D("0.001"),D("0.01"),D(1),D(500),D("0.001"),"received_base"),
                     RiskLimits(D(1000),D("0.25"),D("0.05"),D("0.1"),D(250),1,5*NS,D(1)),
                     3600,300,100,5,3900,D(15),D("0.01"))


def book(now, bid=99, ask=100):
    return Book("nobitex","BTC/USDT",(Level(bid,100),),(Level(ask,100),),now,0,now-1_000_000)


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"paper.sqlite"
        self.ledger=PaperLedger(self.path)
        self.ledger.seed({("nobitex","USDT"):D(1000)})
        self.engine=ForwardEngine(self.ledger,CONFIG,source_id="fixture-v1")
        self.rows=[Candle((1000+i)*HOUR,100,100,100,100,100) for i in range(200)]
        self.now=(self.rows[-1].time+HOUR)*NS+120*NS
        self.engine.start(self.now)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def warmup(self):
        self.engine.apply_candles(self.rows,received_ns=self.now,now_ns=self.now)

    def execute(self):
        self.warmup()
        due=self.now+3600*NS
        self.engine.on_book(book(due),now_ns=due)
        self.engine.on_book(book(due+200_000_000),now_ns=due+200_000_000)
        return due+200_000_000

    def test_config_roundtrip_and_live_mode_refused(self):
        self.assertEqual(ForwardConfig.from_dict(asdict(CONFIG)),CONFIG)
        with self.assertRaises(DataError):
            replace(CONFIG,mode="live")
        with self.assertRaises(DataError):
            replace(CONFIG,decision_delay_seconds=0)

    def test_warmup_never_generates_historical_orders(self):
        self.warmup()
        report=self.engine.report()
        self.assertEqual(len(report["orders"]),0)
        self.assertEqual(report["warmup_bars"],168)
        self.assertEqual(report["forward"]["decision"]["observed_ns"],self.now)

    def test_decision_delay_then_next_snapshot_latency(self):
        self.warmup()
        self.engine.on_book(book(self.now+3599*NS),now_ns=self.now+3599*NS)
        self.assertEqual(self.ledger.report()["orders"],[])
        due=self.now+3600*NS
        self.engine.on_book(book(due),now_ns=due)
        self.assertEqual(self.ledger.report()["orders"][0]["status"],"PENDING")
        self.engine.on_book(book(due+200_000_000),now_ns=due+200_000_000)
        self.assertEqual(self.ledger.report()["orders"][0]["status"],"FILLED")
        self.assertGreater(self.ledger.balance("nobitex","BTC")[0],0)

    def test_unclosed_candle_blocks_decisions(self):
        future=Candle(self.rows[-1].time+HOUR,100,100,100,100,100)
        self.engine.apply_candles(self.rows+[future],received_ns=self.now,now_ns=self.now)
        self.assertFalse(self.engine.state()["candles_ok"])
        self.assertEqual(self.engine.state()["candle_reason"],"unclosed_candle")

    def test_revision_latches_halt_without_rewriting_signal_history(self):
        self.warmup()
        changed=list(self.rows)
        changed[-1]=replace(changed[-1],volume=D(101))
        self.engine.apply_candles(changed,received_ns=self.now+NS,now_ns=self.now+NS)
        self.assertEqual(self.engine.guard.state()["halt"],"closed_candle_revision")
        self.assertEqual(self.engine.signal.bars[-1].volume,100)

    def test_gap_is_not_forward_filled(self):
        self.warmup()
        later=Candle(self.rows[-1].time+3*HOUR,100,100,100,100,100)
        now=(later.time+HOUR)*NS+120*NS
        self.engine.apply_candles([later],received_ns=now,now_ns=now)
        self.assertEqual(self.engine.state()["candle_reason"],"candle_gap")
        self.assertEqual(self.engine.signal.bars[-1],self.rows[-1])

    def test_missed_decision_expires_without_fill(self):
        self.warmup()
        later=self.now+4000*NS
        self.engine.on_book(book(later),now_ns=later)
        self.assertEqual(self.ledger.report()["orders"],[])
        self.assertIsNone(self.engine.state()["decision"])

    def test_stop_and_restart_keep_holdings_without_refill(self):
        now=self.execute()
        balances=self.ledger.report()["balances"]
        self.engine.stop(now+NS)
        self.ledger.close()
        self.ledger=PaperLedger(self.path)
        self.engine=ForwardEngine(self.ledger,CONFIG,source_id="fixture-v1")
        self.engine.start(now+2*NS)
        self.assertEqual(self.ledger.report()["balances"],balances)
        self.assertIsNone(self.engine.state()["decision"])
        self.assertEqual(self.engine.state()["session_count"],2)

    def test_restart_cancels_pending_order_instead_of_gap_fill(self):
        self.warmup()
        due=self.now+3600*NS
        self.engine.on_book(book(due),now_ns=due)
        self.engine.start(due+NS)
        self.engine.on_book(book(due+2*NS),now_ns=due+2*NS)
        self.assertEqual(self.ledger.report()["orders"][0]["status"],"CANCELED")
        self.assertEqual(self.ledger.balance("nobitex","BTC")[0],0)

    def test_crash_after_submission_does_not_submit_again(self):
        self.warmup()
        due=self.now+3600*NS
        with patch.object(self.engine,"_finish_decision",side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.engine.on_book(book(due),now_ns=due)
        self.assertEqual(len(self.ledger.report()["orders"]),1)
        self.engine.on_book(book(due+200_000_000),now_ns=due+200_000_000)
        self.assertEqual(len(self.ledger.report()["orders"]),1)
        self.assertIsNone(self.engine.state()["decision"])

    def test_source_or_config_change_refuses_resume(self):
        with self.assertRaises(DataError):
            ForwardEngine(self.ledger,CONFIG,source_id="different")
        with self.assertRaises(DataError):
            ForwardEngine(self.ledger,replace(CONFIG,strategy="cash"),source_id="fixture-v1")

    def test_processing_delay_uses_processing_clock_for_freshness(self):
        self.warmup()
        due=self.now+3600*NS
        self.engine.on_book(book(due),now_ns=due+6*NS)
        self.assertFalse(self.engine.guard.state()["data_ok"])
        self.assertEqual(self.ledger.report()["orders"],[])

    def test_replayed_book_cannot_dispatch_a_due_decision(self):
        self.warmup()
        due = self.now+3600*NS
        first = book(due-100_000_000)
        self.engine.on_book(first, now_ns=first.received_ns)
        self.engine.on_book(replace(first, received_ns=due), now_ns=due)
        self.assertEqual(self.ledger.report()["orders"], [])
        self.assertEqual(self.engine.latest_book, first)
        self.engine.on_book(book(due+100_000_000), now_ns=due+100_000_000)
        self.assertEqual(self.ledger.report()["orders"][0]["status"], "PENDING")

    def test_no_repeated_entry_when_target_is_already_owned(self):
        now=self.execute()
        for offset in (NS,2*NS,3*NS):
            self.engine.on_book(book(now+offset),now_ns=now+offset)
        self.assertEqual(len(self.ledger.report()["orders"]),1)


class RuntimeTests(unittest.TestCase):
    def test_owner_lock_is_exclusive_and_stale_filename_is_harmless(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"owner.lock"
            path.write_text("old pid is irrelevant")
            with OwnerLock(path):
                with self.assertRaises(DataError):
                    with OwnerLock(path):
                        pass
            with OwnerLock(path):
                pass
            self.assertTrue(path.exists())

    def test_atomic_status_replacement(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"status.json"
            atomic_json(path,{"generation":1})
            atomic_json(path,{"generation":2})
            self.assertEqual(json.loads(path.read_text()),{"generation":2})
            self.assertEqual(len(list(Path(tmp).iterdir())),1)


if __name__ == "__main__":
    unittest.main()
