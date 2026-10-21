import logging
import os
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

# Run startup checks (verifying submodules and config templates)
from mealie_planner.startup_check import run_startup_checks

run_startup_checks()

# Add mealie-mcp-server/src to the python path to import vendored tools and client
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_src_dir = os.path.join(base_dir, "mealie-mcp-server", "src")

# Critical: Ensure mcp_src_dir is at the VERY FRONT to avoid shadowing 'utils' or 'mealie'
# Python automatically adds the script's directory to sys.path[0]. We must remove it
# or replace it to prevent its 'utils.py' from being found first.
script_dir = os.path.dirname(os.path.abspath(__file__))
while script_dir in sys.path:
    sys.path.remove(script_dir)
if mcp_src_dir not in sys.path:
    sys.path.insert(0, mcp_src_dir)
if base_dir not in sys.path:
    sys.path.append(base_dir)

from tools import register_all_tools

from mealie_planner.unified_client import UnifiedMealieClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mealie-planner-mcp")

# Initialize the Unified Client
try:
    mealie = UnifiedMealieClient()
except Exception as e:
    logger.error(f"Failed to initialize UnifiedMealieClient: {e}")
    raise

# Initialize FastMCP
mcp = FastMCP("mealie-planner")

def resolve_ingredient_food_and_unit(mealie, item: dict) -> tuple:
    """Ensure that the ingredient's food and unit exist in Mealie, creating them if necessary.
    Returns: (unit_dict, food_dict) ready for nested recipe ingredient payload.
    """
    unit = item.get('unit')
    food = item.get('food')
    
    resolved_unit = None
    if unit and unit.get('name'):
        unit_id = unit.get('id')
        unit_name = unit.get('name')
        if not unit_id:
            try:
                res = mealie._handle_request("GET", "/api/units", params={"per_page": 1000})
                all_units = res.get('items', []) if isinstance(res, dict) else res
                existing = next((u for u in all_units if u.get('name', '').lower() == unit_name.lower()), None)
                if existing:
                    unit_id = existing['id']
                else:
                    new_u = mealie._handle_request("POST", "/api/units", json={
                        "name": unit_name,
                        "pluralName": unit_name + "s"
                    })
                    unit_id = new_u['id']
            except Exception as e:
                logger.warning(f"Failed to get/create unit '{unit_name}': {e}")
        if unit_id:
            resolved_unit = {"id": unit_id, "name": unit_name}
            
    resolved_food = None
    if food and food.get('name'):
        food_id = food.get('id')
        food_name = food.get('name')
        if not food_id:
            try:
                res = mealie._handle_request("GET", "/api/foods", params={"per_page": 1000})
                all_foods = res.get('items', []) if isinstance(res, dict) else res
                existing = next((f for f in all_foods if f.get('name', '').lower() == food_name.lower()), None)
                if existing:
                    food_id = existing['id']
                else:
                    new_f = mealie._handle_request("POST", "/api/foods", json={"name": food_name})
                    food_id = new_f['id']
            except Exception as e:
                logger.warning(f"Failed to get/create food '{food_name}': {e}")
        if food_id:
            resolved_food = {"id": food_id, "name": food_name}
            
    return resolved_unit, resolved_food

# 2. Register Custom/Overlay Tools that override or extend the base tools

@mcp.tool()
def get_detailed_meal_plan(
    start_date: str,
    end_date: str
) -> List[Dict[str, Any]]:
    """Get meal plans enriched with full recipe details (ingredients, etc.).
    Use this to see exactly what is in the scheduled meals.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).

    Returns:
        List[Dict[str, Any]]: List of meal plan entries with nested recipe details.
    """
    try:
        return mealie.get_detailed_meal_plan(start_date, end_date)
    except Exception as e:
        raise ToolError(f"Error fetching detailed meal plan: {str(e)}") from e

@mcp.tool()
def create_recipe_from_url(url: str) -> Dict[str, Any]:
    """Create a new recipe by scraping/importing from a URL with high-fidelity AI ingredient parsing.

    Args:
        url: The URL of the recipe to import.

    Returns:
        Dict[str, Any]: Details of the created recipe.
    """
    try:
        logger.info(f"Creating recipe from URL: {url}")
        res = mealie.create_recipe_from_url(url)
        
        # Determine the recipe slug from the response
        if isinstance(res, str):
            slug = res
        elif isinstance(res, dict) and "slug" in res:
            slug = res["slug"]
        else:
            return res
            
        # High-Fidelity Parsing: Clean with AI, then structure with Mealie NLP
        try:
            recipe_json = mealie.get_recipe(slug)
            raw_ingredients = []
            for ing in recipe_json.get("recipeIngredient", []):
                text = ing.get("note") or ing.get("display")
                if text:
                    raw_ingredients.append(text)
            
            if raw_ingredients:
                # Use the AI to clean and standardize the lines
                clean_lines = mealie.standardize_ingredients_with_ai(raw_ingredients)
                
                # Use Mealie's NLP parser to structure the clean lines
                parsed_results = mealie.parse_raw_ingredients(clean_lines)
                
                # Map to Mealie's reliable nested object schema for PUT
                update_ingredients = []
                for item in parsed_results:
                    resolved_unit, resolved_food = resolve_ingredient_food_and_unit(mealie, item)
                    update_ingredients.append({
                        "note": item.get('note') or "",
                        "quantity": item.get('quantity', 0.0),
                        "unit": resolved_unit,
                        "food": resolved_food,
                        "disableAmount": item.get('disableAmount', False)
                    })
                
                recipe_json["recipeIngredient"] = update_ingredients
                
                # Clean up metadata
                for field in ["createdAt", "updatedAt", "dateAdded", "dateUpdated"]:
                    if field in recipe_json:
                        del recipe_json[field]

                mealie.update_recipe(slug, recipe_json)
                res = mealie.get_recipe(slug)
        except Exception as parse_err:
            logger.warning(f"Failed to high-fidelity parse ingredients for {slug}: {parse_err}")
            
        return res
    except Exception as e:
        raise ToolError(f"Error creating recipe from URL '{url}': {str(e)}") from e

@mcp.tool()
def parse_ingredients(raw_text: str) -> List[Dict[str, Any]]:
    """Parse free-text ingredients (like freezer/pantry items) into structured data.

    Args:
        raw_text: Comma-separated list of ingredients to parse.

    Returns:
        List[Dict[str, Any]]: Structured ingredient objects.
    """
    try:
        return mealie.parse_ingredients_with_ai(raw_text)
    except Exception as e:
        raise ToolError(f"Error parsing ingredients: {str(e)}") from e

@mcp.tool()
def create_recipe(
    name: str, ingredients: List[str], instructions: List[str]
) -> Dict[str, Any]:
    """Create a new recipe with high-fidelity AI ingredient parsing.

    Args:
        name: The name of the new recipe.
        ingredients: A list of ingredients.
        instructions: A list of instructions.

    Returns:
        Dict[str, Any]: The created recipe details.
    """
    try:
        slug = mealie.create_recipe(name)
        recipe_json = mealie.get_recipe(slug)
        
        # High-Fidelity Parsing: Clean with AI, then structure with Mealie NLP
        clean_lines = mealie.standardize_ingredients_with_ai(ingredients)
        parsed_results = mealie.parse_raw_ingredients(clean_lines)
        
        update_ingredients = []
        for item in parsed_results:
            resolved_unit, resolved_food = resolve_ingredient_food_and_unit(mealie, item)
            update_ingredients.append({
                "note": item.get('note') or "",
                "quantity": item.get('quantity', 0.0),
                "unit": resolved_unit,
                "food": resolved_food,
                "disableAmount": item.get('disableAmount', False)
            })
        
        recipe_json["recipeIngredient"] = update_ingredients
        recipe_json["recipeInstructions"] = [{"text": i} for i in instructions]
        
        # Clean up metadata (keep 'id')
        for field in ["createdAt", "updatedAt", "dateAdded", "dateUpdated"]:
            if field in recipe_json:
                del recipe_json[field]

        return mealie.update_recipe(slug, recipe_json)
    except Exception as e:
        raise ToolError(f"Error creating recipe '{name}': {str(e)}") from e

@mcp.tool()
def update_recipe(
    slug: str,
    ingredients: List[str],
    instructions: List[str],
) -> Dict[str, Any]:
    """Replaces the ingredients and instructions of an existing recipe with AI parsing.

    Args:
        slug: The unique text identifier for the recipe.
        ingredients: A list of ingredients.
        instructions: A list of instructions.

    Returns:
        Dict[str, Any]: The updated recipe details.
    """
    try:
        recipe_json = mealie.get_recipe(slug)
        
        # High-Fidelity Parsing: Clean with AI, then structure with Mealie NLP
        clean_lines = mealie.standardize_ingredients_with_ai(ingredients)
        parsed_results = mealie.parse_raw_ingredients(clean_lines)
        
        update_ingredients = []
        for item in parsed_results:
            resolved_unit, resolved_food = resolve_ingredient_food_and_unit(mealie, item)
            update_ingredients.append({
                "note": item.get('note') or "",
                "quantity": item.get('quantity', 0.0),
                "unit": resolved_unit,
                "food": resolved_food,
                "disableAmount": item.get('disableAmount', False)
            })
        
        recipe_json["recipeIngredient"] = update_ingredients
        recipe_json["recipeInstructions"] = [{"text": i} for i in instructions]
        
        # Clean up metadata (keep 'id')
        for field in ["createdAt", "updatedAt", "dateAdded", "dateUpdated"]:
            if field in recipe_json:
                del recipe_json[field]

        return mealie.update_recipe(slug, recipe_json)
    except Exception as e:
        raise ToolError(f"Error updating recipe '{slug}': {str(e)}") from e

@mcp.tool()
def get_shopping_list_labels() -> List[Dict[str, Any]]:
    """Get all available shopping list labels (categories) from Mealie.
    Use this to identify existing categories for item organization.

    Returns:
        List[Dict[str, Any]]: List of label objects (id, name).
    """
    try:
        return mealie.get_labels()
    except Exception as e:
        raise ToolError(f"Error fetching labels: {str(e)}") from e

@mcp.tool()
def update_shopping_list_item(
    item_id: str,
    note: Optional[str] = None,
    quantity: Optional[float] = None,
    checked: Optional[bool] = None,
    label_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Updates an existing shopping list item. Use this to change categories (label_id) or details.

    Args:
        item_id: The unique ID of the item to update.
        note: New name or note for the item.
        quantity: New numeric quantity.
        checked: Whether the item is checked off.
        label_id: The ID of the category/label to assign.

    Returns:
        Dict[str, Any]: The updated item details.
    """
    try:
        # First fetch current item to preserve other fields
        # Note: We use the internal list items fetcher
        items_res = mealie.get_shopping_list_items(per_page=1000)
        items = items_res.get('items', [])
        current_item = next((i for i in items if i['id'] == item_id), None)
        
        if not current_item:
            raise ToolError(f"Shopping list item with ID '{item_id}' not found.")
            
        # Merge updates
        if note is not None:
            current_item['note'] = note
        if quantity is not None:
            current_item['quantity'] = quantity
        if checked is not None:
            current_item['checked'] = checked
        if label_id is not None:
            current_item['labelId'] = label_id
        
        return mealie.update_shopping_list_item(item_id, current_item)
    except Exception as e:
        raise ToolError(f"Error updating shopping list item: {str(e)}") from e

@mcp.tool()
def delete_mealplan(entry_id: str) -> str:
    """Delete an existing mealplan entry (such as a placeholder or scheduled meal).
    Use this when swapping or removing scheduled meals.

    Args:
        entry_id: The unique ID of the mealplan entry to delete.

    Returns:
        str: Status message.
    """
    try:
        mealie.delete_meal_plan_entry(entry_id)
        return f"Successfully deleted mealplan entry '{entry_id}'."
    except Exception as e:
        raise ToolError(f"Error deleting mealplan entry: {str(e)}") from e

@mcp.tool()
def sync_shopping_list(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    """Trigger a sync of the active shopping list based on the scheduled meals.
    Call this whenever you make changes to the meal plan (e.g. creating, updating, or deleting meals)
    to automatically rebuild the active shopping list with organic tags, staples, and exclusions in sync.

    Args:
        start_date: Optional start date (YYYY-MM-DD). Defaults to the active planning week Saturday.
        end_date: Optional end date (YYYY-MM-DD). Defaults to the active planning week Friday.

    Returns:
        str: Status message of the sync.
    """
    try:
        # Load state for low staples and freezer items
        from mealie_planner.database import load_state_from_db
        from mealie_planner.shopping_sync import sync_shopping_list as run_sync
        from mealie_planner.utils import get_planning_week_strings
        state = load_state_from_db()
        low_staples = state.get("low_staples", [])
        freezer_items = state.get("freezer_items", "")

        if not start_date or not end_date:
            active_start, active_end = get_planning_week_strings()
            start_date = start_date or active_start
            end_date = end_date or active_end

        success = run_sync(start_date, end_date, low_staples_ids=low_staples, freezer_items=freezer_items)
        if success:
            return f"Successfully synchronized shopping list for the week of {start_date} to {end_date}."
        else:
            return "Failed to synchronize the shopping list. Check the server logs."
    except Exception as e:
        raise ToolError(f"Error during shopping list sync: {str(e)}") from e

if __name__ == "__main__":
    # Tool-override policy (IMPORTANT):
    # FastMCP's ToolManager.add_tool is FIRST-REGISTRATION-WINS — a duplicate name keeps
    # the EXISTING tool and ignores the new one. Our custom overlay tools above are
    # registered at import time (via @mcp.tool() decorators), i.e. BEFORE this block.
    # Therefore calling register_all_tools() here can only ADD vendored tools whose names
    # don't already exist; for any collision the custom high-fidelity version is preserved.
    # Do NOT move register_all_tools() above the overlay definitions or the vendored tools
    # would win instead.
    try:
        overlay_names = sorted(mcp._tool_manager._tools.keys())
        logger.info(f"Custom overlay tools (take precedence over vendored): {overlay_names}")
    except Exception:
        pass

    register_all_tools(mcp, mealie)

    mcp.run()
