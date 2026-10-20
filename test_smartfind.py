"""Run with:  python -m unittest -v      (no Streamlit needed)"""
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import smartfind_mail
import smartfind_vision
import smartfind_store
from smartfind_auth import resolve_role
from smartfind_core import (NOTIFY_THRESHOLD, calculate_match_score, compare_locations,
                            find_pairs, new_notifications, normalize_location)


def report(rid, rtype, **kw):
    base = {"id": rid, "type": rtype, "item": "Black Smartphone", "category": "Smartphone",
            "color": "Black", "location": "Laboratory", "date": "2026-10-08",
            "description": "cracked screen with black case", "photo": None,
            "status": "Searching" if rtype == "Lost" else "Reported", "user_email": "a@tip.edu.ph"}
    base.update(kw)
    return base


def state(rows, rule):
    return next(r for r in rows if r["rule"] == rule)


class LocationTests(unittest.TestCase):
    def test_identical_locations_match_fully(self):
        pts, _, _, st_ = compare_locations("Laboratory", "laboratory ")
        self.assertEqual((pts, st_), (20, "match"))

    def test_room_code_formats_are_equal(self):
        for other in ("Q5312", "q 5312", "Room Q-5312"):
            self.assertEqual(compare_locations("Q-5312", other)[0], 20, other)

    def test_same_first_two_letters_but_unrelated_earns_nothing(self):
        # old rule: both start with "st" -> partial points
        self.assertEqual(compare_locations("Student Lounge", "Study Hall")[0], 0)
        self.assertEqual(compare_locations("Canteen", "Cashier Window")[0], 0)

    def test_related_locations_earn_partial(self):
        self.assertEqual(compare_locations("Library — 2nd Floor", "Library")[0], 12)   # same area
        self.assertEqual(compare_locations("Computer Lab", "Laboratory")[0], 12)       # alias
        self.assertEqual(compare_locations("Q-5312", "Q-5301")[0], 12)                 # same building+floor
        self.assertEqual(compare_locations("Q-5312", "Q-4201")[0], 8)                  # same building
        self.assertEqual(compare_locations("Q-5312", "R-5312")[0], 0)                  # other building
        self.assertEqual(compare_locations("Q-5312", "Library")[0], 0)

    def test_room_code_matches_inside_longer_location_text(self):
        self.assertEqual(compare_locations("q-5312 po nakita", "Q-5312")[:1], (20,))

    def test_shared_location_keyword_matches_inside_longer_text(self):
        pts, detail, _, state_ = compare_locations("sa may auditorium", "near the auditorium entrance")
        self.assertEqual((pts, detail, state_), (12, "Same location keyword", "partial"))

    def test_close_location_keyword_matches_inside_longer_text(self):
        pts, detail, _, state_ = compare_locations("sa may congre", "congregrating area")
        self.assertEqual((pts, detail, state_), (8, "Close location keyword", "partial"))

    def test_unrelated_keywords_do_not_match(self):
        self.assertEqual(compare_locations("sa may congre", "near cafeteria")[0], 0)

    def test_missing_location_earns_nothing(self):
        for a, b in (("", ""), ("Laboratory", ""), ("  ", "Laboratory")):
            pts, _, _, st_ = compare_locations(a, b)
            self.assertEqual((pts, st_), (0, "unverified"))

    def test_normalize_handles_none(self):
        self.assertEqual(normalize_location(None)["text"], "")


class ScoreTests(unittest.TestCase):
    def test_identical_reports_score_100(self):
        s, rows = calculate_match_score(report("L1", "Lost"), report("F1", "Found"))
        self.assertEqual(s, 100)
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(r["pts"] for r in rows), s)

    def test_score_never_exceeds_100(self):
        s, _ = calculate_match_score(report("L1", "Lost"), report("F1", "Found"))
        self.assertLessEqual(s, 100)

    def test_missing_optional_color_and_description_are_unverified(self):
        lost = report("L1", "Lost", color="", description="")
        s, rows = calculate_match_score(lost, report("F1", "Found"))
        self.assertEqual(state(rows, "Color")["state"], "unverified")
        self.assertEqual(state(rows, "Description")["state"], "unverified")
        self.assertEqual(s, 100 - 20 - 10)

    def test_two_blank_colors_do_not_count_as_a_match(self):
        s, rows = calculate_match_score(report("L1", "Lost", color=""), report("F1", "Found", color=""))
        self.assertEqual(state(rows, "Color")["pts"], 0)

    def test_different_categories_lose_category_points_and_cannot_be_strong(self):
        s, rows = calculate_match_score(report("L1", "Lost"), report("F1", "Found", category="Keys"))
        self.assertEqual(state(rows, "Category")["pts"], 0)
        self.assertLess(s, NOTIFY_THRESHOLD)

    def test_other_vs_other_is_only_partial(self):
        s, rows = calculate_match_score(report("L1", "Lost", category="Other"),
                                        report("F1", "Found", category="Other"))
        self.assertEqual(state(rows, "Category")["pts"], 10)

    def test_unrelated_same_prefix_location_lowers_score(self):
        s, rows = calculate_match_score(report("L1", "Lost", location="Student Lounge"),
                                        report("F1", "Found", location="Study Hall"))
        self.assertEqual(state(rows, "Location")["pts"], 0)
        self.assertEqual(s, 80)


class PairAndNotificationTests(unittest.TestCase):
    def test_resolved_reports_are_excluded_from_pairs(self):
        reps = [report("L1", "Lost"), report("F1", "Found")]
        self.assertEqual(len(find_pairs(reps)), 1)
        reps[0]["status"] = "Resolved"
        self.assertEqual(find_pairs(reps), [])
        reps[0]["status"], reps[1]["status"] = "Searching", "Resolved"
        self.assertEqual(find_pairs(reps), [])

    def test_resolved_reports_never_notify(self):
        reps = [report("L1", "Lost", status="Resolved"), report("F1", "Found")]
        self.assertEqual(new_notifications(reps, set(), "now"), [])

    def test_new_found_report_creates_one_notification(self):
        reps = [report("L1", "Lost")]
        self.assertEqual(new_notifications(reps, set(), "now"), [])      # nothing to match yet
        reps.append(report("F1", "Found"))                               # new report arrives
        made = new_notifications(reps, set(), "now")
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["pair_key"], "L1_F1")
        self.assertEqual(made[0]["recipient"], "a@tip.edu.ph")

    def test_both_report_owners_receive_match_notifications(self):
        lost = report("L1", "Lost", user_email="owner1@example.com")
        found = report("F1", "Found", user_email="owner2@example.com")
        made = new_notifications([lost, found], set(), "now")
        self.assertEqual({entry["recipient"] for entry in made},
                         {"owner1@example.com", "owner2@example.com"})
        self.assertEqual({entry["pair_key"] for entry in made}, {"L1_F1"})

    def test_recipient_identity_is_case_normalized(self):
        lost = report("L1", "Lost", user_email="Owner@Example.com")
        found = report("F1", "Found", user_email="owner@example.com")
        made = new_notifications([lost, found], set(), "now")
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0]["recipient"], "owner@example.com")

    def test_duplicate_notifications_are_prevented(self):
        reps = [report("L1", "Lost"), report("F1", "Found")]
        keys = set()
        first = new_notifications(reps, keys, "now")
        keys |= {n["key"] for n in first}
        self.assertEqual(len(first), 1)
        self.assertEqual(new_notifications(reps, keys, "later"), [])     # rerun: no duplicate

    def test_weak_match_is_listed_but_does_not_notify(self):
        reps = [report("L1", "Lost"), report("F1", "Found", category="Keys", color="Silver",
                                              location="Canteen", date="2026-10-20")]
        self.assertEqual(new_notifications(reps, set(), "now"), [])

    def test_each_pair_notifies_separately(self):
        reps = [report("L1", "Lost"), report("F1", "Found"), report("F2", "Found")]
        self.assertEqual(sorted(n["pair_key"] for n in new_notifications(reps, set(), "now")), ["L1_F1", "L1_F2"])


class MatchingRefinementTests(unittest.TestCase):
    def test_color_partial_needs_a_shared_word_not_a_substring(self):
        _, rows = calculate_match_score(report("L1", "Lost", color="Navy Blue"), report("F1", "Found", color="Blue"))
        self.assertEqual(state(rows, "Color")["pts"], 10)
        _, rows = calculate_match_score(report("L1", "Lost", color="Tan"), report("F1", "Found", color="Titanium"))
        self.assertEqual(state(rows, "Color")["pts"], 0)

    def test_description_ignores_punctuation(self):
        lost = report("L1", "Lost", description="Black cover. Cracked, screen!")
        found = report("F1", "Found", description="black cover cracked screen")
        _, rows = calculate_match_score(lost, found)
        self.assertEqual(state(rows, "Description")["pts"], 10)

    def test_brand_is_supporting_evidence_without_inflating_score(self):
        lost = report("L1", "Lost", brand="ASUS")
        found = report("F1", "Found", brand="asus")
        score, rows = calculate_match_score(lost, found)
        self.assertEqual(state(rows, "Brand")["pts"], 5)
        self.assertEqual(state(rows, "Description")["pts"], 5)
        self.assertEqual(score, 100)

    def test_brand_mismatch_reduces_score_and_missing_brand_is_unverified(self):
        score, rows = calculate_match_score(report("L1", "Lost", brand="ASUS"),
                                            report("F1", "Found", brand="Acer"))
        self.assertEqual(state(rows, "Brand")["state"], "differ")
        self.assertEqual(score, 95)
        _, rows = calculate_match_score(report("L1", "Lost", brand="ASUS"), report("F1", "Found"))
        self.assertEqual(state(rows, "Brand")["state"], "unverified")


class VisionScopeTests(unittest.TestCase):
    """Real model metadata and image-analysis helpers are used; OCR stays optional."""

    def test_trained_model_and_embedded_categories_are_available(self):
        self.assertTrue(smartfind_vision.MODEL_PATH.is_file())
        self.assertEqual(smartfind_vision.RECOGNIZER_MODE, "model")
        self.assertTrue(smartfind_vision.get_model_categories())

    def test_color_estimator_returns_named_color(self):
        from PIL import Image
        self.assertEqual(smartfind_vision.estimate_dominant_color(Image.new("RGB", (30, 30), "red")), "Red")

    def test_invalid_image_fails_as_a_user_correctable_error(self):
        with self.assertRaisesRegex(ValueError, "readable image"):
            smartfind_vision.classify_image(b"not-an-image")

    def test_empty_photo_returns_empty_detection_shape(self):
        result = smartfind_vision.classify_image(b"")
        self.assertIsNone(result["category"])
        self.assertIsNone(result["bbox"])

    def test_ocr_groups_words_and_ranks_common_brand_correction(self):
        data = {
            "text": ["Sam", "sunq", "case"],
            "conf": ["91", "85", "98"],
            "block_num": [1, 1, 1], "par_num": [1, 1, 1], "line_num": [1, 1, 2],
        }
        candidates = smartfind_vision._ocr_candidate_lines(data)
        brands, raw_text = smartfind_vision._rank_candidates(candidates, "Mobile phone")
        self.assertEqual(brands[0], "Samsung")
        self.assertIn("Sam sunq", raw_text)

    def test_ocr_wrapper_returns_brand_and_raw_text_candidates(self):
        from unittest.mock import patch
        from PIL import Image
        with patch.object(smartfind_vision, "_rapidocr_candidates", return_value=[("SAMSUNG", 94)]):
            brands, raw_text = smartfind_vision.read_text_candidates(
                Image.new("RGB", (240, 120), "white"), "Mobile phone")
        self.assertIn("Samsung", brands)
        self.assertIn("SAMSUNG", raw_text)

    def test_brand_vocabulary_matches_all_trained_categories(self):
        self.assertEqual(len(smartfind_vision.BRANDS_BY_CATEGORY), 22)
        self.assertIn("Samsung", smartfind_vision.BRANDS_BY_CATEGORY["Mobile phone"])
        brands, _ = smartfind_vision._rank_candidates([("SAMSUNG", 94)], "Mobile phone")
        self.assertEqual(brands, ["Samsung"])

    def test_brand_legal_names_and_manual_category_aliases_normalize(self):
        self.assertEqual(smartfind_vision.normalize_brand("Samsung Electronics Co., Ltd."), "Samsung")
        self.assertEqual(smartfind_vision.normalize_brand("Apple Inc."), "Apple")
        self.assertEqual(smartfind_vision.normalize_brand("  Acme Labs  "), "Acme Labs")
        self.assertIn("Samsung", smartfind_vision.brands_for_category("Smartphone"))
        self.assertIn("Casio", smartfind_vision.brands_for_category("Calculator"))

    def test_full_photo_fallback_keeps_color_and_ocr_when_category_missing(self):
        from unittest.mock import patch
        from PIL import Image
        image = Image.new("RGB", (100, 100), "black")
        with patch.object(smartfind_vision, "estimate_dominant_color", return_value="Black"), \
                patch.object(smartfind_vision, "read_text_candidates",
                             return_value=(["Samsung"], ["SAMSUNG"])):
            result = smartfind_vision._fallback_image_analysis(image, "No supported object detected")
        self.assertIsNone(result["category"])
        self.assertEqual(result["color"], "Black")
        self.assertEqual(result["brand_candidates"], ["Samsung"])
        self.assertEqual(result["source"], "image-fallback")

    def test_ocr_preprocessing_upscales_small_item_crops(self):
        from PIL import Image
        enhanced, thresholded = smartfind_vision._prepare_ocr_images(Image.new("RGB", (100, 50), "white"))
        self.assertGreater(enhanced.width, 100)
        self.assertEqual(enhanced.size, thresholded.size)


class ReportStoreTests(unittest.TestCase):
    def test_postgres_schema_is_initialized_once_and_queries_contain_no_ddl(self):
        class FakeCursor:
            rowcount = 1

            def fetchall(self):
                return []

            def fetchone(self):
                return None

        class FakeConnection:
            def __init__(self):
                self.statements = []
                self.commits = 0
                self.closed = False

            def execute(self, statement, params=None):
                self.statements.append(statement)
                return FakeCursor()

            def commit(self):
                self.commits += 1

            def rollback(self):
                pass

            def close(self):
                self.closed = True

        connections = []

        def connect(*_args, **_kwargs):
            connection = FakeConnection()
            connections.append(connection)
            return connection

        old_url = smartfind_store._SUPABASE_DATABASE_URL
        old_initialized = set(smartfind_store._INITIALIZED_TARGETS)
        fake_psycopg = types.SimpleNamespace(connect=connect)
        try:
            smartfind_store._INITIALIZED_TARGETS.clear()
            smartfind_store.configure_supabase("postgresql://example.test/smartfind")
            with patch.dict(sys.modules, {"psycopg": fake_psycopg}):
                smartfind_store.initialize_storage()
                self.assertEqual(len(connections), 1)
                ddl = connections[0].statements
                self.assertTrue(any("CREATE TABLE" in statement for statement in ddl))
                self.assertTrue(any("ENABLE ROW LEVEL SECURITY" in statement for statement in ddl))
                with smartfind_store._connect() as connection:
                    connection.execute("SELECT payload::text FROM reports")
                self.assertEqual(len(connections), 2)
                self.assertEqual(connections[1].statements, ["SELECT payload::text FROM reports"])
        finally:
            smartfind_store.configure_supabase(old_url)
            smartfind_store._INITIALIZED_TARGETS.clear()
            smartfind_store._INITIALIZED_TARGETS.update(old_initialized)

class AuthenticationTests(unittest.TestCase):
    def test_only_verified_allowlisted_identities_receive_roles(self):
        self.assertEqual(resolve_role("Student@TIP.edu.ph", True, ["tip.edu.ph"], []), "student")
        self.assertEqual(resolve_role("admin@example.com", True, [], ["ADMIN@example.com"]), "admin")
        self.assertIsNone(resolve_role("user@other.edu", True, ["tip.edu.ph"], []))
        self.assertIsNone(resolve_role("user@tip.edu.ph", False, ["tip.edu.ph"], []))


class MobileNavigationTests(unittest.TestCase):
    def test_mobile_menu_is_visible_and_uses_the_same_navigation_targets(self):
        source = Path("main.py").read_text(encoding="utf-8")
        mobile_css = source[source.index("@media (max-width: 700px)"):]
        self.assertIn('key="mobile_nav"', source)
        self.assertIn('st.popover("☰ Menu")', source)
        self.assertIn('navigate(name)', source)
        self.assertIn(".st-key-mobile_nav", mobile_css)
        self.assertIn("min-width:44px", mobile_css)


class EmailDeliveryTests(unittest.TestCase):
    def test_mail_requires_smtp_credentials(self):
        self.assertFalse(smartfind_mail.is_configured({}))
        self.assertTrue(smartfind_mail.is_configured({
            "smtp_host": "smtp.gmail.com", "smtp_username": "a@example.com",
            "smtp_password": "app-password"}))

    def test_email_uses_report_owner_and_match_details(self):
        server = MagicMock()
        notification = {"recipient": "owner@example.com", "item": "Black phone", "score": 86}
        settings = {"smtp_host": "smtp.gmail.com", "smtp_port": 587,
                    "smtp_username": "sender@gmail.com", "smtp_password": "app pass word",
                    "app_url": "https://smartfind.streamlit.app"}
        with patch("smartfind_mail.smtplib.SMTP", return_value=server) as smtp:
            smartfind_mail.send_match_email(notification, settings)
        smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=20)
        server.login.assert_called_once_with("sender@gmail.com", "apppassword")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "owner@example.com")
        self.assertIn("86/100", message.get_content())
        self.assertIn("https://smartfind.streamlit.app", message.get_content())

    def test_smtp_probe_reports_the_stage_that_failed(self):
        server = MagicMock()
        server.starttls.side_effect = ConnectionError("TLS blocked")
        settings = {"smtp_host": "smtp.gmail.com", "smtp_port": 587,
                    "smtp_username": "sender@gmail.com", "smtp_password": "app-password"}
        with patch("smartfind_mail.smtplib.SMTP", return_value=server):
            with self.assertRaisesRegex(smartfind_mail.SMTPDeliveryError, "STARTTLS: ConnectionError"):
                smartfind_mail.test_smtp_connection(settings)

if __name__ == "__main__":
    unittest.main()
