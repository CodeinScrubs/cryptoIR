from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from cryptoir.domain import DataError
from cryptoir.order_recovery import DEMO_EPOCH_NS, Intent, RecoveryJournal, synthetic_pages


INTENT = Intent("rehearsal-trader-1", "buy", "0.002", "50000")
OTHER = Intent("rehearsal-trader-2", "buy", "0.002", "50000")


def at(milliseconds):
    return DEMO_EPOCH_NS+milliseconds*1_000_000


def observe(journal, pages, milliseconds=300):
    return journal.observe(INTENT.client_id, pages, started_ns=at(milliseconds)-2,
                           received_ns=at(milliseconds)-1, now_ns=at(milliseconds))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root/"test.sqlite"

    def attempted(self, journal):
        journal.prepare(INTENT, at(100))
        journal.begin_attempt(INTENT.client_id, at(200))

    def test_process_dies_after_attempt_before_receipt_and_never_sends_again(self):
        child = """
import os, sys
from pathlib import Path
from cryptoir.order_recovery import DEMO_EPOCH_NS, Intent, RecoveryJournal, synthetic_pages
from cryptoir.streams import dumps
path = Path(sys.argv[1])
intent = Intent('rehearsal-trader-1', 'buy', '0.002', '50000')
with RecoveryJournal(path, create=True) as journal:
    journal.prepare(intent, DEMO_EPOCH_NS+100_000_000)
    journal.begin_attempt(intent.client_id, DEMO_EPOCH_NS+200_000_000)
    # The synthetic venue receives and immediately completes the order. The
    # process dies without recording the acknowledgement or venue order ID.
    path.with_suffix('.venue.json').write_text(dumps(synthetic_pages(
        intent, status='Done', matched='0.002', with_fill=True)), encoding='utf-8')
    os._exit(74)
"""
        process = subprocess.run([sys.executable, "-c", child, str(self.path)], capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 74, process.stderr.decode(errors="replace"))
        with RecoveryJournal(self.path) as journal:
            self.assertEqual(journal.report()["intents"][0]["state"], "UNCERTAIN")
            self.assertEqual(observe(journal, synthetic_pages(INTENT))["state"], "UNCERTAIN")
            with self.assertRaisesRegex(DataError, "cannot be repeated"):
                journal.begin_attempt(INTENT.client_id, at(301))
            with self.assertRaisesRegex(DataError, "unresolved intent"):
                journal.prepare(OTHER, at(301))
            evidence = json.loads(self.path.with_suffix(".venue.json").read_bytes())
            self.assertEqual(observe(journal, evidence, 310)["state"], "RESOLVED")
            self.assertEqual(journal.report()["attempts_committed"], 1)
            self.assertEqual(journal.report()["intents"][0]["exchange_order_id"], 101)
            self.assertEqual(journal.prepare(OTHER, at(311))["state"], "PREPARED")

    def test_two_writers_get_only_one_durable_attempt(self):
        with RecoveryJournal(self.path, create=True) as journal:
            journal.prepare(INTENT, at(100))
        gate = Barrier(2)

        def attempt():
            with RecoveryJournal(self.path) as journal:
                gate.wait(timeout=5)
                try:
                    journal.begin_attempt(INTENT.client_id, at(200))
                except DataError:
                    return False
                return True

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: attempt(), range(2))), [False, True])
        with RecoveryJournal(self.path) as journal:
            self.assertEqual(journal.report()["attempts_committed"], 1)

    def test_partial_cancel_requires_exact_fill_evidence(self):
        with RecoveryJournal(self.path, create=True) as journal:
            self.attempted(journal)
            self.assertEqual(observe(journal, synthetic_pages(INTENT, status="Active", matched="0.001"))["state"], "OPEN")
            terminal = synthetic_pages(INTENT, status="Canceled", matched="0.001")
            self.assertEqual(observe(journal, terminal, 400)["state"], "TERMINAL_NEEDS_FILLS")
            with self.assertRaisesRegex(DataError, "unresolved intent"):
                journal.prepare(OTHER, at(410))
            self.assertEqual(observe(journal, synthetic_pages(INTENT), 500)["state"], "TERMINAL_NEEDS_FILLS")
            terminal = synthetic_pages(INTENT, status="Canceled", matched="0.001", with_fill=True)
            self.assertEqual(observe(journal, terminal, 600)["state"], "RESOLVED")
            with self.assertRaisesRegex(DataError, "cannot be repeated"):
                journal.begin_attempt(INTENT.client_id, at(601))

    def test_mutated_identity_prices_and_source_times_remain_blocked(self):
        cases = [
            ("order", "price", "50001", "observed_order_differs_from_intent"),
            ("order", "created_at", "2026-01-01T00:00:00.100Z", "order_creation_outside_attempt_window"),
            ("fill", "timestamp", "2026-01-01T00:00:00.310Z", "fill_time_outside_observation_window"),
            ("fill", "price", "50001", "fill_violates_intent_limit"),
            ("fill", "amount", "0.001001", "observed_fills_exceed_order_match"),
            ("fill", "type", "sell", "side_disagreement"),
        ]
        for i, (kind, key, value, reason) in enumerate(cases):
            with self.subTest(reason=reason), RecoveryJournal(self.root/f"case-{i}.sqlite", create=True) as journal:
                self.attempted(journal)
                pages = synthetic_pages(INTENT, status="Canceled", matched="0.001", with_fill=True)
                row = pages["recent_spot_orders"]["orders"][0] if kind == "order" else pages["recent_spot_fills"]["trades"][0]
                row[key] = value
                self.assertEqual(observe(journal, pages), {"state": "CONFLICT", "reason": reason})
                with self.assertRaisesRegex(DataError, "unresolved intent"):
                    journal.prepare(OTHER, at(301))
                with self.assertRaisesRegex(DataError, "not awaiting"):
                    observe(journal, synthetic_pages(INTENT), 400)

    def test_duplicate_venue_ids_and_nonatomic_order_change_remain_blocking(self):
        with RecoveryJournal(self.path, create=True) as journal:
            self.attempted(journal)
            pages = synthetic_pages(INTENT, status="Active", matched="0.001")
            pages["recent_spot_orders"]["orders"][0] = dict(pages["recent_spot_orders"]["orders"][0], status="Canceled")
            self.assertEqual(observe(journal, pages)["reason"], "order_changed_between_nonatomic_reads")
            self.assertEqual(journal.report()["intents"][0]["state"], "UNCERTAIN")
            pages["recent_spot_orders"]["orders"][0]["id"] = 102
            self.assertEqual(observe(journal, pages, 400)["state"], "CONFLICT")

    def test_unrelated_or_unavailable_evidence_and_clock_errors_cannot_clear(self):
        with RecoveryJournal(self.path, create=True) as journal:
            self.attempted(journal)
            history = synthetic_pages(OTHER, status="Done", matched="0.002", with_fill=True)
            self.assertEqual(observe(journal, history)["state"], "UNCERTAIN")
            candidate = synthetic_pages(INTENT, status="Canceled")
            with self.assertRaisesRegex(DataError, "overlaps"):
                observe(journal, candidate)
            with self.assertRaisesRegex(DataError, "stale"):
                journal.observe(INTENT.client_id, candidate, started_ns=at(400), received_ns=at(410), now_ns=at(5500))
            del candidate["recent_spot_fills"]
            with self.assertRaisesRegex(DataError, "three"):
                observe(journal, candidate, 500)
            self.assertEqual(journal.report()["intents"][0]["state"], "UNCERTAIN")
            # No observation time or venue identity can move backwards.
            with self.assertRaisesRegex(DataError, "clock regressed"):
                journal.prepare(INTENT, at(299))

    def test_positive_confirmation_and_binding_commit_with_evidence_digest(self):
        with RecoveryJournal(self.path, create=True) as journal:
            self.attempted(journal)
            pages = synthetic_pages(INTENT, status="Canceled")
            with patch.object(journal, "_event", side_effect=RuntimeError("synthetic journal failure")):
                with self.assertRaisesRegex(RuntimeError, "journal failure"):
                    observe(journal, pages)
            self.assertIsNone(journal.report()["intents"][0]["exchange_order_id"])
            self.assertEqual(observe(journal, pages)["state"], "RESOLVED")
            evidence = journal.db.execute("SELECT details FROM journal WHERE kind='observed'").fetchone()
            self.assertEqual(len(json.loads(evidence[0])["evidence_sha256"]), 64)

    def test_demo_cli_writes_exclusive_synthetic_artifacts(self):
        output = self.root/"demo"
        args = [sys.executable, "-m", "cryptoir", "order-recovery-demo", "--directory", str(output)]
        first = subprocess.run(args, capture_output=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr.decode(errors="replace"))
        result = json.loads((output/"report.json").read_bytes())
        self.assertEqual([x["state"] for x in result["observations"]],
                         ["UNCERTAIN", "OPEN", "TERMINAL_NEEDS_FILLS", "RESOLVED"])
        self.assertFalse(result["live_trading_enabled"])
        second = subprocess.run(args, capture_output=True, timeout=10)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(json.loads((output/"report.json").read_bytes()), result)
