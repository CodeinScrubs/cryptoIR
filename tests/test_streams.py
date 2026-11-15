import asyncio
import json
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from cryptoir.domain import DataError
from cryptoir.streams import EventWriter, RecordingLimit, StreamParser, messages, record_streams


def nbt(offset=10):
    return {"push": {"channel": "public:orderbook-BTCUSDT", "pub": {"offset": offset,
             "data": {"bids": [["99", "2"]], "asks": [["100", "3"]], "lastUpdate": 1000}}}}


def wl(side="buy", price="99", wrapped=True):
    rows = [{"price": price, "quantity": "1"}]
    return {"push": {"channel": "orderbook:BTCUSDT@" + side + "Depth",
                     "pub": {"data": {"message": rows} if wrapped else rows}}}


def td():
    return {"stream": "btcusdt@depth@2000ms", "data": {"s": "BTCUSDT", "e": "depthUpdate",
            "E": 1000, "b": [["99", "1"]], "a": [["100", "2"]]}}


class ParserTests(unittest.TestCase):
    def test_decimal_json_and_batched_frames(self):
        values = messages('{}\n{"price":0.1}\n')
        self.assertEqual(values, [{}, {"price": D("0.1")}])
        for raw in (b'{}', '[]', '', 'NaN'):
            with self.assertRaises(DataError):
                messages(raw)

    def test_nobitex_source_and_gap(self):
        parser = StreamParser("nobitex", "BTC/USDT")
        self.assertEqual(parser.parse(nbt(), 1_100_000_000)[0]["book"]["exchange_ns"], 1_000_000_000)
        self.assertEqual(parser.parse(nbt(12), 1_200_000_000)[0]["kind"], "gap")
        with self.assertRaises(DataError):
            parser.parse(nbt(12), 1_300_000_000)

    def test_subscription_position_detects_initial_missing_message(self):
        parser = StreamParser("nobitex", "BTC/USDT")
        parser.parse({"id": 2, "subscribe": {"offset": 7}}, 1)
        self.assertEqual(parser.parse(nbt(10), 1_000_000_000)[0]["kind"], "gap")

    def test_encoded_payload_supported(self):
        message = nbt()
        message["push"]["pub"]["data"] = json.dumps(message["push"]["pub"]["data"])
        self.assertEqual(StreamParser("nobitex", "BTC/USDT").parse(message, 1_100_000_000)[0]["kind"], "book")

    def test_wrong_symbol_and_delta_rejected(self):
        message = nbt()
        message["push"]["pub"]["delta"] = True
        with self.assertRaises(DataError):
            StreamParser("nobitex", "BTC/USDT").parse(message, 1)
        message = td()
        message["data"]["s"] = "ETHUSDT"
        with self.assertRaises(DataError):
            StreamParser("tabdeal", "BTC/USDT").parse(message, 1)

    def test_tabdeal_timestamp_not_fabricated(self):
        event = StreamParser("tabdeal", "BTC/USDT").parse(td(), 1_100_000_000)[0]
        self.assertTrue(event["source_time_known"])
        self.assertEqual(event["continuity"], "no_exchange_sequence")

    def test_wallex_requires_both_sides(self):
        parser = StreamParser("wallex", "BTC/USDT")
        self.assertEqual([x["kind"] for x in parser.parse(wl(), 1_000_000_000)], ["book_side"])
        book = parser.parse(wl("sell", "100", False), 1_100_000_000)[-1]
        self.assertEqual(book["kind"], "book")
        self.assertFalse(book["source_time_known"])
        self.assertFalse(book["atomic_exchange_snapshot"])

    def test_side_staleness_and_reset(self):
        parser = StreamParser("wallex", "BTC/USDT")
        parser.parse(wl(), 1_000_000_000)
        self.assertEqual(parser.parse(wl("sell", "100"), 4_000_000_000)[-1]["kind"], "book_unavailable")
        parser.reset()
        self.assertEqual([x["kind"] for x in parser.parse(wl("sell", "100"), 4_100_000_000)], ["book_side"])

    def test_crossed_asynchronous_sides_are_not_tradeable(self):
        parser = StreamParser("wallex", "BTC/USDT")
        parser.parse(wl(), 1_000_000_000)
        self.assertEqual(parser.parse(wl("sell", "98"), 1_100_000_000)[-1]["kind"], "book_unavailable")

    def test_error_and_unsubscribe_invalidate(self):
        for message in ({"error": {"code": 102}}, {"push": {"unsubscribe": {}}}):
            with self.assertRaises(DataError):
                StreamParser("wallex", "BTC/USDT").parse(message, 1)

    def test_writer_bounds_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            writer = EventWriter(path, 1)
            try:
                with self.assertRaises(RecordingLimit):
                    writer.write("a", {"kind": "frame"})
                with self.assertRaises(FileExistsError):
                    EventWriter(path, 10000)
            finally:
                writer.close()

    def test_payload_cannot_replace_recording_schema_or_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            writer=EventWriter(Path(folder)/"events.jsonl",10000)
            try:
                for key in ("schema","seq","run_id","venue","observed_ns","monotonic_ns"):
                    with self.assertRaises(DataError):
                        writer.write("a",{"kind":"test",key:1})
                self.assertEqual(writer.seq,0)
            finally:
                writer.close()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_when_receive_completes_does_not_leave_capture_running(self):
        from unittest.mock import AsyncMock, MagicMock
        from cryptoir.streams import capture_venue

        calls=0
        async def receive():
            nonlocal calls
            calls+=1
            if calls==1:
                # Complete recv and cancel its owner in the same event-loop turn.
                asyncio.get_running_loop().call_soon(task.cancel)
                return '{"id":1,"connect":{}}'
            await asyncio.Event().wait()

        ws=MagicMock()
        ws.recv=receive
        ws.send=AsyncMock()
        context=MagicMock()
        context.__aenter__=AsyncMock(return_value=ws)
        context.__aexit__=AsyncMock(return_value=False)
        with patch('websockets.asyncio.client.connect',return_value=context):
            task=asyncio.create_task(capture_venue('nobitex','BTC/USDT',MagicMock()))
            try:
                done,_=await asyncio.wait([task],timeout=0.5)
                self.assertIn(task,done,'capture swallowed cancellation when recv completed')
                self.assertTrue(task.cancelled())
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)

    async def test_local_socket_ping_and_reconnect(self):
        from websockets.asyncio.server import serve
        sessions = 0

        async def handler(ws):
            nonlocal sessions
            sessions += 1
            command = json.loads(await ws.recv())
            self.assertEqual(command, {"id": 1, "connect": {}})
            await ws.send('{"id":1,"connect":{"ping":25,"pong":true}}')
            self.assertEqual(json.loads(await ws.recv())["subscribe"]["channel"], "public:orderbook-BTCUSDT")
            await ws.send('{"id":2,"subscribe":{"offset":9}}\n{}')
            self.assertEqual(await ws.recv(), '{}')
            await ws.send(json.dumps(nbt()))
            await ws.close()

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "socket.jsonl"
            async with serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                with patch.dict("cryptoir.streams.URLS", {"nobitex": f"ws://127.0.0.1:{port}"}):
                    report = await record_streams(path, "BTC/USDT", 5, ["nobitex"])
            self.assertGreaterEqual(sessions, 2)
            self.assertGreaterEqual(report["counts"]["nobitex:book"], 2)
            self.assertGreaterEqual(report["counts"]["nobitex:gap"], 2)
            self.assertEqual(report["venues_without_books"], [])
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([r["seq"] for r in rows], list(range(1, len(rows) + 1)))


if __name__ == "__main__":
    unittest.main()
