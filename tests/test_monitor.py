import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

from cryptoir.dashboard import DashboardServer
from cryptoir.domain import DataError
from cryptoir.monitor import dashboard_snapshot, read_status
from cryptoir.runtime import OwnerLock, atomic_json
from test_valuation import books, FX, SPOT
from cryptoir.valuation import create_capital_receipt, matched_valuation
from dataclasses import asdict

NS = 1_000_000_000
NOW = 101*NS


class PaperStatusFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.status = self.directory/"status.json"
        fx,spot = books(100*NS)
        self.receipt = create_capital_receipt(fx,spot,10000,FX,SPOT,now_ns=100*NS,max_age_ns=5*NS,source_sha256="fixture")
        fx,spot = books(NOW)
        quote = self.receipt["conversion"]["net_base"]
        value = matched_valuation(self.receipt,fx,spot,quote,0,now_ns=NOW,max_age_ns=5*NS)
        self.report = {"schema":1,"kind":"paper_accounting","live_execution":False,"updated_ns":NOW,
                       "session_status":"running","reason":None,"venue":"nobitex","pair":"BTC/USDT",
                       "balances":[{"venue":"nobitex","asset":"USDT","free":str(quote),"reserved":"0"}],
                       "orders":[],"risk":{"data_ok":True,"halt":None,"drawdown_fraction":"0.01"},
                       "risk_limits":{"max_feed_age_ns":5*NS,"drawdown_fraction":"0.05"},
                       "last_book":asdict(spot),"capital_basis":"paper_irt_conversion",
                       "matched_valuation":{"available":True,"last_valuation":value,
                                            "marked_balances":{"usdt":str(quote),"btc":"0"}}}
        atomic_json(self.status,self.report)
        atomic_json(self.directory/"capital-receipt.json",self.receipt)
        with OwnerLock(self.directory/"owner.lock"):
            pass

    def tearDown(self):
        self.tmp.cleanup()


class MonitorTests(PaperStatusFixture, unittest.TestCase):
    def test_freshness_requires_live_owner_and_matching_balance(self):
        with OwnerLock(self.directory/"owner.lock"):
            value = read_status(self.directory,now_ns=NOW+NS)
            self.assertEqual(value["process_state"],"running")
            self.assertTrue(value["irt_performance_available"])
            self.assertTrue(value["risk_current"])
            self.report["balances"][0]["free"] = "1"
            atomic_json(self.status,self.report)
            self.assertFalse(read_status(self.directory,now_ns=NOW+NS)["irt_performance_available"])
        self.assertEqual(read_status(self.directory,now_ns=NOW+NS)["process_state"],"interrupted")

    def test_old_mark_remains_historical_and_file_not_rewritten(self):
        before = self.status.read_bytes()
        with OwnerLock(self.directory/"owner.lock"):
            value = read_status(self.directory,now_ns=NOW+6*NS)
        self.assertFalse(value["irt_performance_available"])
        self.assertFalse(value["book_current"])
        self.assertIsNotNone(value["matched_valuation"]["last_valuation"])
        self.assertEqual(before,self.status.read_bytes())

    def test_future_clock_and_unresponsive_owner_never_fresh(self):
        with OwnerLock(self.directory/"owner.lock"):
            for clock,state in ((NOW-NS,"clock_unknown"),(NOW+16*NS,"unresponsive")):
                value = read_status(self.directory,now_ns=clock)
                self.assertEqual(value["process_state"],state)
                self.assertFalse(value["irt_performance_available"])
                self.assertFalse(value["book_current"])

    def test_projection_excludes_extra_local_fields_and_scales_exact_percent(self):
        self.report.update(pid=1234,private_machine_path="test-only-path",unexpected_key="not-served")
        atomic_json(self.status,self.report)
        view = dashboard_snapshot(self.directory,now_ns=NOW)
        self.assertNotIn("pid",view)
        self.assertNotIn("private_machine_path",view)
        self.assertNotIn("unexpected_key",view)
        self.assertEqual(view["risk"]["drawdown_fraction_percent"],"1.00")
        self.assertEqual(view["initial_irt"],"10000")

    def test_mismatched_receipt_rejected(self):
        self.report["matched_valuation"]["last_valuation"]["capital_receipt_sha256"] = "wrong"
        atomic_json(self.status,self.report)
        with self.assertRaisesRegex(DataError,"do not match"):
            dashboard_snapshot(self.directory,now_ns=NOW)

    def test_unrecognized_live_status_and_oversized_file_rejected(self):
        self.report["live_execution"] = True
        atomic_json(self.status,self.report)
        with self.assertRaises(DataError):
            read_status(self.directory,now_ns=NOW)
        self.status.write_bytes(b" "*(8*1024*1024+1))
        with self.assertRaisesRegex(DataError,"read limit"):
            read_status(self.directory,now_ns=NOW)

    def test_only_last_fifty_orders_returned_with_total_preserved(self):
        self.report["orders"] = [{"id":str(i)} for i in range(75)]
        atomic_json(self.status,self.report)
        view = dashboard_snapshot(self.directory,now_ns=NOW)
        self.assertEqual(view["order_count"],75)
        self.assertEqual(len(view["orders"]),50)
        self.assertEqual(view["orders"][0]["id"],"74")
        self.assertEqual(view["orders"][-1]["id"],"25")


class DashboardHTTPTests(PaperStatusFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = DashboardServer(self.directory,0)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        super().tearDown()

    def request(self,path="/api/status",method="GET",headers=None):
        client = HTTPConnection("127.0.0.1",self.server.server_address[1],timeout=3)
        try:
            client.request(method,path,headers=headers or {})
            response=client.getresponse()
            return response.status,dict(response.getheaders()),response.read()
        finally:
            client.close()

    def test_http_is_read_only_loopback_and_no_cache(self):
        before=hashlib.sha256(self.status.read_bytes()).hexdigest()
        self.assertEqual(self.server.server_address[0],"127.0.0.1")
        code,headers,body=self.request()
        self.assertEqual(code,200)
        self.assertFalse(json.loads(body)["live_execution"])
        self.assertEqual(headers["Cache-Control"],"no-store")
        self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin",headers)
        self.assertEqual(self.request(method="POST")[0],405)
        self.assertEqual(before,hashlib.sha256(self.status.read_bytes()).hexdigest())
        self.assertFalse((self.directory/"STOP").exists())

    def test_cross_origin_dns_rebinding_and_file_routes_refused(self):
        for headers in ({"Host":"example.com"},{"Origin":"https://example.com"},{"Sec-Fetch-Site":"cross-site"}):
            self.assertEqual(self.request(headers=headers)[0],403)
        for path in ("/status.json","/capital-receipt.json","/../status.json","/%2e%2e/status.json","/api/status?file=config.json"):
            self.assertEqual(self.request(path)[0],404)

    def test_link_navigation_can_open_shell_but_not_financial_json(self):
        headers={"Sec-Fetch-Site":"cross-site","Sec-Fetch-Mode":"navigate","Sec-Fetch-Dest":"document"}
        self.assertEqual(self.request("/",headers=headers)[0],200)
        self.assertEqual(self.request("/api/status",headers=headers)[0],403)

    def test_corrupt_status_returns_generic_error_and_assets_still_load(self):
        self.status.write_text("corrupt fixture with private content",encoding="utf-8")
        code,_,body=self.request()
        self.assertEqual(code,503)
        self.assertEqual(json.loads(body),{"error":"paper_status_unavailable"})
        for path in ("/","/dashboard.js","/dashboard.css"):
            self.assertEqual(self.request(path)[0],200)


if __name__ == "__main__":
    unittest.main()
