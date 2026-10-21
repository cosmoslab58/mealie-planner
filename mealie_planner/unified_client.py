import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# Run startup checks (verifying submodules and config templates)
from .startup_check import run_startup_checks

run_startup_checks()

# Add mealie-mcp-server/src to the python path to import MealieFetcher
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_src_dir = os.path.join(base_dir, "mealie-mcp-server", "src")
if mcp_src_dir not in sys.path:
    sys.path.insert(0, mcp_src_dir)

from mealie import MealieFetcher

logger = logging.getLogger("mealie-planner-unified")
from .config import _INGREDIENT_STANDARDIZATION_SKILL_DEFINITION
from .models import StandardizedIngredients

_recipe_details_cache = {}
# Per-key fetch timestamps so cached recipe details can expire (see RECIPE_CACHE_TTL).
_recipe_details_cache_ts = {}
_recipes_cache = None
_recipes_cache_ts = 0
# Default time-to-live (seconds) for cached recipe details. Bounds staleness when a
# recipe is edited out-of-band (e.g. by the MCP chat subprocess, whose cache is separate).
RECIPE_CACHE_TTL = int(os.getenv('RECIPE_CACHE_TTL', '600'))

def _record_owned_entry(entry_id, date_str=None, entry_type=None):
    """Best-effort ownership bookkeeping; never let it break scheduling."""
    if not entry_id:
        return
    try:
        from .database import record_plan_entry
        record_plan_entry(entry_id, date_str, entry_type)
    except Exception as e:
        logger.warning("Could not record meal-plan entry ownership for %s: %s", entry_id, e)


def _forget_owned_entry(entry_id):
    """Best-effort ownership bookkeeping; never let it break deletion."""
    if not entry_id:
        return
    try:
        from .database import forget_plan_entry
        forget_plan_entry(entry_id)
    except Exception as e:
        logger.warning("Could not clear meal-plan entry ownership for %s: %s", entry_id, e)


def is_retryable_exception(exception):
    """Determine if a request failure should trigger a retry attempt."""
    if isinstance(exception, httpx.RequestError):
        return True
    if isinstance(exception, httpx.HTTPStatusError):
        return exception.response.status_code >= 500
    if isinstance(exception, ConnectionError):
        return True
    return False

class UnifiedMealieClient(MealieFetcher):
    """
    A unified Mealie client that inherits from the vendored MealieFetcher
    and adds missing or legacy compatibility methods.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(UnifiedMealieClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        if self._initialized:
            return
            
        base_url = base_url or os.getenv('MEALIE_API_URL') or os.getenv('MEALIE_BASE_URL', 'http://mealie:9000')
        api_key = api_key or os.getenv('MEALIE_TOKEN') or os.getenv('MEALIE_API_KEY')
        
        if not api_key:
            raise ValueError("Mealie API Key (MEALIE_TOKEN or MEALIE_API_KEY) is missing.")
            
        super().__init__(base_url=base_url, api_key=api_key)
        self._recipe_details_cache = _recipe_details_cache
        self._recipe_details_cache_ts = _recipe_details_cache_ts
        self._initialized = True

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(is_retryable_exception),
        reraise=True
    )
    def _handle_request(self, method: str, url: str, **kwargs):
        """Overridden request handler that wraps all requests in exponential backoff retries."""
        return super()._handle_request(method, url, **kwargs)

    # --- Legacy Compatibility Aliases ---
    
    @property
    def api_url(self):
        """Backwards compatibility for scripts using client.api_url"""
        return self._client.base_url

    @property
    def headers(self):
        """Backwards compatibility for scripts using client.headers"""
        return self._client.headers

    def get_all_recipes(self) -> List[Dict[str, Any]]:
        """Legacy alias for get_recipes(per_page=500), cached for performance."""
        global _recipes_cache, _recipes_cache_ts
        now = time.time()
        if _recipes_cache is not None and (now - _recipes_cache_ts) < RECIPE_CACHE_TTL:
            return _recipes_cache

        res = self.get_recipes(per_page=500)
        if isinstance(res, dict):
            _recipes_cache = res.get('items', [])
            _recipes_cache_ts = now
            return _recipes_cache
        return []

    def get_recipe_details(self, recipe_id_or_slug: str) -> Dict[str, Any]:
        """Fetch full details of a specific recipe, using a local TTL cache."""
        cached = self._recipe_details_cache.get(recipe_id_or_slug)
        if cached is not None:
            age = time.time() - self._recipe_details_cache_ts.get(recipe_id_or_slug, 0)
            if age < RECIPE_CACHE_TTL:
                return cached

        details = self.get_recipe(recipe_id_or_slug)
        self._recipe_details_cache[recipe_id_or_slug] = details
        self._recipe_details_cache_ts[recipe_id_or_slug] = time.time()
        return details

    def invalidate_recipe_cache(self, *keys: str):
        """Drop cached recipe details. Pass one or more id/slug keys, or no args to clear all.

        Call after mutating a recipe so the next read re-fetches fresh data."""
        global _recipes_cache, _recipes_cache_ts
        _recipes_cache = None
        _recipes_cache_ts = 0
        if not keys:
            self._recipe_details_cache.clear()
            self._recipe_details_cache_ts.clear()
            return
        for key in keys:
            self._recipe_details_cache.pop(key, None)
            self._recipe_details_cache_ts.pop(key, None)

    def get_recipes_details_bulk(self, recipe_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch full details for a list of recipes with bounded concurrency.

        Worker count is kept low to avoid overwhelming Mealie's SQLite backend."""
        now = time.time()
        to_fetch = [
            rid for rid in recipe_ids
            if self._recipe_details_cache.get(rid) is None
            or (now - self._recipe_details_cache_ts.get(rid, 0)) >= RECIPE_CACHE_TTL
        ]

        if to_fetch:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=min(4, len(to_fetch))) as executor:
                futures = {executor.submit(self.get_recipe_details, rid): rid for rid in to_fetch}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Error fetching recipe {futures[future]}: {e}")

        return {rid: self._recipe_details_cache.get(rid) for rid in recipe_ids}

    def get_mealplans(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Override get_mealplans to use correct snake_case query parameters for the Mealie API."""
        param_dict = {
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "per_page": per_page,
        }
        params = {k: v for k, v in param_dict.items() if v is not None}
        return self._handle_request("GET", "/api/households/mealplans", params=params)

    def get_meal_plan(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Legacy alias for get_mealplans with simplified return."""
        res = self.get_mealplans(start_date=start_date, end_date=end_date, per_page=100)
        if isinstance(res, dict):
            return res.get('items', [])
        return []

    def delete_meal_plan_entry(self, entry_id: str):
        """Missing method in MealieFetcher to delete a meal plan entry."""
        result = self._handle_request("DELETE", f"/api/households/mealplans/{entry_id}")
        _forget_owned_entry(entry_id)
        return result

    def schedule_meal(self, date_str: str, entry_type: str, recipe_id: Optional[str] = None, title: Optional[str] = None, text: Optional[str] = None):
        """Legacy alias for create_mealplan, adding support for prep notes (text)."""
        payload = {
            "date": date_str,
            "entryType": entry_type,
        }
        if recipe_id:
            payload["recipeId"] = recipe_id
        if title:
            payload["title"] = title
        if text:
            payload["text"] = text

        created = self._handle_request("POST", "/api/households/mealplans", json=payload)
        # Claim the entry so regeneration knows it may clear it later. Anything
        # unclaimed in Mealie's calendar was put there by a human.
        if isinstance(created, dict):
            _record_owned_entry(created.get("id"), date_str, entry_type)
        return created

    def clear_shopping_list(self, list_id: str):
        """Clear all items from a shopping list using targeted fetch."""
        items = self.get_shopping_list_items_for_list(list_id)
        item_ids_to_delete = [item['id'] for item in items if 'id' in item]
        if item_ids_to_delete:
            return self.delete_shopping_list_items_bulk(item_ids_to_delete)
        return {"message": "List already empty"}

    def add_shopping_list_items_bulk(self, items: List[Dict[str, Any]]):
        """Alias for create_shopping_list_items_bulk."""
        return self.create_shopping_list_items_bulk(items)

    def add_shopping_list_item(self, list_id: str, note: str, label_id: Optional[str] = None):
        """Legacy alias for create_shopping_list_item."""
        return self.create_shopping_list_item(shopping_list_id=list_id, note=note, label_id=label_id)
    
    def get_shopping_list_items_for_list(self, list_id: str) -> List[Dict[str, Any]]:
        """Fetch all items currently on a specific shopping list.

        (The inherited paginated get_shopping_list_items(page, per_page) remains
        available for the vendored MCP tools.)"""
        res = self.get_shopping_list(list_id)
        if isinstance(res, dict):
            return res.get('listItems', [])
        return []

    def get_users(self) -> List[Dict[str, Any]]:
        """Fetch all users (legacy compatibility)."""
        res = self._handle_request("GET", "/api/admin/users")
        if isinstance(res, dict):
            return res.get('items', [])
        return []

    def get_labels(self) -> List[Dict[str, Any]]:
        """Fetch all shopping list labels (legacy compatibility)."""
        res = self._handle_request("GET", "/api/groups/labels")
        if isinstance(res, dict):
            return res.get('items', [])
        return []

    def create_recipe_from_url(self, url: str) -> Dict[str, Any]:
        """
        Missing method to create a recipe from a URL.
        This uses the Mealie API endpoint directly.
        """
        return self._handle_request("POST", "/api/recipes/create/url", json={"url": url})

    def get_detailed_meal_plan(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Fetches meal plan entries and enriches them with full recipe details 
        (ingredients, instructions, etc.) in a single call.
        """
        plans = self.get_meal_plan(start_date, end_date)
        recipe_ids = list(set(p['recipeId'] for p in plans if p.get('recipeId')))
        
        if not recipe_ids:
            return plans
            
        details_map = self.get_recipes_details_bulk(recipe_ids)
        
        for p in plans:
            rid = p.get('recipeId')
            if rid and rid in details_map:
                p['recipe'] = details_map[rid]
                
        return plans

    def parse_raw_ingredients(self, ingredients: List[str]) -> List[Dict[str, Any]]:
        """Helper to parse a list of ingredients using Mealie's NLP ingredient parser endpoint."""
        try:
            logger.info({"message": "Parsing raw ingredients via Mealie API", "count": len(ingredients)})
            res = self._handle_request("POST", "/api/parser/ingredients", json={
                "ingredients": ingredients
            })
            parsed_ingredients = []
            for item in res:
                if isinstance(item, dict) and "ingredient" in item:
                    parsed_ingredients.append(item["ingredient"])
                else:
                    parsed_ingredients.append({"note": item.get("input") if isinstance(item, dict) else str(item)})
            return parsed_ingredients
        except Exception as e:
            logger.warning({"message": "Failed to parse ingredients via Mealie API, falling back to note field mapping", "error": str(e)})
            return [{"note": i} for i in ingredients]

    def parse_ingredients_with_ai(self, raw_text: str) -> List[Dict[str, Any]]:
        """Parse free-text ingredients using the shared AIClient and config definitions."""
        from .ai_client import AIClient
        from .parsers import parse_freezer_items

        return parse_freezer_items(AIClient(), raw_text)

    def standardize_ingredients_with_ai(self, ingredients: List[str]) -> List[str]:
        """
        Use the AI client to clean and standardize a list of ingredient strings.
        Removes brand names, extraneous instructions, and formatting noise.
        """
        from .ai_client import AIClient
        ai = AIClient()

        try:
            raw_text = "\n".join(ingredients)
            logger.info({"message": "Standardizing ingredients via AI", "count": len(ingredients)})
            
            prompt = (
                "You are an expert in the 'Ingredient Standardization Skill'.\n\n" +
                _INGREDIENT_STANDARDIZATION_SKILL_DEFINITION +
                "\n\n### CONTEXT FOR THIS INVOCATION:\n" +
                f"Input List:\n{raw_text}\n\n" +
                "Return ONLY a JSON array of strings."
            )

            ai_response = ai.call(prompt, response_schema=StandardizedIngredients)
            return StandardizedIngredients.model_validate_json(ai_response).root
        except Exception as e:
            logger.error(f"Error standardizing ingredients with AI: {e}")
            return ingredients
