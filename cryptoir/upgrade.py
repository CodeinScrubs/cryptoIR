"""Explicit, backup-first code upgrade for an untouched forward paper account."""

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .domain import DataError
from .forward import ForwardConfig, ForwardEngine, source_fingerprint
from .paper import PaperLedger
from .runtime import OwnerLock
from .streams import dumps


def upgrade_empty_account(directory: Path, config: ForwardConfig) -> dict:
    path=directory/"paper.sqlite"
    if not path.is_file():
        raise DataError("no existing paper ledger")
    with OwnerLock(directory/"owner.lock"):
        ledger=PaperLedger(path)
        try:
            if ledger.db.execute("SELECT 1 FROM orders").fetchone():
                raise DataError("automatic source upgrade supports accounts with no orders only")
            rows=ledger.db.execute("SELECT details FROM journal WHERE event='forward_initialized' ORDER BY seq").fetchall()
            if len(rows)!=1 or json.loads(rows[0][0])["configuration"]!=json.loads(dumps(asdict(config))):
                raise DataError("source upgrade must preserve the original forward configuration")
            state_row=ledger.db.execute("SELECT value FROM metadata WHERE key='risk_state'").fetchone()
            if state_row is None or json.loads(state_row[0])["halt"] is not None:
                raise DataError("source upgrade must not clear a risk halt")
            if ledger.balance(config.venue,"USDT")!=(config.risk.initial_quote,0):
                raise DataError("source upgrade requires unchanged initial quote balance")
            for row in ledger.db.execute("SELECT * FROM balances"):
                if row["venue"]!=config.venue or row["asset"]!="USDT":
                    raise DataError("unexpected inventory in untouched paper account")
            previous=ledger.db.execute("SELECT value FROM metadata WHERE key='forward_identity'").fetchone()
            if previous is None:
                raise DataError("no sealed forward identity")
            current=hashlib.sha256(dumps({"config":asdict(config),"source":source_fingerprint()}).encode()).hexdigest()
            if previous[0]==current:
                return {"kind":"paper_source_upgrade","status":"already_current","capital_preserved":True}
            backup=directory/("paper-before-source-upgrade-"+uuid.uuid4().hex+".sqlite")
            with backup.open("xb"):
                pass
            destination=sqlite3.connect(backup)
            try:
                ledger.db.backup(destination)
            finally:
                destination.close()
            with ledger.db:
                ledger.db.execute("BEGIN IMMEDIATE")
                ledger.db.execute("UPDATE metadata SET value=? WHERE key='forward_identity'",(current,))
                ledger._event("forward_source_upgraded",previous_identity=previous[0],new_identity=current,
                              backup=backup.name,now_ns=time.time_ns(),capital_reset=False,orders_at_upgrade=0)
            ForwardEngine(ledger,config)  # Verify the unchanged account can reopen.
            return {"kind":"paper_source_upgrade","status":"upgraded_with_backup","capital_preserved":True,
                    "orders_preserved":True,"old_identity":previous[0],"new_identity":current,"backup":backup.name}
        finally:
            ledger.close()
