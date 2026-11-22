import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "redesign" / "lib"))

from yoteiban_rd.route_links import build_route_links
from yoteiban_rd import cli


class RouteLinksTests(unittest.TestCase):
    def links(self, **values):
        return build_route_links({"destination": "架空駅", **values})["links"]

    def params(self, url):
        return parse_qs(urlparse(url).query)

    def test_google_maps_origin_optional_and_all_travel_modes(self):
        for mode in ("transit", "walking", "driving", "bicycling"):
            with self.subTest(mode=mode):
                links = self.links(origin="架空出発地", mode=mode, transit_time_fallback=False)
                self.assertEqual([item["provider"] for item in links], ["google"])
                query = self.params(links[0]["url"])
                self.assertEqual(query["api"], ["1"])
                self.assertEqual(query["origin"], ["架空出発地"])
                self.assertEqual(query["destination"], ["架空駅"])
                self.assertEqual(query["travelmode"], [mode])
        no_origin = self.links(mode="walking", transit_time_fallback=False)[0]
        self.assertNotIn("origin", self.params(no_origin["url"]))

    def test_yahoo_departure_and_arrival_time_digits_and_padding(self):
        departure = self.links(origin="架空駅A", datetime="2026-01-03T09:05:00", time_kind="departure")[1]
        q = self.params(departure["url"])
        self.assertEqual((q["from"], q["to"]), (["架空駅A"], ["架空駅"]))
        self.assertEqual((q["y"], q["m"], q["d"], q["hh"], q["m1"], q["m2"], q["type"]),
                         (["2026"], ["01"], ["03"], ["09"], ["0"], ["5"], ["1"]))
        arrival = self.links(datetime="2026-12-31T23:59:00", time_kind="arrival")[1]
        q = self.params(arrival["url"])
        self.assertEqual((q["y"], q["m"], q["d"], q["hh"], q["m1"], q["m2"], q["type"]),
                         (["2026"], ["12"], ["31"], ["23"], ["5"], ["9"], ["4"]))

    def test_japanese_names_are_url_encoded(self):
        link = self.links(origin="架空の出発地", mode="driving", transit_time_fallback=False)[0]["url"]
        self.assertIn("%E", link)
        self.assertEqual(self.params(link)["origin"], ["架空の出発地"])

    def test_coordinates_are_used_only_inside_returned_links(self):
        result = build_route_links({"origin": {"lat": 35.0, "lon": 139.0},
                                    "destination": {"lat": 36.0, "lon": 140.0},
                                    "mode": "walking", "transit_time_fallback": False})
        self.assertEqual(set(result), {"links"})
        query = self.params(result["links"][0]["url"])
        self.assertEqual(query["origin"], ["35.0,139.0"])
        self.assertEqual(query["destination"], ["36.0,140.0"])

    def test_maps_primary_switch_and_fallback_toggle(self):
        data = {"destination": "架空駅", "mode": "transit", "datetime": "2026-01-03T09:05:00",
                "time_kind": "departure", "maps_primary": "yahoo"}
        links = build_route_links(data)["links"]
        self.assertEqual([item["provider"] for item in links], ["yahoo", "google"])
        self.assertTrue(links[0]["primary"])
        data["maps_primary"] = "google"
        links = build_route_links(data)["links"]
        self.assertEqual([item["provider"] for item in links], ["google", "yahoo"])
        data["transit_time_fallback"] = False
        self.assertEqual([item["provider"] for item in build_route_links(data)["links"]], ["google"])

    def test_yahoo_is_only_for_timed_transit_and_no_location_is_read(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network access"), create=True), \
             patch("socket.socket.connect", side_effect=AssertionError("network access")):
            links = self.links(mode="walking")
            self.assertEqual([item["provider"] for item in links], ["google"])
            links = build_route_links({"destination": "架空駅", "mode": "transit"})["links"]
            self.assertEqual([item["provider"] for item in links], ["google"])

    def test_cli_route_links_reads_and_returns_json(self):
        stdin = io.StringIO(json.dumps({"destination": "架空駅", "mode": "walking"}, ensure_ascii=False))
        stdout = io.StringIO()
        with patch.object(sys, "stdin", stdin), redirect_stdout(stdout):
            self.assertEqual(cli.main(["route-links"]), 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["links"][0]["provider"], "google")

    def test_decided_trip_output_instruction_includes_route_link_row(self):
        skill = (ROOT / "skills" / "adjust" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("決まった移動の予定をチャットや朝のまとめに出すとき", skill)
        self.assertIn("route-links", skill)
        self.assertIn('「経路：Google マップで開く」をリンクとして置く', skill)
        result = build_route_links({"destination": "架空駅", "mode": "walking"})
        self.assertTrue(result["links"][0]["url"].startswith("https://www.google.com/maps/dir/"))


if __name__ == "__main__":
    unittest.main()
