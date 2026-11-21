import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "core"))
from yoteiban_core.imessage_routes import choose_route, duplicate_fingerprint


class RouteTests(unittest.TestCase):
    def test_owner_groups_are_mac_canonical_and_linq_copy_is_ignored(self):
        roster = [{"handle": "+1 (206) 555-0100", "status": "active"},
                  {"handle": "+1 425 555 0100", "status": "active"}]
        self.assertEqual(choose_route(source="linq", is_group=True,
                         owner_handles=["+12065550100"], participants=roster), "ignore")

    def test_linq_only_group_routes_to_linq(self):
        roster = [{"handle": "+1 425 555 0100", "status": "active"},
                  {"handle": "+1 206 555 0100", "status": "active"}]
        self.assertEqual(choose_route(source="linq", is_group=True,
                         owner_handles=["+12065550199"], participants=roster), "linq")

    def test_linq_direct_requires_configured_bot_line(self):
        self.assertEqual(choose_route(source="linq", is_group=False, owner_handles=[],
                         owner_handle="+12065550100", linq_handles=["+12065550100"]), "linq")
        self.assertEqual(choose_route(source="linq", is_group=False, owner_handles=[],
                         owner_handle="+12065550100", linq_handles=[]), "hold")

    def test_missing_roster_or_owner_identity_holds(self):
        self.assertEqual(choose_route(source="linq", is_group=True,
                         owner_handles=["+12065550100"], participants=None), "hold")
        self.assertEqual(choose_route(source="linq", is_group=True,
                         owner_handles=[], participants=[]), "hold")

    def test_outbound_never_enters_as_inbound(self):
        self.assertEqual(choose_route(source="imessage", is_group=False,
                         owner_handles=[], from_me=True), "ignore")

    def test_fingerprint_is_exact_and_stable(self):
        args = dict(conversation_key="group:a,b", sender="+1 206 555 0100", body=" hello ",
                    sent_at="2026-11-21T00:00:00Z")
        self.assertEqual(duplicate_fingerprint(**args), duplicate_fingerprint(**args))
        self.assertNotEqual(duplicate_fingerprint(**args), duplicate_fingerprint(**{**args, "body":"hello!"}))


if __name__ == "__main__":
    unittest.main()
