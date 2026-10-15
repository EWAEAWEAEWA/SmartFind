"""Run with:  python -m unittest -v      (no Streamlit needed)"""
import unittest

import smartfind_vision
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
        self.assertEqual(made[0]["key"], "L1_F1")
        self.assertEqual(made[0]["recipient"], "a@tip.edu.ph")

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
        self.assertEqual(sorted(n["key"] for n in new_notifications(reps, set(), "now")), ["L1_F1", "L1_F2"])


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


class VisionScopeTests(unittest.TestCase):
    """Phase 1: only calculators are recognizable, and only by placeholder file-name keywords."""

    def test_only_calculator_is_in_scope(self):
        self.assertEqual(list(smartfind_vision.AI_CATEGORIES), ["Calculator"])

    def test_calculator_filename_is_suggested_as_placeholder(self):
        r = smartfind_vision.classify_image(b"", "black_casio_calculator.jpg")
        self.assertEqual(r["category"], "Calculator")
        self.assertEqual(r["color"], "Black")
        self.assertEqual(r["source"], "placeholder")
        self.assertIsNone(r["confidence"])            # placeholder must never invent a confidence

    def test_other_items_get_no_suggestion(self):
        for name in ("my_phone.jpg", "id_card.png", "keys.jpg", "eyeglasses.jpg", "earphones.jpg", "camera_input.jpg"):
            self.assertIsNone(smartfind_vision.classify_image(b"", name)["category"], name)

    def test_model_mode_without_a_model_raises_instead_of_pretending(self):
        old = smartfind_vision.RECOGNIZER_MODE
        smartfind_vision.RECOGNIZER_MODE = "model"
        try:
            with self.assertRaises(NotImplementedError):
                smartfind_vision.classify_image(b"", "calculator.jpg")
        finally:
            smartfind_vision.RECOGNIZER_MODE = old

    def test_default_mode_is_placeholder(self):
        self.assertEqual(smartfind_vision.RECOGNIZER_MODE, "placeholder")


if __name__ == "__main__":
    unittest.main()
