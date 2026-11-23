import gzip
import json
from hashlib import sha256
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptoir.domain import DataError
from cryptoir.forward import ForwardEngine, NS, source_fingerprint
from cryptoir.history import HOUR
from cryptoir.paper import PaperLedger
from cryptoir.paper_replay import config_digest, ledger_digest, replay
from cryptoir.runner import PaperWriter
from cryptoir.stream_audit import events
from cryptoir.streams import EventWriter, StreamParser, dumps
from test_forward import CONFIG
from test_runner import response


class Timeline:
    def __init__(self, start):
        self.at = start

    def now(self):
        return self.at


class PaperReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recording = self.root/"market.jsonl.gz"

    def record(self):
        # Use production writer/engine and raw feed layout with a virtual clock.
        start = (1200*HOUR + 120)*NS
        clock = Timeline(start)
        venue, pair = CONFIG.venue, CONFIG.pair
        parser = StreamParser(venue, pair)
        ledger = PaperLedger(self.root/"paper.sqlite")
        self.addCleanup(ledger.close)
        ledger.seed({(venue, "USDT"): CONFIG.risk.initial_quote})
        code = source_fingerprint()
        engine = ForwardEngine(ledger, CONFIG, source_id=code)

        def publish(at, offset):
            clock.at = at
            raw = dumps({"push": {"channel": "public:orderbook-BTCUSDT", "pub": {"offset": offset,
                "data": {"lastUpdate": (at-1_000_000)//1_000_000, "asks": [[100, 100]], "bids": [[99, 100]]}}}})
            writer.write(venue, {"kind": "frame", "pair": pair, "attempt": 1,
                                  "raw": raw, "sha256": sha256(raw.encode()).hexdigest(), "received_ns": at})
            for event in parser.parse(json.loads(raw), at):
                writer.write(venue, {"pair": pair, "attempt": 1, **event})

        with patch("cryptoir.streams.time.time_ns", side_effect=clock.now), \
                patch("cryptoir.streams.time.monotonic_ns", side_effect=clock.now):
            writer = PaperWriter(self.recording, 1024*1024, engine, clock)
            try:
                writer.write("all", {"kind": "recording_start", "purpose":"experimental_forward_paper",
                    "pair": pair, "venues": [venue], "live_execution": False, "paper_input_version": 1,
                    "source_sha256": code, "config_sha256": config_digest(CONFIG),
                    "opening_ledger_sha256": ledger_digest(ledger)})
                writer.input("start")
                writer.write(venue, {"kind": "session_start", "pair": pair, "attempt": 1})
                writer.write(venue, {"kind": "candle_response", "pair": pair, **response(CONFIG, None, start)})
                # No decision before the candle-derived delay or fill before order latency.
                due = start+3600*NS
                publish(due-NS, 1)
                self.assertEqual(ledger.report()["orders"], [])
                publish(due, 2)
                publish(due+50_000_000, 3)
                self.assertEqual(ledger.report()["orders"][0]["status"], "PENDING")
                publish(due+200_000_000, 4)
                self.assertEqual(ledger.report()["orders"][0]["status"], "FILLED")
                clock.at += NS
                writer.input("advance")
                writer.input("stop", reason="duration_completed")
                EventWriter.write(writer, "all", {"kind": "recording_end", "reason": "duration_completed",
                                                   "closing_ledger_sha256": ledger_digest(ledger)})
            finally:
                writer.close()
        return engine.report(), ledger_digest(ledger)

    def test_exact_fill_replay_at_cli(self):
        online, expected_digest = self.record()
        config = self.root/"config.json"
        from dataclasses import asdict
        config.write_text(dumps(asdict(CONFIG)), encoding="utf-8")
        output = self.root/"report.json"
        result = subprocess.run([sys.executable, "-m", "cryptoir", "paper-replay", "--config", str(config),
                                 "--recording", str(self.recording), "--output", str(output)],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        report = json.loads(output.read_bytes())
        self.assertTrue(report["ledger_parity"])
        self.assertEqual(report["sessions"][0]["closing_ledger_sha256"], expected_digest)
        self.assertEqual(report["paper"]["orders"], online["orders"])
        self.assertEqual(report["paper"]["balances"], online["balances"])
        with self.assertRaises(FileExistsError):
            replay(CONFIG, [self.recording], output)

    def mutate(self, name):
        rows = list(events(self.recording, strict=True))
        if name == "book":
            book = next(row["book"] for row in rows if row["kind"] == "book")
            book["asks"][0]["quantity"] = "101"
        elif name == "frame":
            frame = next(row for row in rows if row["kind"] == "frame")
            frame["sha256"] = "0"*64
        elif name == "future":
            action = next(row for row in rows if row.get("action") == "candle_response")
            action["now_ns"] -= NS
        elif name == "candle":
            row = next(row for row in rows if row["kind"] == "candle_response")
            row["response"]["payload"]["v"][-1] = 5000
        elif name == "source":
            rows[0]["source_sha256"] = "0"*64
        elif name == "closing":
            rows[-1]["closing_ledger_sha256"] = "0"*64
        elif name == "legacy":
            del rows[0]["paper_input_version"]
        elif name == "footer":
            rows.pop()
        path = self.root/(name+".jsonl.gz")
        with gzip.open(path, "xt", encoding="utf-8") as target:
            for row in rows:
                target.write(dumps(row)+"\n")
        return path

    def test_tampering_lookahead_and_incomplete_recordings_fail_closed(self):
        self.record()
        for mutation, message in (("book", "differs from original frame"), ("frame", "frame hash mismatch"),
                                  ("future", "input clock"), ("candle", "payload differs from raw"),
                                  ("source", "source differs"), ("closing", "closing ledger"),
                                  ("legacy", "lacks exact paper inputs"), ("footer", "closing checkpoint")):
            with self.subTest(mutation=mutation):
                output = self.root/(mutation+".json")
                with self.assertRaisesRegex(DataError, message):
                    replay(CONFIG, [self.mutate(mutation)], output)
                self.assertFalse(output.exists())
