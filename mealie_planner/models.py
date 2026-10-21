from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, RootModel


# 1. Ingredient Parsing (parse_freezer_items)
class ParsedIngredient(BaseModel):
    raw: str
    core_ingredient: str
    has_meat: bool
    is_main_dish: bool = True

class ParsedIngredientList(RootModel[List[ParsedIngredient]]):
    pass

# 2. Meal Exclusions (parse_exclusions)
DayName = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MealName = Literal["breakfast", "lunch", "dinner"]

class MealExclusions(RootModel[Dict[DayName, List[MealName]]]):
    pass

# 3. Weekly Meal Selection (generate_weekly_plan)
class DayMeals(BaseModel):
    breakfast: str
    lunch: str
    dinner: str  # The recipe ID or text title
    prep_note: Optional[str] = None

class PlanDayEntry(BaseModel):
    date: str
    meals: DayMeals

class WeeklyMealPlanResponse(BaseModel):
    days: List[PlanDayEntry]

# 4. Recipe Nutrition Imputation (impute_nutrition_with_ai)
class RecipeNutritionImputation(BaseModel):
    calories: str
    proteinContent: str
    carbohydrateContent: str
    fatContent: str
    fiberContent: str
    sodiumContent: str
    sugarContent: str
    cholesterolContent: str

# 5. Active Shopping List Syncer (sync_shopping_list)
class CompiledShoppingItem(BaseModel):
    active_item_index: Optional[int] = None
    name: str
    quantity: float
    unit: Optional[str] = None
    checked: bool
    category: str

class CompiledShoppingList(RootModel[List[CompiledShoppingItem]]):
    pass

# Object wrapper for the compiled list (an object root with an `items` field).
# AIClient also wraps bare-array RootModel schemas itself, but this shape is what
# shopping_sync parses, so it stays.
class CompiledShoppingListWrapper(BaseModel):
    items: List[CompiledShoppingItem]

# 6. Standardize Ingredients (standardize_ingredients_with_ai)
class StandardizedIngredients(RootModel[List[str]]):
    pass
