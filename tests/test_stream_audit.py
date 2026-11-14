import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptoir.domain import DataError
from cryptoir.stream_audit import audit_stream, events, replay_books
from cryptoir.streams import EventWriter


class StreamAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "events.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def fixture(self, path=None):
        path = path or self.path
        writer = EventWriter(path, 100000)
        try:
            writer.write("all", {"kind": "recording_start", "venues": ["tabdeal"]})
            writer.write("tabdeal", {"kind": "session_start", "pair": "BTC/USDT"})
            raw = json.dumps({"stream": "btcusdt@depth@2000ms", "data": {"s": "BTCUSDT",
                "e": "depthUpdate", "E": 1000, "b": [["99", "1"]], "a": [["100", "1"]]}})
            writer.write("tabdeal", {"kind": "frame", "received_ns": 1_000_000_000,
                                     "raw": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()})
            # Deliberately false derived data must never influence replay.
            writer.write("tabdeal", {"kind": "book", "book": {"fabricated": "profit"}})
            writer.write("all", {"kind": "recording_end", "reason": "duration_completed"})
        finally:
            writer.close()

    def test_replay_uses_original_frames_only(self):
        self.fixture()
        books = [e["book"] for e in replay_books(self.path) if e["kind"] == "book"]
        self.assertEqual(len(books), 1)
        self.assertEqual(str(books[0].bids[0].price), "99")
        report = audit_stream(self.path)
        self.assertTrue(report["ended_cleanly"])
        self.assertFalse(report["market_history_complete"])

    def test_gzip_preserves_audit(self):
        path = Path(self.temp.name) / "events.jsonl.gz"
        self.fixture(path)
        self.assertEqual(audit_stream(path)["counts"]["tabdeal:replayed_book"], 1)

    def test_hash_tampering_rejected(self):
        self.fixture()
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        rows[2]["raw"] += " "
        self.path.write_text("\n".join(json.dumps(row) for row in rows))
        with self.assertRaises(DataError):
            list(events(self.path))

    def test_missing_sequence_rejected(self):
        self.fixture()
        lines = self.path.read_text().splitlines()
        self.path.write_text("\n".join(lines[:2] + lines[3:]))
        with self.assertRaises(DataError):
            audit_stream(self.path)

    def test_unfinished_recording_not_clean(self):
        self.fixture()
        lines = self.path.read_text().splitlines()
        self.path.write_text("\n".join(lines[:-1]))
        self.assertFalse(audit_stream(self.path)["ended_cleanly"])

    def test_events_after_end_rejected(self):
        self.fixture()
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        extra = {**rows[-1], "seq": 6, "kind": "heartbeat"}
        self.path.write_text("\n".join(json.dumps(row) for row in [*rows, extra]))
        with self.assertRaises(DataError):
            audit_stream(self.path)


if __name__ == "__main__":
    unittest.main()
