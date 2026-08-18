import json
import logging
import random
from datetime import datetime, timedelta

from .config import (
    _BANNED_RECIPES_SKILL_DEFINITION,
    _WEEKLY_MEAL_SELECTION_SKILL_DEFINITION,
    ACTIVE_LIST_ID,
    FAMILY_DIETARY_RULES_PROMPT,
    RECENT_LOOKBACK_DAYS,
    get_banned_recipes,
)
from .database import (
    adopt_plan_entries,
    filter_planner_owned,
    get_plan_meta,
    prune_plan_entries,
    set_plan_meta,
)
from .exceptions import MealieAPIError
from .models import WeeklyMealPlanResponse
from .parsers import parse_exclusions, parse_freezer_items

logger = logging.getLogger(__name__)

# Set once, the first time a plan is generated after ownership tracking shipped.
# Before that marker exists every entry in Mealie's calendar came from this app,
# so the first run adopts what it finds rather than stranding a week of meals.
OWNERSHIP_SINCE_KEY = "plan_ownership_since"
# How far back ownership rows are kept before being pruned.
OWNERSHIP_RETENTION_DAYS = 180


def resolve_owned_entries(existing_plans):
    """Return the ids of the given Mealie entries that this app is allowed to clear.

    Entries created by a human in Mealie's own Meal Planner are not ours, and
    deleting them is silent data loss.
    """
    existing_plans = existing_plans or []
    entry_ids = [p.get('id') for p in existing_plans if p.get('id')]

    if not get_plan_meta(OWNERSHIP_SINCE_KEY):
        # One-time upgrade path: claim the calendar as it stands today.
        adopt_plan_entries(existing_plans)
        set_plan_meta(OWNERSHIP_SINCE_KEY, datetime.now().isoformat(timespec='seconds'))
        logger.info(
            "[Ownership] Adopted %d pre-existing meal-plan entries on first tracked run.",
            len(entry_ids)
        )
        return set(str(i) for i in entry_ids)

    return filter_planner_owned(entry_ids)


def _prune_stale_ownership(start_date_str):
    """Forget ownership rows for long-past weeks so the table stays small.

    The cutoff is generous: regenerating a recent previous week must still find
    its own entries.
    """
    try:
        cutoff = datetime.strptime(start_date_str, "%Y-%m-%d") - timedelta(days=OWNERSHIP_RETENTION_DAYS)
        prune_plan_entries(cutoff.strftime("%Y-%m-%d"))
    except (ValueError, TypeError) as e:
        logger.warning("Skipping ownership prune for %r: %s", start_date_str, e)


def slot_key(entry):
    """(date, entryType) identity of a meal-plan entry, for slot comparison."""
    return (str(entry.get('date') or '')[:10], entry.get('entryType'))


def classify_early_late_dates(target_date_strings):
    """Split the planning range into 'early' (fresh/perishable) and 'late'
    (frozen/shelf-stable) day buckets used for perishability sequencing.

    Pure function so the sequencing rule can be unit-tested independently of the
    LLM-driven plan generation.
    """
    num_days = len(target_date_strings)
    if num_days <= 1:
        return list(target_date_strings), list(target_date_strings)
    elif num_days == 2:
        return target_date_strings[:1], target_date_strings[1:]
    elif num_days == 3:
        return target_date_strings[:2], target_date_strings[2:]
    else:
        # 4 to 7 days: reserve the last 3 days as 'late' once the range is long enough.
        split_idx = num_days - 3 if num_days >= 6 else num_days // 2
        return target_date_strings[:split_idx], target_date_strings[split_idx:]


class PlanGenerator:
    def __init__(self, mealie_client, ai_client, crawler, shopping, notifier):
        self.client = mealie_client
        self.ai = ai_client
        self.crawler = crawler
        self.shopping = shopping
        self.notifier = notifier

    def clear_owned_entries(self, start_date_str, end_date_str):
        """Delete this app's meal-plan entries for the week, leaving a human's alone.

        Returns the set of (date, entryType) slots that were left in place, so the
        caller can plan around them instead of scheduling a duplicate on top.
        """
        protected_slots = set()
        _prune_stale_ownership(start_date_str)
        try:
            existing_plans = self.client.get_meal_plan(start_date_str, end_date_str)
            owned_ids = resolve_owned_entries(existing_plans)
            for entry in existing_plans:
                if str(entry.get('id')) in owned_ids:
                    self.client.delete_meal_plan_entry(entry['id'])
                else:
                    protected_slots.add(slot_key(entry))
        except MealieAPIError as e:
            logger.error(f"Error clearing existing plans: {e}")
        return protected_slots

    def generate_weekly_plan(self, start_date_str, end_date_str, exclude_text="", freezer_items="", special_requests="", low_staples_ids=None, progress_callback=None, list_id=ACTIVE_LIST_ID):
        """Generate weekly plan using an AI-driven intelligent rule-based scoring engine and schedule in Mealie."""
        low_staples_ids = low_staples_ids or []
        if progress_callback:
            progress_callback("Analyzing inputs and processing freezer/pantry/fridge items...", 5)
        
        # Fetch all recipes once to use for lookup and deduplication
        all_recipes = self.crawler.get_recipes_from_db()

        priority_recipe_ids = []
        # Map each freezer/pantry item to its resolved recipe ID for the AI prompt
        item_to_recipe_map = {}
        if freezer_items:
            # Use AI to parse free-text items into structured ingredients
            try:
                parsed_items = parse_freezer_items(self.ai, freezer_items)
            except Exception as e:
                logger.error(f"[Plan Generation] parse_freezer_items failed: {e}")
                if progress_callback:
                    progress_callback(f"⚠️ Meal plan generation failed: AI could not understand your freezer/pantry/fridge items ({e}). No changes were made to your existing plan.", 100)
                return

            for item in parsed_items:
                if not item.get("is_main_dish", True):
                    logger.warning(f"[Plan Generation] Skipping recipe lookup/import for non-main-dish item: '{item.get('raw')}'")
                    continue
                
                core = item.get("core_ingredient", item.get("raw", ""))
                raw = item.get("raw", core)
                
                if progress_callback:
                    progress_callback(f"Finding/importing recipe for: {core}...", 15)
                
                recipe_id = self.crawler.find_recipe_for_ingredient(core, all_recipes=all_recipes)
                if not recipe_id:
                    if self.crawler.find_and_import_recipe(core, existing_recipe_ids=priority_recipe_ids):
                        all_recipes = self.crawler.get_recipes_from_db()
                        recipe_id = self.crawler.find_recipe_for_ingredient(core, all_recipes=all_recipes)
                
                if recipe_id and recipe_id not in priority_recipe_ids:
                    priority_recipe_ids.append(recipe_id)
                    item_to_recipe_map[raw] = recipe_id
                elif recipe_id:
                    item_to_recipe_map[raw] = recipe_id
                else:
                    logger.warning(f"[Plan Generation] WARNING: Could not find or import a recipe for '{raw}' (core: '{core}')")
                    
                    
        if progress_callback:
            progress_callback("Filtering recipes and checking exclusions...", 40)
        
        banned_recipes = [name.lower() for name in get_banned_recipes()]
        logger.info(f"[Plan Generation] Loaded banned recipes: {banned_recipes}")

        allowed_recipes = []
        
        for r in all_recipes:
            name_lower = r['name'].lower()
            slug_lower = r.get('slug', '').lower()
            
            # Tags are often dictionaries in newer Mealie versions
            tags = []
            for t in r.get('tags', []):
                if isinstance(t, dict):
                    tags.append(t.get('name', '').lower())
                elif isinstance(t, str):
                    tags.append(t.lower())
            
            # Check against specific banned recipe names
            is_banned = False
            for banned_name in banned_recipes:
                if banned_name in name_lower or banned_name in slug_lower or name_lower in banned_name:
                    is_banned = True
                    break
            
            if is_banned:
                logger.info(f"[Plan Generation] Excluding banned recipe: {r['name']}")
                continue
                
            allowed_recipes.append(r)
            
        if not allowed_recipes:
            logger.warning("Warning: No recipes left after filtering! Using unfiltered recipes.")
            allowed_recipes = all_recipes
            
        # Build a lookup map for validation after AI selection
        catalogue_ids = {r['id'] for r in allowed_recipes}

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        num_days = (end_date - start_date).days + 1
        try:
            exclusions = parse_exclusions(self.ai, exclude_text, start_date, end_date)
        except Exception as e:
            logger.error(f"[Plan Generation] parse_exclusions failed: {e}")
            if progress_callback:
                progress_callback(f"⚠️ Meal plan generation failed: AI could not understand your meal exclusion notes ({e}). No changes were made to your existing plan.", 100)
            return
        target_date_strings = [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(num_days)]

        # Fetch recently planned recipes (preceding RECENT_LOOKBACK_DAYS days) to avoid repeating them
        recent_recipe_names = []
        recent_recipe_ids = set()
        try:
            prev_start = start_date - timedelta(days=RECENT_LOOKBACK_DAYS)
            prev_end = start_date - timedelta(days=1)
            prev_plans = self.client.get_detailed_meal_plan(prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d"))
            for p in prev_plans:
                if p.get('recipeId'):
                    recent_recipe_ids.add(p['recipeId'])
                if p.get('recipe') and p['recipe'].get('name'):
                    recent_recipe_names.append(p['recipe']['name'])
            recent_recipe_names = list(set(recent_recipe_names))
        except MealieAPIError as e:
            logger.error(f"Error fetching recently planned recipes: {e}")

        # Filter out recently used recipes to prevent re-using them (unless explicitly requested/prioritized)
        from .shopping_sync import normalize_ingredient_name
        recent_names_norm = {normalize_ingredient_name(name) for name in recent_recipe_names}
        
        filtered_recipes = []
        for r in allowed_recipes:
            is_recent = r['id'] in recent_recipe_ids or normalize_ingredient_name(r['name']) in recent_names_norm
            if is_recent and r['id'] not in priority_recipe_ids:
                logger.info(f"[Plan Generation] Excluding recently planned recipe: {r['name']}")
                continue
            filtered_recipes.append(r)

        # Safety fallback: if too many recipes were filtered out, restore them to avoid running out of choices
        if len(filtered_recipes) < num_days + 3:
            logger.warning(f"[Plan Generation] Warning: Filtering recent recipes left only {len(filtered_recipes)} options. Restoring recent recipes to ensure enough choices.")
            filtered_recipes = allowed_recipes

        recipe_catalogue = [
            {
                "id": r["id"],
                "name": r["name"],
                "description": (r.get("description") or "")[:120],
                "tags": [t.get('name', t) if isinstance(t, dict) else t for t in r.get('tags', [])],
                "fiber_g": r.get("fiber_content"),
                "ingredients": r.get("ingredients", []),
                "instructions_preview": " ".join(r.get("instructions", []))[:120]
            }
            for r in filtered_recipes
        ]
        random.shuffle(recipe_catalogue)

        # Classify early vs late days relative to the planning range
        early_target_dates, late_target_dates = classify_early_late_dates(target_date_strings)

        early_days_display = ", ".join([datetime.strptime(d, "%Y-%m-%d").strftime("%A (%Y-%m-%d)") for d in early_target_dates])
        late_days_display = ", ".join([datetime.strptime(d, "%Y-%m-%d").strftime("%A (%Y-%m-%d)") for d in late_target_dates])

        selection_prompt = (
            """You are an expert in the 'Mealie Weekly Meal Selection Skill'.

""" +
            _WEEKLY_MEAL_SELECTION_SKILL_DEFINITION +
            """

### BANNED RECIPES SKILL RULES:
""" +
            _BANNED_RECIPES_SKILL_DEFINITION +
            """

### CONTEXT FOR THIS INVOCATION:
""" +
            f"- **Family Dietary Rules & Preferences**: {FAMILY_DIETARY_RULES_PROMPT}\n" +
            f"- **Banned Recipes (NEVER select these)**: {', '.join(get_banned_recipes())}\n" +
            f"- **Target Planning Dates (You MUST generate exactly these {num_days} dates in this exact order, even if some or all meals on a day are excluded)**: {json.dumps(target_date_strings)}\n" +
            "- **REDEFINED PERISHABILITY RULES FOR THIS PLANNED RANGE (STRICT RULE OVERRIDE)**:\n" +
            f"  * **EARLY DAYS (Reserve for fresh/highly-perishable ingredients)**: {early_days_display}\n" +
            f"  * **LATE DAYS (Reserve for frozen, canned, or shelf-stable ingredients)**: {late_days_display}\n" +
            "  * **Strict Sequencing constraint**: You MUST schedule recipes using frozen/shelf-stable items strictly on the LATE DAYS listed above. If there are multiple late days, prioritize the latest ones (e.g. towards the end of the late days list) to ensure they are eaten last. You MUST NOT schedule frozen/shelf-stable ingredients on the EARLY DAYS.\n" +
            (f"- **Note on Weekdays**: Since the target planning range ({start_date_str} to {end_date_str}) is shorter than a full 7-day week, if any prioritized items are described as fresh, schedule them on the earliest available target dates (e.g. Wednesday or Thursday) rather than requiring Sat-Tue.\n" if num_days < 7 else "") +
            f"- **Meal Exclusions (Skipped/Eating Out)**: {json.dumps(exclusions)}\n" +
            f"- **Freezer/Pantry/Fridge items to prioritize**: {freezer_items or 'none'}\n" +
            (f"- **MANDATORY Priority Recipes (MUST include ALL of these in the plan)**: {json.dumps(item_to_recipe_map)}\n" if item_to_recipe_map else "") +
            f"- **Special requests from the family**: {special_requests or 'none'}\n" +
            f"- **Recently planned recipes (AVOID selecting these)**: {', '.join(recent_recipe_names) if recent_recipe_names else 'none'}\n\n" +
            "### RECIPE CATALOGUE (JSON):\n" +
            f"{json.dumps(recipe_catalogue, indent=2)}\n\n" +
            "Return ONLY the JSON object as specified in the skill definition."
        )

        if progress_callback:
            progress_callback(f"Querying AI for optimized {num_days}-day plan...", 50)
        
        meals = []
        try:
            raw = self.ai.call(selection_prompt, response_schema=WeeklyMealPlanResponse, temperature=0.7)
            ai_result = WeeklyMealPlanResponse.model_validate_json(raw).model_dump()
            
            for day_entry in ai_result.get("days", []):
                d_str = day_entry['date']
                if d_str not in target_date_strings:
                    logger.warning(f"[Plan Generation] WARNING: AI generated out-of-range date {d_str}. Skipping.")
                    continue
                m = day_entry['meals']
                prep_note = m.get('prep_note') or ""
                
                # Deterministic Exclusion Enforcement
                day_name = datetime.strptime(d_str, "%Y-%m-%d").strftime("%A")
                day_exclusions = exclusions.get(day_name, [])
                logger.info(f"[Plan Generation] Processing {day_name} ({d_str}) with exclusions: {day_exclusions}")

                # Breakfast
                if 'breakfast' in day_exclusions:
                    meals.append({"date": d_str, "entryType": "breakfast", "title": "Skipped", "recipeId": None})
                else:
                    breakfast_title = m.get('breakfast', 'Staples')
                    # Double-check if AI returned something else for skipped meal
                    if breakfast_title.lower() == 'skipped':
                        breakfast_title = "Skipped"
                    meals.append({"date": d_str, "entryType": "breakfast", "title": breakfast_title, "recipeId": None})
                
                # Lunch
                if 'lunch' in day_exclusions:
                    meals.append({"date": d_str, "entryType": "lunch", "title": "Skipped", "recipeId": None})
                else:
                    lunch_title = m.get('lunch', 'Leftovers')
                    if lunch_title.lower() == 'skipped':
                        lunch_title = "Skipped"
                    meals.append({"date": d_str, "entryType": "lunch", "title": lunch_title, "recipeId": None})
                
                # Dinner
                if 'dinner' in day_exclusions:
                    meals.append({"date": d_str, "entryType": "dinner", "title": "Eating Out", "recipeId": None, "text": prep_note})
                else:
                    din_val = m.get('dinner')
                    is_uuid = False
                    if din_val:
                        try:
                            import uuid
                            uuid.UUID(str(din_val))
                            is_uuid = True
                        except ValueError:
                            is_uuid = False
                    
                    if is_uuid:
                        # Deterministic Safety Check: Verify ID exists in catalogue
                        if din_val in catalogue_ids:
                            meals.append({"date": d_str, "entryType": "dinner", "title": "", "recipeId": din_val, "text": prep_note})
                        else:
                            logger.warning(f"[Plan Generation] WARNING: AI hallucinated recipe ID {din_val}. Falling back to text entry.")
                            meals.append({"date": d_str, "entryType": "dinner", "title": "Planned Dinner", "recipeId": None, "text": prep_note})
                    else:
                        meals.append({"date": d_str, "entryType": "dinner", "title": din_val or "Eating Out", "recipeId": None, "text": prep_note})
                
            logger.info("[AI] Successfully generated structured 7-day plan.")
        except Exception as e:
            logger.error(f"[AI] Plan generation failed: {e}")
            if progress_callback:
                progress_callback(f"⚠️ Meal plan generation failed: AI error ({e}). No changes were made to your existing plan. Please try again.", 100)
            return

        if progress_callback:
            progress_callback("Clearing old scheduled meals in Mealie calendar...", 70)

        # Slots a human filled in from Mealie's own Meal Planner. We neither
        # delete those entries nor schedule over them.
        protected_slots = self.clear_owned_entries(start_date_str, end_date_str)

        if protected_slots:
            kept = ", ".join(
                f"{date} {entry_type}" for date, entry_type in sorted(protected_slots)
            )
            logger.info("[Ownership] Kept %d entry/entries added in Mealie: %s", len(protected_slots), kept)
            if progress_callback:
                progress_callback(
                    f"Kept {len(protected_slots)} meal(s) you added in Mealie ({kept}); planning around them...",
                    75
                )

        if progress_callback:
            progress_callback("Scheduling new breakfasts, lunches, and dinners...", 80)
        for m in meals:
            if (m['date'], m['entryType']) in protected_slots:
                logger.info("[Ownership] Skipping %s %s; that slot is yours.", m['date'], m['entryType'])
                continue
            try:
                self.client.schedule_meal(
                    date_str=m['date'],
                    entry_type=m['entryType'],
                    title=m.get('title') or "",
                    text=m.get('text') or "",
                    recipe_id=m.get('recipeId')
                )
            except MealieAPIError as e:
                logger.error(f"Error scheduling meal {m}: {e}")
            
        sync_ok = self.shopping.sync_shopping_list(start_date_str, end_date_str, low_staples_ids, progress_callback=progress_callback, freezer_items=freezer_items, list_id=list_id)
        if not sync_ok:
            logger.warning("[Plan Generation] WARNING: Shopping list sync returned False — list may be empty.")

        if progress_callback:
            progress_callback("Sending weekly plan report email to family...", 99)
        self.notifier.send_saturday_report_email(start_date_str, end_date_str, exclude_text, freezer_items, low_staples_ids, special_requests)

        if progress_callback:
            if sync_ok:
                progress_callback("Meal plan generation complete!", 100)
            else:
                progress_callback("⚠️ Meal plan generated, but the shopping list sync failed (AI error). Click 'Refresh List' in the sidebar to retry.", 100)
        logger.info(f"Rule-based plan successfully generated and scheduled for {start_date_str} to {end_date_str}.")
