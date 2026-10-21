# scripts/

Standalone CLI utilities. Run from the project root, e.g. `python -m scripts.list_plans`
or `python scripts/list_plans.py`. They reuse `mealie_planner` clients and read `.env`.

> ⚠️ **Do not move or rename `clear_mealie.py`** — it is imported by `app.py`
> (`from mealie_planner.maintenance import wipe_mealie_data`) for the dashboard "Clear" action.

## Operational
Routine/data operations against the live Mealie instance.

| Script | Purpose |
| --- | --- |
| `clear_mealie.py` | Wipe current+next week meal plans and the active shopping list. **Imported by the app.** |
| `full_wipe_mealie.py` | Destructive: wipe a broader set of Mealie data. |
| `import_sample_recipes.py` | Seed the instance with sample recipes. |
| `migrate_to_zones.py` | One-time migration of shopping-list labels to numbered store "zones". |
| `add_honey_staple.py` | Add a specific staple item. |
| `recreate_sandwiches.py` | Data fix for sandwich/lunch entries. |
| `list_plans.py` | Print scheduled meal plans for inspection. |
| `check_current_ingredients.py` | Print the ingredients currently driving the shopping list. |
| `reconstruct_prep_notes.py` | Reconstruct and update dinner preparation notes/guides using AI. |

## Debug / profiling (throwaway)
Ad-hoc investigation scripts; not part of normal operation and may be stale.

| Script | Purpose |
| --- | --- |
| `debug_plan_generation.py` | Trace a plan-generation run. |
| `debug_daily_email.py` | Render/inspect the daily briefing email. |
| `profile_plan_generation.py` | Time the end-to-end plan generation. |
| `profile_substeps.py` | Time individual plan-generation substeps. |
| `test_breakfasts.py` | Probe breakfast selection behavior. |
| `test_sqlite_locking.py` | Reproduce Mealie SQLite locking under concurrent fetches. |
