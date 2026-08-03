import os
import sys

from dotenv import load_dotenv

# Load .env file explicitly
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, '.env'))

# Add project root to path
sys.path.insert(0, REPO_ROOT)

from mealie_planner.unified_client import UnifiedMealieClient


def main():
    client = UnifiedMealieClient()
    
    print("Fetching all recipes from Mealie...")
    recipes = client.get_all_recipes()
    
    recipe_api_count = 0
    
    for r in recipes:
        slug = r.get('slug')
        if not slug:
            continue
            
        # Get full recipe details to inspect extras
        details = client.get_recipe_details(slug)
        extras = details.get('extras') or {}
        source = extras.get('nutrition_source')
        
        if source == 'recipe-api':
            recipe_api_count += 1
            print(f"Clearing 'recipe-api' source for recipe: '{details.get('name')}' ({slug})")
            
            # Prepare patch payload to clear nutrition_source and reset nutrition
            patch_payload = {
                "nutrition": {
                    "calories": "",
                    "proteinContent": "",
                    "carbohydrateContent": "",
                    "fatContent": "",
                    "fiberContent": "",
                    "sodiumContent": "",
                    "sugarContent": "",
                    "cholesterolContent": ""
                },
                "extras": {
                    **extras,
                    "nutrition_source": None
                }
            }
            try:
                client.patch_recipe(slug, patch_payload)
                print("  Successfully cleared.")
            except Exception as e:
                print(f"  Error patching recipe {slug}: {e}")
                
    print(f"\nDone! Cleared {recipe_api_count} recipes previously tagged with 'recipe-api'.")

if __name__ == '__main__':
    main()
