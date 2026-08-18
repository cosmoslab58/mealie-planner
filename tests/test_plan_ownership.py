"""Meal-plan entry ownership.

Mealie's calendar is shared between this app and whoever opens Mealie's own
Meal Planner. Regeneration clears the week before rewriting it, so these tests
pin the rule that only entries this app created may be cleared.
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from mealie_planner import database
from mealie_planner.plan_generator import (
    OWNERSHIP_SINCE_KEY,
    PlanGenerator,
    resolve_owned_entries,
    slot_key,
)


def entry(entry_id, date="2026-05-30", entry_type="dinner"):
    return {"id": entry_id, "date": date, "entryType": entry_type}


class OwnershipTestCase(unittest.TestCase):
    """Gives each test a throwaway SQLite file and a clean thread-local connection."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_db = database.DATABASE_FILE
        database.DATABASE_FILE = os.path.join(self._tmpdir.name, "data", "test.db")
        if hasattr(database._local, "conn"):
            database._local.conn.close()
            del database._local.conn
        database.init_db()

    def tearDown(self):
        if hasattr(database._local, "conn"):
            database._local.conn.close()
            del database._local.conn
        database.DATABASE_FILE = self._prev_db
        self._tmpdir.cleanup()


class TestOwnershipRecords(OwnershipTestCase):
    def test_recorded_entry_is_owned(self):
        database.record_plan_entry("a", "2026-05-30", "dinner")
        self.assertEqual(database.filter_planner_owned(["a"]), {"a"})

    def test_unrecorded_entry_is_not_owned(self):
        database.record_plan_entry("a")
        self.assertEqual(database.filter_planner_owned(["a", "b"]), {"a"})

    def test_forget_drops_ownership(self):
        database.record_plan_entry("a")
        database.forget_plan_entry("a")
        self.assertEqual(database.filter_planner_owned(["a"]), set())

    def test_empty_input_is_empty_output(self):
        self.assertEqual(database.filter_planner_owned([]), set())
        self.assertEqual(database.filter_planner_owned([None, ""]), set())

    def test_ids_are_compared_as_strings(self):
        database.record_plan_entry(42)
        self.assertEqual(database.filter_planner_owned([42]), {"42"})

    def test_chunking_handles_more_than_the_sqlite_variable_limit(self):
        ids = [str(i) for i in range(1200)]
        for i in ids:
            database.record_plan_entry(i)
        self.assertEqual(database.filter_planner_owned(ids), set(ids))

    def test_prune_drops_only_older_dates(self):
        database.record_plan_entry("old", "2026-01-01", "dinner")
        database.record_plan_entry("new", "2026-05-30", "dinner")
        database.prune_plan_entries("2026-05-01")
        self.assertEqual(database.filter_planner_owned(["old", "new"]), {"new"})


class TestResolveOwnedEntries(OwnershipTestCase):
    def test_first_run_adopts_everything_it_finds(self):
        """Upgrade path: before tracking existed, every entry came from this app."""
        existing = [entry("a"), entry("b", entry_type="lunch")]
        self.assertEqual(resolve_owned_entries(existing), {"a", "b"})
        self.assertIsNotNone(database.get_plan_meta(OWNERSHIP_SINCE_KEY))

    def test_after_first_run_unrecorded_entries_are_protected(self):
        resolve_owned_entries([])  # sets the marker with nothing to adopt
        database.record_plan_entry("ours")
        owned = resolve_owned_entries([entry("ours"), entry("theirs")])
        self.assertEqual(owned, {"ours"})

    def test_first_run_marker_is_only_set_once(self):
        resolve_owned_entries([entry("a")])
        marker = database.get_plan_meta(OWNERSHIP_SINCE_KEY)
        resolve_owned_entries([entry("b")])
        self.assertEqual(database.get_plan_meta(OWNERSHIP_SINCE_KEY), marker)
        self.assertEqual(database.filter_planner_owned(["b"]), set())

    def test_slot_key_trims_datetime_to_date(self):
        self.assertEqual(
            slot_key({"date": "2026-05-30T00:00:00", "entryType": "dinner"}),
            ("2026-05-30", "dinner"),
        )


class TestClearOwnedEntries(OwnershipTestCase):
    def _generator(self, existing):
        client = MagicMock()
        client.get_meal_plan.return_value = existing
        gen = PlanGenerator(client, MagicMock(), MagicMock(), MagicMock(), MagicMock())
        return gen, client

    def test_deletes_only_our_entries_and_reports_the_rest(self):
        resolve_owned_entries([])  # past the one-time adoption
        database.record_plan_entry("ours", "2026-05-30", "dinner")
        existing = [
            entry("ours", "2026-05-30", "dinner"),
            entry("theirs", "2026-05-31", "dinner"),
        ]
        gen, client = self._generator(existing)

        protected = gen.clear_owned_entries("2026-05-30", "2026-06-05")

        client.delete_meal_plan_entry.assert_called_once_with("ours")
        self.assertEqual(protected, {("2026-05-31", "dinner")})

    def test_nothing_owned_means_nothing_deleted(self):
        resolve_owned_entries([])
        gen, client = self._generator([entry("theirs")])

        protected = gen.clear_owned_entries("2026-05-30", "2026-06-05")

        client.delete_meal_plan_entry.assert_not_called()
        self.assertEqual(protected, {("2026-05-30", "dinner")})

    def test_api_failure_protects_nothing_and_deletes_nothing(self):
        from mealie_planner.exceptions import MealieAPIError

        client = MagicMock()
        client.get_meal_plan.side_effect = MealieAPIError("boom")
        gen = PlanGenerator(client, MagicMock(), MagicMock(), MagicMock(), MagicMock())

        self.assertEqual(gen.clear_owned_entries("2026-05-30", "2026-06-05"), set())
        client.delete_meal_plan_entry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
