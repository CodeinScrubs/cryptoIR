import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptoir.domain import DataError
from cryptoir.forward import ForwardConfig,ForwardEngine
from cryptoir.paper import PaperLedger
from cryptoir.runtime import OwnerLock
from cryptoir.upgrade import upgrade_empty_account


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)
        self.path=self.directory/"paper.sqlite"
        self.config=ForwardConfig.from_dict(json.loads((Path(__file__).resolve().parents[1]/"examples"/"paper-reference.json").read_bytes()))
        ledger=PaperLedger(self.path)
        ledger.seed({("nobitex","USDT"):self.config.risk.initial_quote})
        engine=ForwardEngine(ledger,self.config,source_id="older-version")
        engine.start(1000000000)
        engine.stop(2000000000)
        self.before=ledger.report()["balances"]
        ledger.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_upgrade_backs_up_and_preserves_cash_and_history(self):
        result=upgrade_empty_account(self.directory,self.config)
        self.assertEqual(result["status"],"upgraded_with_backup")
        backup=sqlite3.connect(self.directory/result["backup"])
        try:
            self.assertEqual(backup.execute("SELECT value FROM metadata WHERE key='forward_identity'").fetchone()[0],result["old_identity"])
        finally:
            backup.close()
        ledger=PaperLedger(self.path)
        try:
            engine=ForwardEngine(ledger,self.config)
            self.assertEqual(engine.state()["session_count"],1)
            self.assertEqual(ledger.report()["balances"],self.before)
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM journal WHERE event='forward_source_upgraded'").fetchone()[0],1)
        finally:
            ledger.close()
        self.assertEqual(upgrade_empty_account(self.directory,self.config)["status"],"already_current")

    def test_halt_is_not_reset_by_upgrade(self):
        ledger=PaperLedger(self.path)
        try:
            state=json.loads(ledger.db.execute("SELECT value FROM metadata WHERE key='risk_state'").fetchone()[0])
            state["halt"]="manual_stop"
            with ledger.db:
                ledger.db.execute("UPDATE metadata SET value=? WHERE key='risk_state'",(json.dumps(state),))
        finally:
            ledger.close()
        with self.assertRaisesRegex(DataError,"risk halt"):
            upgrade_empty_account(self.directory,self.config)

    def test_account_with_any_order_requires_another_upgrade_process(self):
        ledger=PaperLedger(self.path)
        try:
            ledger.submit("prior","nobitex","BTC/USDT","buy","0.1","100",self.config.rules,
                          now_ns=3000000000,latency_ns=100000000,ttl_ns=1000000000)
            ledger.cancel("prior")
        finally:
            ledger.close()
        with self.assertRaisesRegex(DataError,"no orders"):
            upgrade_empty_account(self.directory,self.config)

    def test_cannot_upgrade_active_owner(self):
        with OwnerLock(self.directory/"owner.lock"):
            with self.assertRaisesRegex(DataError,"active owner"):
                upgrade_empty_account(self.directory,self.config)


if __name__=="__main__":
    unittest.main()
