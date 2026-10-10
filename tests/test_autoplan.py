"""Saturday auto-generation: the week is planned without anyone filling out the form,
but never over a week that already has dinners, and failures come back as an email."""
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock

from mealie_planner import database
from mealie_planner.email_notifier import auto_generate_weekly_plan


def services(plans=()):
    s = MagicMock()
    s.mealie.get_meal_plan.return_value = list(plans)
    lock = threading.Lock()
    s.week_lock.return_value = lock
    s.lock = lock
    return s


class AutoPlanTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_db = database.DATABASE_FILE
        database.DATABASE_FILE = os.path.join(self._tmpdir.name, "data", "test.db")
        if hasattr(database._local, "conn"):
            database._local.conn.close()
            del database._local.conn
        database.init_db()
        database.save_state_to_db({"low_staples": ["milk-id"], "freezer_items": "stale chicken"})

    def tearDown(self):
        if hasattr(database._local, "conn"):
            database._local.conn.close()
            del database._local.conn
        database.DATABASE_FILE = self._prev_db
        self._tmpdir.cleanup()

    def test_generates_with_saved_staples_and_empty_free_text(self):
        s = services()
        self.assertTrue(auto_generate_weekly_plan(s))
        kwargs = s.plan_generator.generate_weekly_plan.call_args.kwargs
        self.assertEqual(kwargs["low_staples_ids"], ["milk-id"])
        self.assertNotIn("freezer_items", kwargs)  # last week's answers are stale
        s.notifier.send_email.assert_not_called()
        self.assertFalse(s.lock.locked())

    def test_skips_when_dinners_already_planned(self):
        s = services([{"entryType": "dinner", "recipeId": "r1", "date": "2026-10-10"}])
        self.assertFalse(auto_generate_weekly_plan(s))
        s.plan_generator.generate_weekly_plan.assert_not_called()

    def test_breakfast_only_week_still_generates(self):
        s = services([{"entryType": "breakfast", "title": "Oats", "date": "2026-10-10"}])
        self.assertTrue(auto_generate_weekly_plan(s))
        s.plan_generator.generate_weekly_plan.assert_called_once()

    def test_skips_while_a_manual_generation_runs(self):
        s = services()
        s.lock.acquire()
        self.assertFalse(auto_generate_weekly_plan(s))
        s.plan_generator.generate_weekly_plan.assert_not_called()

    def test_failure_emails_and_releases_lock(self):
        s = services()
        s.plan_generator.generate_weekly_plan.side_effect = RuntimeError("AI down")
        self.assertFalse(auto_generate_weekly_plan(s))
        subject, body = s.notifier.send_email.call_args.args
        self.assertIn("needs a look", subject)
        self.assertIn("AI down", body)
        self.assertFalse(s.lock.locked())

    def test_partial_success_still_counts_but_warns(self):
        s = services()

        def gen(**kw):
            kw["progress_callback"]("⚠️ Meal plan generated, but the shopping list sync failed", 100)
        s.plan_generator.generate_weekly_plan.side_effect = gen
        self.assertTrue(auto_generate_weekly_plan(s))
        s.notifier.send_email.assert_called_once()


if __name__ == "__main__":
    unittest.main()
