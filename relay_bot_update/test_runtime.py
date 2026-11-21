import tempfile
import unittest
import stat
from datetime import datetime, timezone
from pathlib import Path

from runtime import ReplyLedger, classify_event, decision_context, route_event


class RelayRuntimeTests(unittest.TestCase):
    def test_unmarked_group_does_not_call_ai_or_dispatch(self):
        result = route_event("ordinary group text", is_group=True,
                             call_markers=["CALL"], reply_markers=["BOT"])
        self.assertEqual(result, {"kind": "group_without_call", "should_call_ai": False,
                                  "should_dispatch": False})

    def test_group_call_and_unmarked_owner_request_are_independent(self):
        call = route_event("CALL inspect this", is_group=True,
                           call_markers=["CALL"], reply_markers=["BOT"])
        owner_request = route_event("please help me", is_group=True,
                                    call_markers=["CALL"], reply_markers=["BOT"])
        self.assertTrue(call["should_call_ai"])
        self.assertFalse(owner_request["should_call_ai"])
        self.assertEqual(classify_event("ordinary BOT mention", is_group=True,
                                        call_markers=["CALL"], reply_markers=["BOT"]),
                         "group_without_call")

    def test_markers_are_only_special_at_the_start(self):
        self.assertEqual(classify_event("  CALL task", is_group=True,
                                        call_markers=["CALL"], reply_markers=["BOT"]),
                         "called_group")
        self.assertEqual(classify_event("ordinary BOT text", is_group=True,
                                        call_markers=["CALL"], reply_markers=["BOT"]),
                         "group_without_call")

    def test_reply_ledger_deduplicates_across_routes_and_stores_no_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reply.sqlite"
            ledger = ReplyLedger(path)
            other_route = ReplyLedger(path)
            self.assertTrue(ledger.reserve("fixture-message-id", "mac", "2026-01-01T00:00:00Z"))
            self.assertFalse(other_route.reserve("fixture-message-id", "relay", "2026-01-01T00:00:01Z"))
            self.assertTrue(ledger.mark_replied("fixture-message-id", "2026-01-01T00:00:02Z"))
            self.assertEqual(ledger.state("fixture-message-id"), "replied")
            self.assertEqual(ledger.db.execute("select count(*) from reply_ledger").fetchone()[0], 1)
            columns = {row[1] for row in ledger.db.execute("pragma table_info(reply_ledger)").fetchall()}
            self.assertEqual(columns, {"message_id", "state", "route", "updated_at"})
            other_route.close()
            ledger.close()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_local_time_uses_configured_zone_and_injected_instant(self):
        context = decision_context(datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "Asia/Tokyo")
        self.assertEqual(context["now_local"], "2026-01-01T09:00:00+09:00")


if __name__ == "__main__":
    unittest.main()
