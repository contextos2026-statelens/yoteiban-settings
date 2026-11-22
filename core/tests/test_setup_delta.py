import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(R, "core"))

from yoteiban_core import setup_delta as sd


def fixture_catalog():
    data = sd.load_catalog()
    extra = {
        "id": "body_routines_fixture",
        "added_in": "1.0.31",
        "title": "体の用事",
        "memory_key": "体の用事",
        "in_initial_setup": False,
        "default": "",
        "unavailable_if_declined": "薬・食事・風呂の枠は入れない。",
    }
    data = {
        "baseline_version": data["baseline_version"],
        "releases": list(data["releases"])
        + [{"version": "1.0.31", "added_question_ids": ["body_routines_fixture"], "note": "試験用"}],
        "questions": list(data["questions"]) + [extra],
    }
    return data


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(sd.parse_version("1.0.28"), (1, 0, 28))
        self.assertTrue(sd.version_less("1.0.28", "1.0.29"))
        self.assertTrue(sd.version_less_or_equal("1.0.29", "1.0.29"))
        self.assertFalse(sd.version_less("1.0.29", "1.0.28"))

    def test_bad_version(self):
        with self.assertRaises(ValueError):
            sd.parse_version("1.0")
        with self.assertRaises(ValueError):
            sd.parse_version("v1.0.29")


class CatalogTests(unittest.TestCase):
    def test_default_catalog_loads(self):
        catalog = sd.load_catalog()
        self.assertEqual(catalog["baseline_version"], "1.0.28")
        self.assertTrue(any(r["version"] == "1.0.29" for r in catalog["releases"]))

    def test_settings_from_1_0_40_through_1_0_43_are_catalogued(self):
        catalog = sd.load_catalog()
        self.assertEqual(next(r for r in catalog["releases"] if r["version"] == "1.0.40")["added_question_ids"], ["movement_monitoring"])
        ids = {q["id"] for q in catalog["questions"]}
        self.assertTrue({"movement_monitoring", "departure_notice", "route_link_preferences",
                         "done_check", "mac_watch_connection_prep", "reader_connection_prep",
                         "relay_bot_update_use"}.issubset(ids))

    def test_conditional_imessage_questions_wait_for_answers_and_follow_branches(self):
        catalog = sd.load_catalog()
        questions = [q for q in catalog["questions"] if q["added_in"] == "1.0.42"
                     and q["id"] != "send_prefix"]
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, {})], ["imessage_use"])
        self.assertEqual(sd.pending_questions(questions, {"予定ばんでiMessageを使う":"使わない"}), [])
        mac = {"予定ばんでiMessageを使う":"使う", "iMessageを設定したMacがある":"ある"}
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, mac)],
                         ["imessage_mac_receive", "imessage_linq_use"])
        mac["MacのチャットDB受信を使う"] = "使う"
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, mac)],
                         ["imessage_privacy_consent", "imessage_linq_use"])
        mac["MacのチャットDB受信を使う"] = "使わない"
        mac["iMessage連携ボットを利用している"] = "使っていない"
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, mac)], [])
        linq = {**mac, "iMessage連携ボットを利用している":"使っている"}
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, linq)], ["imessage_linq_receive"])
        linq["連携ボット経由のメッセージを予定ばんで受け取る"] = "受け取る"
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, linq)],
                         ["imessage_privacy_consent", "relay_bot_markers"])
        update_question = [q for q in catalog["questions"] if q["id"] == "relay_bot_update_use"]
        self.assertEqual(sd.pending_questions(update_question, {
            "iMessage連携ボットを利用している": "使っている",
            "連携ボット経由のメッセージを予定ばんで受け取る": "受け取らない",
        }), [])
        update_answers = {
            "iMessage連携ボットを利用している": "使っている",
            "連携ボット経由のメッセージを予定ばんで受け取る": "受け取る",
        }
        self.assertEqual([q["id"] for q in sd.pending_questions(update_question, update_answers)],
                         ["relay_bot_update_use"])
        update_answers["連携ボット更新の案内"] = "使わない"
        self.assertEqual(sd.pending_questions(update_question, update_answers), [])
        self.assertEqual([q["id"] for q in sd.pending_questions(
            [q for q in catalog["questions"] if q["id"] == "send_prefix"], {})], ["send_prefix"])

    def test_connection_prep_branches_require_parent_answers(self):
        catalog = sd.load_catalog()
        questions = [q for q in catalog["questions"] if q["id"] in {
            "mac_watch_connection_prep", "reader_connection_prep"}]
        self.assertEqual(sd.pending_questions(questions, {}), [])
        answers = {"常駐Macの有無": "ある", "出発の知らせ設定": "reminder"}
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, answers)],
                         ["mac_watch_connection_prep"])
        answers["Macの見張り番と受け口の準備"] = "準備を進める"
        self.assertEqual([q["id"] for q in sd.pending_questions(questions, answers)],
                         ["reader_connection_prep"])
        answers["Macの見張り番と受け口の準備"] = "今はしない"
        self.assertEqual(sd.pending_questions(questions, answers), [])

    def test_empty_delta_for_1_0_29(self):
        catalog = sd.load_catalog()
        added = sd.questions_added_since(catalog, "1.0.28", "1.0.29")
        self.assertEqual(added, [])
        added_same = sd.questions_added_since(catalog, "1.0.29", "1.0.29")
        self.assertEqual(added_same, [])

    def test_missing_completed_uses_baseline(self):
        catalog = sd.load_catalog()
        self.assertEqual(sd.resolved_completed_version(catalog, None), "1.0.28")
        self.assertEqual(sd.resolved_completed_version(catalog, "  "), "1.0.28")
        self.assertEqual(sd.questions_added_since(catalog, None, "1.0.29"), [])

    def test_route_link_preferences_are_one_pending_question_until_both_answers_exist(self):
        catalog = sd.load_catalog()
        memory = {item["memory_key"]: "test answer" for item in catalog["questions"]
                  if item["id"] != "route_link_preferences"}
        report = sd.build_report(catalog, "1.0.36", "1.0.37", memory)
        self.assertEqual(report["pending"], ["route_link_preferences"])
        question = report["questions"][0]
        self.assertEqual(question["answer_keys"], ["maps_primary", "transit_time_fallback"])
        answered = dict(memory, maps_primary="google", transit_time_fallback=True)
        self.assertEqual(sd.build_report(catalog, "1.0.36", "1.0.37", answered)["pending"], [])

    def test_reminder_reader_questions_are_skipped_when_answered_and_list_is_not_duplicated(self):
        catalog = sd.load_catalog()
        reader_ids = {"resident_mac", "same_apple_account", "find_my_device",
                      "reminder_location_permission", "reminder_full_disk_access", "movement_monitoring"}
        reader_questions = [q for q in catalog["questions"] if q["id"] in reader_ids]
        self.assertEqual(len(reader_questions), len(reader_ids))
        memory = {q["memory_key"]: "fixture answer" for q in reader_questions}
        self.assertEqual(sd.pending_questions(reader_questions, memory), [])
        unanswered = [q for q in reader_questions if q["id"] == "resident_mac"]
        self.assertEqual([q["id"] for q in sd.pending_questions(unanswered, {})], ["resident_mac"])
        self.assertEqual(sum(q["id"] == "departure_notice" for q in catalog["questions"]), 1)
        departure = [q for q in catalog["questions"] if q["id"] == "departure_notice"]
        self.assertEqual(len(departure), 1)
        self.assertEqual(departure[0]["answer_keys"].count("move_reminder_list"), 1)


class DeltaFixtureTests(unittest.TestCase):
    def test_adds_only_later_questions(self):
        catalog = fixture_catalog()
        added = sd.questions_added_since(catalog, "1.0.30", "1.0.31")
        self.assertEqual([q["id"] for q in added], ["body_routines_fixture"])
        self.assertEqual(sd.questions_added_since(catalog, "1.0.31", "1.0.31"), [])
        self.assertEqual(sd.questions_added_since(catalog, "1.0.28", "1.0.29"), [])

    def test_skip_answered_and_tentative(self):
        catalog = fixture_catalog()
        added = sd.questions_added_since(catalog, "1.0.30", "1.0.31")
        self.assertEqual(sd.pending_questions(added, {}), added)
        self.assertEqual(sd.pending_questions(added, {"体の用事": "食事は12時"}), [])
        self.assertEqual(sd.pending_questions(added, {"体の用事": "仮（未回答）"}), [])

    def test_skip_connected_integration(self):
        item = {
            "id": "reminders",
            "memory_key": "リマインダーの専用リスト",
            "integration": "reminders",
        }
        self.assertEqual(sd.pending_questions([item], {}, connected={"reminders"}), [])
        self.assertEqual(sd.pending_questions([item], {}, connected=set()), [item])

    def test_default_is_tentative(self):
        self.assertEqual(sd.default_memory_value({"default": "使わない"}), "使わない：仮（未回答）")
        self.assertEqual(sd.default_memory_value({}), "仮（未回答）")

    def test_record_and_revert(self):
        mem = {"夜の通知": "急ぎだけ夜に知らせる（見に行かない）"}
        after = sd.record_answer(mem, "夜の通知", "夜は鳴らさない")
        self.assertEqual(after["夜の通知"], "夜は鳴らさない")
        self.assertEqual(after["夜の通知（前の値）"], "急ぎだけ夜に知らせる（見に行かない）")
        reverted, ok = sd.revert_answer(after, "夜の通知")
        self.assertTrue(ok)
        self.assertEqual(reverted["夜の通知"], "急ぎだけ夜に知らせる（見に行かない）")
        self.assertNotIn("夜の通知（前の値）", reverted)
        empty, ok2 = sd.revert_answer({"夜の通知": "夜は鳴らさない"}, "夜の通知")
        self.assertFalse(ok2)
        self.assertEqual(empty["夜の通知"], "夜は鳴らさない")

    def test_said_rules_are_not_overwritten_by_learning(self):
        self.assertFalse(sd.may_learn_overwrite("家用の見分けの表"))
        self.assertFalse(sd.may_learn_overwrite("本人が言ったルール"))
        self.assertFalse(sd.may_learn_overwrite("本人が言った：入口の分け方"))
        self.assertTrue(sd.may_learn_overwrite("確認の間隔"))
        self.assertFalse(sd.may_learn_overwrite("体の用事", {"said_rule": True}))

    def test_declined_text(self):
        catalog = fixture_catalog()
        item = next(q for q in catalog["questions"] if q["id"] == "body_routines_fixture")
        self.assertIn("入れない", sd.declined_unavailable_text(item))

    def test_cli_empty_delta(self):
        from io import StringIO
        from contextlib import redirect_stdout

        catalog = sd.load_catalog()
        memory = {
            item["memory_key"]: "架空の回答"
            for item in sd.questions_up_to(catalog, "1.0.29")
        }
        buf = StringIO()
        with redirect_stdout(buf):
            code = sd.main([
                "--completed-version", "1.0.28",
                "--current-version", "1.0.29",
                "--memory-json", json.dumps(memory, ensure_ascii=False),
            ])
        self.assertEqual(code, 0)
        self.assertIn('"pending": []', buf.getvalue())

    def test_release_mismatch_rejected(self):
        catalog = fixture_catalog()
        catalog["releases"][-1]["added_question_ids"] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaises(ValueError):
                sd.load_catalog(path)




class Version130Tests(unittest.TestCase):
    def test_real_catalog_adds_1_0_30(self):
        catalog = sd.load_catalog()
        added = sd.questions_added_since(catalog, "1.0.29", "1.0.30")
        self.assertEqual(
            [q["id"] for q in added],
            ["body_routines", "usage_cap", "heartbeat", "phone_permissions", "family_role"],
        )
        self.assertEqual(sd.questions_added_since(catalog, "1.0.28", "1.0.29"), [])


class MissingAnswerTests(unittest.TestCase):
    def test_no_initial_marker_or_completed_version_is_initial_setup(self):
        report = sd.build_report(sd.load_catalog(), None, "1.0.46", {})
        self.assertTrue(report["initial_setup_required"])
        self.assertFalse(report["additional_setup_required"])

    def test_initial_setup_marker_without_version_is_additional_setup(self):
        report = sd.build_report(sd.load_catalog(), None, "1.0.46", {"初回設定済み（日付）": "2026-01-01"})
        self.assertFalse(report["initial_setup_required"])
        self.assertTrue(report["additional_setup_required"])

    def test_newer_root_version_starts_additional_setup(self):
        catalog = sd.load_catalog()
        report = sd.build_report(catalog, "1.0.44", "1.0.45", {})
        self.assertTrue(report["additional_setup_required"])
        self.assertIn("movement_monitoring", report["pending"])

    def test_unanswered_movement_monitoring_is_asked_once_and_stays_off(self):
        catalog = sd.load_catalog()
        item = next(q for q in catalog["questions"] if q["id"] == "movement_monitoring")
        self.assertEqual(sd.pending_questions([item], {}), [item])
        tentative = sd.record_answer({}, item["memory_key"], sd.default_memory_value(item))
        self.assertEqual(sd.pending_questions([item], tentative), [])
        self.assertIn(sd.TENTATIVE_MARK, tentative[item["memory_key"]])

    def test_old_setup_checks_missing_baseline_answers(self):
        catalog = sd.load_catalog()
        report = sd.build_report(
            catalog,
            "1.0.20",
            "1.0.32",
            {"呼び名": "架空の呼び名", "夜の通知": "仮（未回答）"},
            connected={"reminders"},
        )
        self.assertIn("calendar", report["pending"])
        self.assertNotIn("nickname", report["pending"])
        self.assertNotIn("night_notify", report["pending"])
        self.assertNotIn("reminders", report["pending"])
        self.assertLess(report["pending"].index("calendar"), report["pending"].index("body_routines"))

    def test_same_or_newer_completed_version_still_checks_missing_answers(self):
        catalog = sd.load_catalog()
        report = sd.build_report(catalog, "1.0.32", "1.0.32", {})
        self.assertEqual(report["added"], [])
        self.assertIn("nickname", report["pending"])

        future = sd.build_report(catalog, "1.0.33", "1.0.32", {})
        self.assertIn("nickname", future["pending"])

    def test_no_pending_when_all_current_questions_are_answered(self):
        catalog = sd.load_catalog()
        memory = {
            item["memory_key"]: "架空の回答"
            for item in sd.questions_up_to(catalog, "1.0.32")
        }
        report = sd.build_report(catalog, "1.0.32", "1.0.32", memory)
        self.assertEqual(report["pending"], [])


class DepartureSetupQuestionTests(unittest.TestCase):
    def test_departure_question_is_pending_until_both_settings_are_answered(self):
        catalog = sd.load_catalog()
        item = next(q for q in catalog["questions"] if q["id"] == "departure_notice")
        self.assertEqual(item["answer_keys"], ["move_reminder_list", "departure_notice"])
        self.assertEqual(sd.pending_questions([item], {"move_reminder_list": "架空のリスト"}), [item])
        self.assertEqual(sd.pending_questions([item], {
            "move_reminder_list": "架空のリスト", "departure_notice": "reminder"
        }), [])


class DoneCheckSetupQuestionTests(unittest.TestCase):
    def test_question_32_is_pending_once_and_done_check_output_is_conditional(self):
        catalog = sd.load_catalog()
        item = next(q for q in catalog["questions"] if q["id"] == "done_check")
        self.assertEqual(item["setup_question"], "32")
        self.assertEqual(item["answer_keys"], ["done_check"])
        self.assertEqual(sd.pending_questions([item], {}), [item])
        self.assertEqual(sd.pending_questions([item], {"done_check": "all"}), [])
        self.assertEqual(sd.pending_questions([item], {"予定の完了確認": "off"}), [])


class AliasAnswerTests(unittest.TestCase):
    def test_alias_answer_is_treated_as_answered(self):
        question = {
            "id": "calendar_fixture",
            "memory_key": "個人用カレンダーID",
            "aliases": ["個人用カレンダー", "書く先のカレンダー"],
        }
        self.assertEqual(
            sd.pending_questions([question], {"書く先のカレンダー": "架空の回答"}),
            [],
        )

    def test_missing_alias_answer_stays_pending_and_tentative_is_skipped(self):
        question = {
            "id": "calendar_fixture",
            "memory_key": "個人用カレンダーID",
            "aliases": ["個人用カレンダー", "書く先のカレンダー"],
        }
        self.assertEqual(sd.pending_questions([question], {}), [question])
        self.assertEqual(
            sd.pending_questions([question], {"個人用カレンダー": "仮（未回答）"}),
            [],
        )

    def test_catalog_rejects_non_array_aliases(self):
        catalog = sd.load_catalog()
        catalog["questions"][0]["aliases"] = "呼び名"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_aliases.json"
            path.write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaises(ValueError):
                sd.load_catalog(path)


class PrepareItemsTests(unittest.TestCase):
    def test_prepare_list_exists_and_has_columns(self):
        path = Path(R) / "docs" / "prepare_items.md"
        text = path.read_text(encoding="utf-8")
        required = ["何か", "何に要るか", "無いと使えなくなる機能", "ボットが作れるか", "本人にしかできない一歩", "代わりの手段"]
        for col in required:
            self.assertIn(col, text)
        rows = [ln for ln in text.splitlines() if ln.startswith("| ") and not ln.startswith("| ---") and "何か" not in ln]
        self.assertGreaterEqual(len(rows), 30)
        self.assertNotIn("automations" + "/webhook/", text)
        self.assertNotIn("@gmail.com", text)


if __name__ == "__main__":
    unittest.main()
