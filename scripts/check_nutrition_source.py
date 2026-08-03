import os
import sys
from datetime import datetime

from dotenv import load_dotenv

# Load .env file explicitly
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, '.env'))

# Add project root to path
sys.path.insert(0, REPO_ROOT)

from mealie_planner.ai_client import AIClient
from mealie_planner.recipe_nutrition import RecipeNutrition
from mealie_planner.unified_client import UnifiedMealieClient
from mealie_planner.utils import get_active_week_range


def main():
    client = UnifiedMealieClient()
    ai = AIClient()
    nutrition_service = RecipeNutrition(client, ai)
    
    current_start, current_end = get_active_week_range()
    start_str = current_start.strftime("%Y-%m-%d")
    end_str = current_end.strftime("%Y-%m-%d")
    
    print(f"Checking dinners for week: {start_str} to {end_str}")
    plans = client.get_meal_plan(start_str, end_str)
    
    dinners = [p for p in plans if p['entryType'] == 'dinner' and p.get('recipeId')]
    if not dinners:
        print("No dinners found in the current week's plan.")
        return
        
    print(f"Found {len(dinners)} dinners:")

    # Bulk fetch recipe details to avoid N+1 queries
    recipe_ids = list({d['recipeId'] for d in dinners})
    recipe_details_map = client.get_recipes_details_bulk(recipe_ids)

    for d in dinners:
        recipe_id = d['recipeId']
        recipe = recipe_details_map.get(recipe_id, {})
        name = recipe.get('name')
        has_nut = recipe.get('nutrition') and recipe['nutrition'].get('calories')
        extras = recipe.get('extras') or {}
        source = extras.get('nutrition_source')
        print(f"\n- Recipe: '{name}'")
        print(f"  Has Nutrition Facts: {bool(has_nut)}")
        if has_nut:
            print(f"    Calories: {recipe['nutrition'].get('calories')}")
        print(f"  Extras: {extras}")
        print(f"  Nutrition Source: {source}")
        
        # Test fetching from recipe-api if not set
        if not source:
            print(f"  [Test] Fetching from recipe-api.com for '{name}'...")
            api_data = nutrition_service.fetch_nutrition_from_recipe_api(name)
            if api_data:
                print("    [Test] Found matching recipe on recipe-api.com!")
                print(f"    [Test] Calories returned: {api_data.get('calories')}")
            else:
                print("    [Test] No matching recipe found on recipe-api.com.")

if __name__ == '__main__':
    main()
