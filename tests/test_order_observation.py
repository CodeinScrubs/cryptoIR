from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptoir import accounts, order_observation as obs
from test_accounts import NOB, NOW, WAL


OPERATIONS = tuple(accounts.NOBITEX_ORDER_READS)


def order(identity=42, **changes):
    return {"id": identity, "srcCurrency": "Bitcoin", "dstCurrency": "Tether", "market": "BTC-USDT",
            "status": "Active", "execution": "Limit", "tradeType": "Spot", "type": "buy",
            "amount": "0.004", "matchedAmount": "0.003", "fee": "0.0000003", **changes}


def fill(identity=30, **changes):
    return {"id": identity, "orderId": 42, "srcCurrency": "Bitcoin", "dstCurrency": "Tether", "market": "BTC-USDT",
            "timestamp": "2026-11-23T04:11:12.137Z", "type": "buy", "amount": "0.001",
            "price": "50000.01", "total": "50.00001", "fee": "0.0000001", **changes}


def page(name, rows, **fields):
    key = "trades" if name == "recent_spot_fills" else "orders"
    return obs.parse_page(name, {"status": "ok", key: rows, **fields})


def sample_pages(open_rows=None, recent_rows=None, fills=None):
    return dict(zip(OPERATIONS, [page(OPERATIONS[0], [order()] if open_rows is None else open_rows),
        page(OPERATIONS[1], [order()] if recent_rows is None else recent_rows),
        page(OPERATIONS[2], [fill(), fill(31, amount="0.002")] if fills is None else fills)]))


class OrderObservationTests(unittest.TestCase):
    def test_fixed_read_queries_are_signed_exactly_and_never_available_for_other_venues(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        public = Ed25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(NOB.secret)).public_key()
        for operation in OPERATIONS:
            request = accounts.build_read_request("nobitex", operation, NOB, now_ns=NOW)
            self.assertEqual(request.get_method(), "GET")
            self.assertIsNone(request.data)
            self.assertEqual(request.host, "apiv2.nobitex.ir")
            self.assertTrue(request.selector.startswith("/market/"))
            self.assertIn("srcCurrency=btc&dstCurrency=usdt", request.selector)
            self.assertIn("page=1&pageSize=10", request.selector)
            headers = {k.lower(): v for k, v in request.header_items()}
            signature = base64.urlsafe_b64decode(headers["nobitex-signature"])
            public.verify(signature, (str(NOW//1_000_000_000)+"GET"+request.selector).encode())
            with self.assertRaisesRegex(accounts.AccountReadError, "allowlist"):
                accounts.build_read_request("wallex", operation, WAL, now_ns=NOW)
        self.assertEqual(tuple(accounts.READS["nobitex"]), ("profile", "balances"))
        for mutation in ("add", "cancel", "/market/orders/update-status", "open_spot_orders&page=2"):
            with self.assertRaisesRegex(accounts.AccountReadError, "allowlist"):
                accounts.build_read_request("nobitex", mutation, NOB, now_ns=NOW)

    def test_matches_multiple_partial_fills_exactly_and_preserves_unknown_fields(self):
        pages = sample_pages()
        self.assertIsNone(pages[OPERATIONS[0]]["has_next"])
        self.assertIsNone(pages[OPERATIONS[2]]["rows"][0]["fee_asset"])
        result = obs.reconcile(pages)
        self.assertEqual(result["status"], "matched_observed_quantities")
        row = result["order_observations"][0]
        self.assertEqual(row["observed_fill_base_total"], Decimal("0.003"))
        self.assertEqual(row["observed_fill_ids"], [30, 31])
        self.assertTrue(page(OPERATIONS[2], [fill()], hasNext=True)["has_next"])
        self.assertIsNone(page(OPERATIONS[1], [order(tradeType=None)])["rows"][0]["observed_trade_type"])

    def test_differences_and_no_overlap_are_distinguished(self):
        for pages, reason, overall in (
            (sample_pages(recent_rows=[order(status="Canceled")]), "order_changed_between_reads", "discrepancy_or_change"),
            (sample_pages(fills=[fill(type="sell")]), "side_disagreement", "discrepancy_or_change"),
            (sample_pages(fills=[fill(amount="0.003000000000000000000001")]), "observed_fills_exceed_order_match", "discrepancy_or_change"),
            (sample_pages(fills=[fill()]), "observed_fills_below_order_match", "partial_observed_coverage"),
            (sample_pages(open_rows=[], recent_rows=[], fills=[fill()]), "order_not_in_observed_pages", "partial_observed_coverage"),
        ):
            with self.subTest(reason=reason):
                result = obs.reconcile(pages)
                self.assertEqual(result["status"], overall)
                self.assertEqual(result["order_observations"][0]["status"], reason)
        self.assertEqual(obs.reconcile(sample_pages(open_rows=[], recent_rows=[], fills=[]))["status"], "no_fill_evidence")
        self.assertEqual(obs.reconcile({})["status"], "incomplete_reads")

    def test_out_of_scope_unknown_invalid_quantities_and_duplicates_are_rejected(self):
        for row in (order(status="Unknown"), order(status="Done"), order(matchedAmount="0.005"),
                    order(tradeType="Margin"), order(dstCurrency="﷼"), order(market="ETH-USDT"), order(True)):
            with self.subTest(row=row), self.assertRaises(accounts.AccountReadError):
                page(OPERATIONS[1], [row])
        for rows in ([fill(), fill()], [fill(timestamp="2026-11-23T04:11:12")], [fill(amount="0")],
                     [fill(orderId=True)], [fill(type="invalid")]):
            with self.assertRaises(accounts.AccountReadError):
                page(OPERATIONS[2], rows)
        with self.assertRaises(accounts.AccountReadError):
            page(OPERATIONS[0], [order(status="Canceled")])
        with self.assertRaises(accounts.AccountReadError):
            page(OPERATIONS[1], [order(i) for i in range(1, 12)])

    def test_probe_continues_on_failed_page_and_remains_unreconciled(self):
        with patch.object(accounts, "read_operation", side_effect=[({"status": "ok", "orders": [order()]}, {}),
                accounts.AccountReadError("http_403"), ({"status": "ok", "trades": [fill()]}, {})]) as request:
            report = obs.inspect_orders(NOB)
        self.assertEqual([call.args[1] for call in request.call_args_list], list(OPERATIONS))
        self.assertEqual([row["status"] for row in report["results"]], ["accepted", "unavailable", "accepted"])
        self.assertEqual(report["reconciliation"]["status"], "incomplete_reads")
        self.assertFalse(report["complete_order_history"])
        self.assertFalse(report["local_live_intents_reconciled"])

    def test_saved_key_private_report_and_redacted_cli(self):
        raw_order = order(123498, clientOrderId="private-personal-reference")
        replies = [({"status": "ok", "orders": [raw_order]}, {}),
                   ({"status": "ok", "orders": [raw_order]}, {}), ({"status": "ok", "trades": []}, {})]
        terminal = StringIO()
        with tempfile.TemporaryDirectory() as temporary, patch.object(accounts, "__file__", str(Path(temporary)/"cryptoir/accounts.py")), \
                patch("cryptoir.credentials.load_credentials", return_value=NOB), \
                patch.object(accounts, "read_operation", side_effect=replies), redirect_stdout(terminal):
            self.assertEqual(obs.order_check(), 0)
            summary = json.loads(terminal.getvalue())
            path = Path(temporary)/summary["private_report"]
            private = json.loads(path.read_bytes())
            self.assertIsNotNone(private["results"][0]["data"]["rows"][0]["client_order_id_sha256"])
            self.assertNotIn("private-personal-reference", path.read_text())
            self.assertNotIn("123498", terminal.getvalue())
            self.assertNotIn(NOB.key, terminal.getvalue())
            self.assertFalse(summary["live_trading_enabled"])
        with patch("cryptoir.credentials.load_credentials", return_value=None), \
                patch.object(accounts, "read_operation") as request, redirect_stdout(StringIO()):
            self.assertEqual(obs.order_check(), 1)
            request.assert_not_called()
