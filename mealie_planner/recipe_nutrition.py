import copy
import json
import logging
from datetime import datetime, timedelta

import requests

from .config import (
    _RECIPE_NUTRITION_IMPUTATION_SKILL_DEFINITION,
    BREAKFAST_PROFILES,
    LUNCH_LEFTOVER_PROFILE,
    LUNCH_SANDWICH_PROFILE,
    RDA,
    RECIPE_API_KEY,
)
from .exceptions import MealieAPIError
from .models import RecipeNutritionImputation
from .utils import extract_ingredient_texts

logger = logging.getLogger(__name__)

# Cache of computed week nutrition keyed by date range; each entry stores a
# signature of the meal plan contents so any plan change invalidates it. This
# avoids re-running the recipe-api/AI imputation pipeline on every page load.
_nutrition_cache = {}


def _plan_signature(meal_plans):
    return json.dumps(
        sorted(
            [p.get('id'), p.get('date'), p.get('entryType'), p.get('recipeId'), p.get('title')]
            for p in meal_plans
        ),
        default=str,
    )


class RecipeNutrition:
    def __init__(self, mealie_client, ai_client):
        self.client = mealie_client
        self.ai = ai_client

    def fetch_nutrition_from_recipe_api(self, recipe_name):
        """Fetch recipe details and nutrition from recipe-api.com using search and get endpoints."""
        if not RECIPE_API_KEY:
            logger.info("[RecipeAPI] RECIPE_API_KEY is not set.")
            return None
        
        headers = {
            "X-API-Key": RECIPE_API_KEY,
            "Accept": "application/json"
        }
        
        try:
            logger.info(f"[RecipeAPI] Searching for recipe: '{recipe_name}'")
            search_url = "https://recipe-api.com/api/v1/recipes"
            params = {"q": recipe_name, "per_page": 1}
            r = requests.get(search_url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            search_data = r.json()
            
            recipes = search_data.get("data", [])
            if not recipes:
                logger.info(f"[RecipeAPI] No matching recipes found for: '{recipe_name}'")
                return None
                
            recipe_id = recipes[0].get("id")
            if not recipe_id:
                return None
                
            logger.info(f"[RecipeAPI] Fetching full details for recipe ID: {recipe_id}")
            detail_url = f"https://recipe-api.com/api/v1/recipes/{recipe_id}"
            r_detail = requests.get(detail_url, headers=headers, timeout=10)
            r_detail.raise_for_status()
            detail_data = r_detail.json()
            
            recipe_obj = detail_data.get("data", {})
            nutrition = recipe_obj.get("nutrition", {})
            per_serving = nutrition.get("per_serving")
            if not per_serving:
                logger.info(f"[RecipeAPI] No per-serving nutrition details found for: '{recipe_name}'")
                return None
                
            logger.info(f"[RecipeAPI] Successfully retrieved nutrition for: '{recipe_name}'")
            return {
                "calories": str(per_serving.get("calories") or 0.0),
                "proteinContent": str(per_serving.get("protein_g") or 0.0),
                "carbohydrateContent": str(per_serving.get("carbohydrates_g") or 0.0),
                "fatContent": str(per_serving.get("fat_g") or 0.0),
                "fiberContent": str(per_serving.get("fiber_g") or 0.0),
                "sodiumContent": str(per_serving.get("sodium_mg") or 0.0),
                "sugarContent": str(per_serving.get("sugar_g") or 0.0),
                "cholesterolContent": str(per_serving.get("cholesterol_mg") or 0.0),
                "_source": "recipe-api"
            }
        except Exception as e:
            logger.error(f"[RecipeAPI] Request to recipe-api.com failed: {e}")
            return None
    def simplify_recipe_name_with_ai(self, recipe_name):
        """Simplify a complex recipe name into its most generic, common 2-3 word base dish name."""
        prompt = (
            "You are an expert culinary assistant.\n"
            f"Simplify the following recipe name into its most generic, common 2-3 word base dish name (e.g. 'Crispy Black Bean Tacos with Cilantro Lime Sauce' -> 'Black Bean Tacos', 'Turkey Smash Burger' -> 'Turkey Burger', 'Mom\\'s Classic Homemade Lasagna' -> 'Lasagna').\n"
            "Return ONLY the simplified name as plain text. Do not include quotes, punctuation, or any other words.\n\n"
            f"Recipe Name: {recipe_name}"
        )
        try:
            res = self.ai.call(prompt, expect_json=False)
            simplified = res.strip().strip('"').strip("'").strip()
            logger.info(f"[Nutrition] Simplified recipe name '{recipe_name}' to '{simplified}'")
            return simplified
        except Exception as e:
            logger.error(f"[Nutrition] Failed to simplify recipe name '{recipe_name}': {e}")
            return None

    def impute_nutrition_with_ai(self, recipe_details):
        """Estimate nutritional values for a recipe missing data, trying Recipe API first, then falling back to AI."""
        recipe_name = recipe_details.get('name')
        if recipe_name:
            # 1. Try Recipe API with original name first
            logger.info(f"[Nutrition] Querying recipe-api.com with original name: '{recipe_name}'")
            api_data = self.fetch_nutrition_from_recipe_api(recipe_name)
            if api_data:
                return api_data
                
            # 2. Try Recipe API with simplified name
            simplified_name = self.simplify_recipe_name_with_ai(recipe_name)
            if simplified_name and simplified_name != recipe_name:
                logger.info(f"[Nutrition] Querying recipe-api.com with simplified name: '{simplified_name}'")
                api_data = self.fetch_nutrition_from_recipe_api(simplified_name)
                if api_data:
                    return api_data

        # 2. Fallback to AI imputation
        ingredients = extract_ingredient_texts(recipe_details)
        servings = recipe_details.get('recipeServings') or recipe_details.get('recipeYield') or '4'
        description = recipe_details.get('description') or ''
        
        prompt = (
            """You are an expert in the 'Recipe Nutrition Imputation Skill'.

""" +
            _RECIPE_NUTRITION_IMPUTATION_SKILL_DEFINITION +
            """

### CONTEXT FOR THIS INVOCATION:
""" +
            f"Recipe Name: {recipe_name}\n" +
            f"Description: {description}\n" +
            f"Servings: {servings}\n" +
            f"Ingredients: {', '.join(ingredients)}\n\n" +
            "Return ONLY the JSON object as specified in the skill definition."
        )

        try:
            raw = self.ai.call(prompt, response_schema=RecipeNutritionImputation)
            res = RecipeNutritionImputation.model_validate_json(raw).model_dump()
            res['_source'] = 'ai'
            return res
        except Exception as e:
            logger.error(f"[AI] Nutrition imputation failed: {e}")
            return None

    def _save_nutrition_to_mealie(self, recipe, imputed):
        """Helper to save imputed or fetched nutrition details back to Mealie database and cache."""
        try:
            slug = recipe.get('slug')
            if slug:
                existing_extras = recipe.get('extras') or {}
                source = imputed.get('_source', 'ai')
                patch_payload = {
                    "nutrition": {
                        "calories": imputed.get("calories"),
                        "proteinContent": imputed.get("proteinContent"),
                        "carbohydrateContent": imputed.get("carbohydrateContent"),
                        "fatContent": imputed.get("fatContent"),
                        "fiberContent": imputed.get("fiberContent"),
                        "sodiumContent": imputed.get("sodiumContent"),
                        "sugarContent": imputed.get("sugarContent"),
                        "cholesterolContent": imputed.get("cholesterolContent")
                    },
                    "extras": {
                        **existing_extras,
                        "nutrition_source": source
                    }
                }
                self.client.patch_recipe(slug, patch_payload)
                logger.info(f"[Nutrition] Saved nutrition ({source}) back to Mealie for recipe: {recipe['name']}")
                
                # Update local client details cache too
                r_id = recipe.get('id')
                if r_id and r_id in self.client._recipe_details_cache:
                    self.client._recipe_details_cache[r_id]['nutrition'] = patch_payload["nutrition"]
                    self.client._recipe_details_cache[r_id]['extras'] = patch_payload["extras"]
                if slug and slug in self.client._recipe_details_cache:
                    self.client._recipe_details_cache[slug]['nutrition'] = patch_payload["nutrition"]
                    self.client._recipe_details_cache[slug]['extras'] = patch_payload["extras"]
        except Exception as patch_err:
            logger.error(f"[Nutrition] Failed to save nutrition back to Mealie: {patch_err}")

    def calculate_nutrition_for_range(self, start_date_str, end_date_str):
        """Calculate aggregate and daily nutritional totals for a date range."""
        try:
            meal_plans = self.client.get_meal_plan(start_date_str, end_date_str)
        except MealieAPIError as e:
            logger.error(f"Error fetching meal plans for nutrition: {e}")
            return {}, {}

        cache_key = (start_date_str, end_date_str)
        plan_sig = _plan_signature(meal_plans)
        cached = _nutrition_cache.get(cache_key)
        if cached and cached[0] == plan_sig:
            return copy.deepcopy(cached[1]), copy.deepcopy(cached[2])

        # Initialize tracking dicts
        daily_nutrients = {}
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        
        curr = start_date
        while curr <= end_date:
            daily_nutrients[curr.strftime("%Y-%m-%d")] = {k: 0.0 for k in RDA.keys()}
            curr += timedelta(days=1)

        category_totals = {
            "breakfast": {k: 0.0 for k in RDA.keys()},
            "lunch": {k: 0.0 for k in RDA.keys()},
            "dinner": {k: 0.0 for k in RDA.keys()}
        }
        category_counts = {"breakfast": 0, "lunch": 0, "dinner": 0}

        for item in meal_plans:
            # Normalize date to YYYY-MM-DD
            d_str = item['date'][:10]
            if d_str not in daily_nutrients:
                continue
                
            entry_type = item['entryType']
            title = item.get('title') or ""
            recipe_id = item.get('recipeId')
            
            nut_data = None
            
            # 1. Process Breakfasts
            if entry_type == "breakfast":
                nut_data = BREAKFAST_PROFILES.get(title)
                if nut_data:
                    category_counts["breakfast"] += 1
            
            # 2. Process Lunches
            elif entry_type == "lunch":
                if "leftover" in title.lower():
                    nut_data = LUNCH_LEFTOVER_PROFILE
                    category_counts["lunch"] += 1
                elif "sandwich" in title.lower() or "pb&j" in title.lower():
                    nut_data = LUNCH_SANDWICH_PROFILE
                    category_counts["lunch"] += 1

            # 3. Process Dinners
            elif entry_type == "dinner" and recipe_id:
                try:
                    r = self.client.get_recipe_details(recipe_id)
                    extras = r.get('extras') or {}
                    source = extras.get('nutrition_source')
                    
                    # Scenario A: Already fetched from recipe-api in the past, use Mealie nutrition directly
                    if source == 'recipe-api':
                        raw_nut = r.get('nutrition') or {}
                        nut_data = {
                            "calories": float(raw_nut.get('calories') or 0),
                            "protein": float(raw_nut.get('proteinContent') or 0),
                            "carbs": float(raw_nut.get('carbohydrateContent') or 0),
                            "fat": float(raw_nut.get('fatContent') or 0),
                            "fiber": float(raw_nut.get('fiberContent') or 0),
                            "sodium": float(raw_nut.get('sodiumContent') or 0),
                            "sugar": float(raw_nut.get('sugarContent') or 0),
                            "cholesterol": float(raw_nut.get('cholesterolContent') or 0)
                        }
                    else:
                        # Scenario B: Not retrieved from recipe-api yet. Query recipe-api first!
                        recipe_name = r.get('name')
                        api_data = None
                        if recipe_name:
                            logger.info(f"[Nutrition] Querying recipe-api.com first for recipe: '{recipe_name}'")
                            api_data = self.fetch_nutrition_from_recipe_api(recipe_name)
                            if not api_data:
                                simplified_name = self.simplify_recipe_name_with_ai(recipe_name)
                                if simplified_name and simplified_name != recipe_name:
                                    logger.info(f"[Nutrition] Querying recipe-api.com with simplified name: '{simplified_name}'")
                                    api_data = self.fetch_nutrition_from_recipe_api(simplified_name)
                                    
                        if api_data:
                            # Found on recipe-api! Save it to Mealie and use it
                            nut_data = {
                                "calories": float(api_data.get('calories') or 0),
                                "protein": float(api_data.get('proteinContent') or 0),
                                "carbs": float(api_data.get('carbohydrateContent') or 0),
                                "fat": float(api_data.get('fatContent') or 0),
                                "fiber": float(api_data.get('fiberContent') or 0),
                                "sodium": float(api_data.get('sodiumContent') or 0),
                                "sugar": float(api_data.get('sugarContent') or 0),
                                "cholesterol": float(api_data.get('cholesterolContent') or 0)
                            }
                            self._save_nutrition_to_mealie(r, api_data)
                        else:
                            # Scenario C: recipe-api failed to find anything.
                            # Fall back to existing Mealie nutrition facts (if present)
                            raw_nut = r.get('nutrition') or {}
                            if raw_nut.get('calories'):
                                logger.error(f"[Nutrition] recipe-api failed. Falling back to existing Mealie nutrition facts for: {r['name']}")
                                nut_data = {
                                    "calories": float(raw_nut.get('calories') or 0),
                                    "protein": float(raw_nut.get('proteinContent') or 0),
                                    "carbs": float(raw_nut.get('carbohydrateContent') or 0),
                                    "fat": float(raw_nut.get('fatContent') or 0),
                                    "fiber": float(raw_nut.get('fiberContent') or 0),
                                    "sodium": float(raw_nut.get('sodiumContent') or 0),
                                    "sugar": float(raw_nut.get('sugarContent') or 0),
                                    "cholesterol": float(raw_nut.get('cholesterolContent') or 0)
                                }
                            else:
                                # Scenario D: No recipe-api, no Mealie facts. Fall back to AI imputation
                                logger.info(f"[Nutrition] No data found on recipe-api or Mealie. Falling back to AI imputation for: {r['name']}")
                                ingredients = extract_ingredient_texts(r)
                                servings = r.get('recipeServings') or r.get('recipeYield') or '4'
                                description = r.get('description') or ''
                                
                                prompt = (
                                    """You are an expert in the 'Recipe Nutrition Imputation Skill'.

""" +
                                    _RECIPE_NUTRITION_IMPUTATION_SKILL_DEFINITION +
                                    """

### CONTEXT FOR THIS INVOCATION:
""" +
                                    f"Recipe Name: {r.get('name')}\n" +
                                    f"Description: {description}\n" +
                                    f"Servings: {servings}\n" +
                                    f"Ingredients: {', '.join(ingredients)}\n\n" +
                                    "Return ONLY the JSON object as specified in the skill definition."
                                )

                                try:
                                    raw = self.ai.call(prompt, response_schema=RecipeNutritionImputation)
                                    imputed = RecipeNutritionImputation.model_validate_json(raw).model_dump()
                                    imputed['_source'] = 'ai'
                                    
                                    nut_data = {
                                        "calories": float(imputed.get('calories') or 0),
                                        "protein": float(imputed.get('proteinContent') or 0),
                                        "carbs": float(imputed.get('carbohydrateContent') or 0),
                                        "fat": float(imputed.get('fatContent') or 0),
                                        "fiber": float(imputed.get('fiberContent') or 0),
                                        "sodium": float(imputed.get('sodiumContent') or 0),
                                        "sugar": float(imputed.get('sugarContent') or 0),
                                        "cholesterol": float(imputed.get('cholesterolContent') or 0)
                                    }
                                    self._save_nutrition_to_mealie(r, imputed)
                                except Exception as ai_err:
                                    logger.error(f"[AI] Nutrition imputation failed for {r['name']}: {ai_err}")
                                    
                    if nut_data:
                        category_counts["dinner"] += 1
                        
                except Exception as e:
                    logger.error(f"Error processing dinner nutrition for {recipe_id}: {e}")

            # Add to daily and category totals
            if nut_data:
                for k in RDA.keys():
                    val = float(nut_data.get(k, 0))
                    daily_nutrients[d_str][k] += val
                    category_totals[entry_type][k] += val

        # Calculate averages (weighted by days active)
        averages = {}
        for k in RDA.keys():
            avg_bf = (category_totals["breakfast"][k] / category_counts["breakfast"]) if category_counts["breakfast"] > 0 else 0.0
            avg_ln = (category_totals["lunch"][k] / category_counts["lunch"]) if category_counts["lunch"] > 0 else 0.0
            avg_dn = (category_totals["dinner"][k] / category_counts["dinner"]) if category_counts["dinner"] > 0 else 0.0
            
            averages[k] = round(avg_bf + avg_ln + avg_dn, 1)

        _nutrition_cache[cache_key] = (plan_sig, copy.deepcopy(daily_nutrients), copy.deepcopy(averages))
        return daily_nutrients, averages

def calculate_nutrition_for_range(start_date_str, end_date_str):
    """Standalone helper to run nutrition calculation with fresh clients."""
    from .ai_client import AIClient
    from .unified_client import UnifiedMealieClient
    client = UnifiedMealieClient()
    ai = AIClient()
    nutrition = RecipeNutrition(client, ai)
    return nutrition.calculate_nutrition_for_range(start_date_str, end_date_str)
