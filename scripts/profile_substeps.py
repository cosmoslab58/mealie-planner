import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# Add project root to path
sys.path.insert(0, '/app')

import json

from mealie_planner import config
from mealie_planner.ai_client import AIClient
from mealie_planner.email_notifier import send_email
from mealie_planner.parsers import parse_exclusions
from mealie_planner.unified_client import UnifiedMealieClient


def run_substep_profile():
    print("====================================================")
    print("      PROFILING DETAILED PLAN GENERATION STEPS       ")
    print("====================================================\n")

    client = UnifiedMealieClient()
    ai = AIClient()
    
    # 1. Exclusion Parsing AI Call
    print("[1] Profiling Exclusion Parsing AI Call...")
    t0 = time.perf_counter()
    exclusions = parse_exclusions("No dinners on Wednesday")
    t_excl = time.perf_counter() - t0
    print(f"    Exclusions parsed: {exclusions}")
    print(f"    Duration: {t_excl:.3f}s\n")

    # 2. Recipe Catalog Retrieval
    print("[2] Profiling Recipe Catalog Retrieval (API fallback)...")
    t0 = time.perf_counter()
    all_recipes = client.get_all_recipes()
    t_catalog = time.perf_counter() - t0
    print(f"    Recipes retrieved: {len(all_recipes)}")
    print(f"    Duration: {t_catalog:.3f}s\n")

    # 3. Setup test date range
    today = datetime.now(ZoneInfo(config.TIMEZONE))
    days_to_sat = (5 - today.weekday() + 7) % 7
    start_date = today + timedelta(days=days_to_sat)
    end_date = start_date + timedelta(days=6)
    start_date_str = start_date.strftime("%Y-%m-%d")
    end_date_str = end_date.strftime("%Y-%m-%d")
    
    num_days = (end_date - start_date).days + 1
    dinner_days = [
        (start_date + timedelta(days=i)).strftime("%A")
        for i in range(num_days)
        if 'dinner' not in exclusions.get((start_date + timedelta(days=i)).strftime("%A"), [])
    ]
    num_dinners = len(dinner_days)

    recipe_catalogue = [
        {
            "id": r["id"],
            "name": r["name"],
            "description": (r.get("description") or "")[:120],
            "tags": r.get("tags", []),
            "fiber_g": r.get("fiber_content"),
            "ingredients": r.get("ingredients", []),
            "instructions_preview": " ".join(r.get("instructions", []))[:120]
        }
        for r in all_recipes
    ]

    # 4. Meal Selection AI Call
    print("[4] Profiling Weekly Meal Selection AI Call...")
    selection_prompt = (
        """You are an expert in the 'Mealie Weekly Meal Selection Skill'.

""" +
        config._WEEKLY_MEAL_SELECTION_SKILL_DEFINITION +
        """

### BANNED RECIPES SKILL RULES:
""" +
        config._BANNED_RECIPES_SKILL_DEFINITION +
        """

### CONTEXT FOR THIS INVOCATION:
""" +
        f"- **Family Dietary Rules & Preferences**: {config.FAMILY_DIETARY_RULES_PROMPT}\n" +
        f"- **Dinner nights this week**: {', '.join(dinner_days)}\n" +
        f"- **Number of dinners to plan**: {num_dinners}\n" +
        "- **Freezer items to prioritize**: salmon\n" +
        "- **Special requests from the family**: High fiber, vegetarian priority\n" +
        "- **Recently planned recipes**: none\n\n" +
        "### RECIPE CATALOGUE (JSON):\n" +
        f"{recipe_catalogue}\n\n" +
        "Return ONLY the JSON object as specified in the skill definition."
    )
    
    t0 = time.perf_counter()
    raw = ai.call(selection_prompt, expect_json=True, temperature=0.7)
    selected = json.loads(raw)
    t_select = time.perf_counter() - t0
    print(f"    Selected IDs: {selected.get('dinner_ids')}")
    print(f"    Duration: {t_select:.3f}s\n")

    # 5. Clear Old Calendar Meals
    print("[5] Profiling Clearing of Old Calendar Meals...")
    t0 = time.perf_counter()
    existing_plans = client.get_meal_plan(start_date_str, end_date_str)
    num_deleted = len(existing_plans)
    for p in existing_plans:
        client.delete_meal_plan_entry(p['id'])
    t_clear_cal = time.perf_counter() - t0
    print(f"    Deleted {num_deleted} old plans.")
    print(f"    Duration: {t_clear_cal:.3f}s\n")

    # Prepare meals list for scheduling (simulate 21 meals)
    meals = []
    current_date = start_date
    recipe_index = 0
    selected_ids = selected.get('dinner_ids', [])
    id_to_recipe = {r["id"]: r for r in all_recipes}
    clean_recipes = [id_to_recipe[rid] for rid in selected_ids if rid in id_to_recipe]
    remaining = [r for r in all_recipes if r["id"] not in selected_ids]
    clean_recipes = clean_recipes + remaining
    
    while current_date <= end_date:
        d_str = current_date.strftime("%Y-%m-%d")
        day_name = current_date.strftime("%A")
        day_exclusions = exclusions.get(day_name, [])
        
        if 'breakfast' in day_exclusions:
            meals.append({"date": d_str, "entryType": "breakfast", "title": "Skipped", "recipeId": None})
        else:
            meals.append({"date": d_str, "entryType": "breakfast", "title": "Cereal & Milk", "recipeId": None})
            
        if 'lunch' in day_exclusions:
            meals.append({"date": d_str, "entryType": "lunch", "title": "Skipped", "recipeId": None})
        else:
            meals.append({"date": d_str, "entryType": "lunch", "title": "Leftovers", "recipeId": None})
        
        if 'dinner' in day_exclusions:
            meals.append({"date": d_str, "entryType": "dinner", "title": "Eating Out", "recipeId": None})
        else:
            if clean_recipes:
                recipe = clean_recipes[recipe_index % len(clean_recipes)]
                meals.append({"date": d_str, "entryType": "dinner", "title": "", "recipeId": recipe['id']})
                recipe_index += 1
            else:
                meals.append({"date": d_str, "entryType": "dinner", "title": "TBD", "recipeId": None})
        current_date += timedelta(days=1)

    # 6. Schedule New Meals (sequential HTTP POSTs)
    print(f"[6] Profiling Scheduling of New Meals ({len(meals)} sequential POST requests)...")
    t0 = time.perf_counter()
    for m in meals:
        client.schedule_meal(
            date_str=m['date'],
            entry_type=m['entryType'],
            title=m.get('title') or "",
            recipe_id=m.get('recipeId')
        )
    t_sched = time.perf_counter() - t0
    print(f"    Scheduled {len(meals)} meals.")
    print(f"    Duration: {t_sched:.3f}s\n")

    # 7. Shopping List Sync
    print("[7] Profiling Shopping List Sync (Fetch + LLM + Write)...")
    
    t_fetch_start = time.perf_counter()
    # Fetch active plans & staples
    meal_plans = client.get_meal_plan(start_date_str, end_date_str)
    staples = client.get_shopping_list_items_for_list(config.STAPLES_LIST_ID)
    
    raw_recipe_ingredients = []
    recipe_ids_to_fetch = {p['recipeId'] for p in meal_plans if p.get('entryType') == 'dinner' and p.get('recipeId')}
    if recipe_ids_to_fetch:
        details_map = client.get_recipes_details_bulk(list(recipe_ids_to_fetch))
    else:
        details_map = {}

    for p in meal_plans:
        if p['entryType'] == 'dinner' and p.get('recipeId'):
            r_details = details_map.get(p['recipeId'], {})
            for ing in r_details.get('recipeIngredient', []):
                disp = ing.get('display') or ing.get('originalText') or ""
                if disp.strip():
                    raw_recipe_ingredients.append(disp.strip())
    t_sync_fetch = time.perf_counter() - t_fetch_start
    
    # LLM sync call
    t_llm_start = time.perf_counter()
    payload = {
        "ingredients": raw_recipe_ingredients,
        "staples": [item['note'] for item in staples],
        "low_staples": []
    }
    prompt = (
        """You are an expert in the 'Shopping List Sync Skill'.

""" +
        config._SHOPPING_LIST_SYNC_SKILL_DEFINITION +
        """

### CONTEXT FOR THIS INVOCATION:
""" +
        f"Input Data: {json.dumps(payload)}\n\n" +
        "Return ONLY the JSON array of objects as specified in the skill definition."
    )
    ai_response = ai.call(prompt, expect_json=True)
    ai_output = json.loads(ai_response)
    t_sync_llm = time.perf_counter() - t_llm_start
    
    # Write to Mealie list
    t_write_start = time.perf_counter()
    client.clear_shopping_list(config.ACTIVE_LIST_ID)
    ingredients_list = []
    for item in ai_output:
        name = item.get('name', '').strip()
        if name:
            ingredients_list.append({
                "shoppingListId": config.ACTIVE_LIST_ID,
                "note": name,
                "quantity": item.get('quantity', 1.0),
                "checked": False
            })
    if ingredients_list:
        client.add_shopping_list_items_bulk(ingredients_list)
    t_sync_write = time.perf_counter() - t_write_start
    
    t_sync_total = time.perf_counter() - t_fetch_start
    print(f"    Sync Fetch Data: {t_sync_fetch:.3f}s")
    print(f"    Sync AI Call  : {t_sync_llm:.3f}s")
    print(f"    Sync API Write: {t_sync_write:.3f}s")
    print(f"    Total Sync Time: {t_sync_total:.3f}s\n")

    # 8. Email Delivery
    print("[8] Profiling SMTP Email Delivery...")
    t0 = time.perf_counter()
    try:
        send_email("Profiling test", "This is a profiling test body.")
        email_result = "Success"
    except Exception as e:
        email_result = f"Failed ({e})"
    t_email = time.perf_counter() - t0
    print(f"    Result: {email_result}")
    print(f"    Duration: {t_email:.3f}s\n")

    # Final detailed breakdown
    print("====================================================")
    print("             DETAILED SUB-STEP BREAKDOWN            ")
    print("====================================================")
    print(f"1. Exclusion Parsing AI Call            : {t_excl:>7.3f}s")
    print(f"2. Recipe Catalog API Fetch             : {t_catalog:>7.3f}s")
    print(f"3. Meal Selection AI Call               : {t_select:>7.3f}s")
    print(f"4. Delete Old Meal Plans                : {t_clear_cal:>7.3f}s")
    print(f"5. Schedule New Meals (21 sequential)   : {t_sched:>7.3f}s")
    print(f"6. Shopping List Sync (Total)           : {t_sync_total:>7.3f}s")
    print(f"   - Fetching details from Mealie       :   {t_sync_fetch:>5.3f}s")
    print(f"   - AI processing of list              :   {t_sync_llm:>5.3f}s")
    print(f"   - Writing to active list             :   {t_sync_write:>5.3f}s")
    print(f"7. SMTP Email Delivery                  : {t_email:>7.3f}s")
    print("----------------------------------------------------")
    total = t_excl + t_catalog + t_select + t_clear_cal + t_sched + t_sync_total + t_email
    print(f"Calculated Total                        : {total:>7.3f}s")
    print("====================================================\n")

if __name__ == "__main__":
    run_substep_profile()
