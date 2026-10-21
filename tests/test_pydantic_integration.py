import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from mealie_planner.models import (
    CompiledShoppingList,
    MealExclusions,
    ParsedIngredientList,
    RecipeNutritionImputation,
    StandardizedIngredients,
    WeeklyMealPlanResponse,
)
from mealie_planner.parsers import parse_exclusions, parse_freezer_items


class TestPydanticIntegration(unittest.TestCase):

    def test_parsed_ingredient_list(self):
        valid_json = '[{"raw": "1lb beef", "core_ingredient": "beef", "has_meat": true}]'
        parsed = ParsedIngredientList.model_validate_json(valid_json).root
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].raw, "1lb beef")
        self.assertEqual(parsed[0].has_meat, True)
        self.assertEqual(parsed[0].is_main_dish, True)  # defaults to True

        # Test explicit is_main_dish: False
        condiment_json = '[{"raw": "Tzatziki sauce", "core_ingredient": "Tzatziki", "has_meat": false, "is_main_dish": false}]'
        parsed_condiment = ParsedIngredientList.model_validate_json(condiment_json).root
        self.assertEqual(parsed_condiment[0].is_main_dish, False)
        
        # Invalid payload should raise ValidationError
        invalid_json = '[{"raw": "1lb beef", "has_meat": "not_a_boolean"}]'
        with self.assertRaises(ValidationError):
            ParsedIngredientList.model_validate_json(invalid_json)

    def test_meal_exclusions(self):
        valid_json = '{"Monday": ["dinner"], "Tuesday": ["breakfast", "lunch"]}'
        exclusions = MealExclusions.model_validate_json(valid_json).root
        self.assertEqual(exclusions["Monday"], ["dinner"])
        self.assertEqual(exclusions["Tuesday"], ["breakfast", "lunch"])
        
        # Invalid day should raise ValidationError
        invalid_json = '{"NotADay": ["dinner"]}'
        with self.assertRaises(ValidationError):
            MealExclusions.model_validate_json(invalid_json)

    def test_weekly_meal_plan_response(self):
        valid_json = """{
            "days": [
                {
                    "date": "2026-05-30",
                    "meals": {
                        "breakfast": "Oats",
                        "lunch": "Leftovers",
                        "dinner": "some-uuid",
                        "prep_note": "Batch cook veggies"
                    }
                }
            ]
        }"""
        response = WeeklyMealPlanResponse.model_validate_json(valid_json)
        self.assertEqual(len(response.days), 1)
        self.assertEqual(response.days[0].date, "2026-05-30")
        self.assertEqual(response.days[0].meals.dinner, "some-uuid")

    def test_recipe_nutrition_imputation(self):
        valid_json = """{
            "calories": "350",
            "proteinContent": "23",
            "carbohydrateContent": "48",
            "fatContent": "9",
            "fiberContent": "20",
            "sodiumContent": "650",
            "sugarContent": "5",
            "cholesterolContent": "0"
        }"""
        imputation = RecipeNutritionImputation.model_validate_json(valid_json)
        self.assertEqual(imputation.calories, "350")
        self.assertEqual(imputation.fiberContent, "20")

    def test_compiled_shopping_list(self):
        valid_json = """[
            {
                "active_item_index": 1,
                "name": "Spinach",
                "quantity": 2.5,
                "unit": "oz",
                "checked": false,
                "category": "Produce"
            }
        ]"""
        shopping_list = CompiledShoppingList.model_validate_json(valid_json).root
        self.assertEqual(len(shopping_list), 1)
        self.assertEqual(shopping_list[0].name, "Spinach")
        self.assertEqual(shopping_list[0].active_item_index, 1)

    def test_standardize_ingredients(self):
        valid_json = '["2 lbs Chicken Breast", "1 cup White Rice"]'
        ingredients = StandardizedIngredients.model_validate_json(valid_json).root
        self.assertEqual(ingredients, ["2 lbs Chicken Breast", "1 cup White Rice"])

    @patch("mealie_planner.ai_client.AIClient.call")
    def test_check_blackstone_compatibility(self, mock_ai_call):
        import os
        old_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "dummy_key"
        try:
            from mealie_planner.recipe_crawler import check_blackstone_compatibility
            
            # Test Case 1: Fast-path via name keyword
            recipe_griddle = {"name": "Smashed Griddle Burgers", "recipeInstructions": []}
            self.assertTrue(check_blackstone_compatibility(recipe_griddle))
            
            # Test Case 2: Fast-path via instructions keyword
            recipe_instructions = {"name": "Stir Fry", "recipeInstructions": [{"text": "Cook on the Blackstone griddle"}]}
            self.assertTrue(check_blackstone_compatibility(recipe_instructions))
            
            # Test Case 3: AI Fallback returning YES
            recipe_ai_yes = {"name": "Fajitas", "recipeInstructions": [{"text": "Sear chicken and peppers on a flat pan"}]}
            mock_ai_call.return_value = "YES"
            self.assertTrue(check_blackstone_compatibility(recipe_ai_yes))
            mock_ai_call.assert_called_once()
            
            # Test Case 4: AI Fallback returning NO
            mock_ai_call.reset_mock()
            recipe_ai_no = {"name": "Slow Cooker Beef Stew", "recipeInstructions": [{"text": "Simmer in a slow cooker for 8 hours"}]}
            mock_ai_call.value = "NO"
            mock_ai_call.return_value = "NO"
            self.assertFalse(check_blackstone_compatibility(recipe_ai_no))
            mock_ai_call.assert_called_once()
        finally:
            if old_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = old_key
            else:
                os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_normalize_ingredient_name_comprehensive(self):
        from mealie_planner.shopping_sync import normalize_ingredient_name

        # Test double space normalization and general stripping
        self.assertEqual(normalize_ingredient_name("fresh red onion"), "red onion")
        self.assertEqual(normalize_ingredient_name("chicken raw breast"), "chicken breast")
        self.assertEqual(normalize_ingredient_name("3 cloves   garlic"), "garlic")
        self.assertEqual(normalize_ingredient_name("1/2 cup organic spinach"), "spinach")

        # Test "(buy organic)"
        self.assertEqual(normalize_ingredient_name("milk (buy organic)"), "milk")
        self.assertEqual(normalize_ingredient_name("(buy organic) apples"), "apples")

        # Test descriptors
        self.assertEqual(normalize_ingredient_name("frozen peas"), "peas")
        self.assertEqual(normalize_ingredient_name("canned beans"), "beans")
        self.assertEqual(normalize_ingredient_name("cooked rice"), "rice")
        self.assertEqual(normalize_ingredient_name("fresh raw organic chicken"), "chicken")

        # Test quantity/units
        self.assertEqual(normalize_ingredient_name("2 lbs beef"), "beef")
        self.assertEqual(normalize_ingredient_name("1 oz cheese"), "cheese")
        self.assertEqual(normalize_ingredient_name("1.5 kg flour"), "flour")
        self.assertEqual(normalize_ingredient_name("3/4 cup sugar"), "sugar")
        self.assertEqual(normalize_ingredient_name("2 cans tomatoes"), "tomatoes")
        self.assertEqual(normalize_ingredient_name("1 package pasta"), "pasta")
        self.assertEqual(normalize_ingredient_name("3 slices bread"), "bread")
        self.assertEqual(normalize_ingredient_name("1-2 pieces fruit"), "fruit")

        # Test digits without units
        self.assertEqual(normalize_ingredient_name("5 apples"), "apples")
        self.assertEqual(normalize_ingredient_name("1/2 onion"), "onion")
        self.assertEqual(normalize_ingredient_name("3.5 carrots"), "carrots")

        # Edge cases
        self.assertEqual(normalize_ingredient_name(""), "")
        self.assertEqual(normalize_ingredient_name("   "), "")
        self.assertEqual(normalize_ingredient_name("FRESH CHICKEN"), "chicken")

    def test_parse_freezer_items_empty_text_short_circuits(self):
        ai = MagicMock()
        self.assertEqual(parse_freezer_items(ai, ""), [])
        self.assertEqual(parse_freezer_items(ai, "   "), [])
        ai.call.assert_not_called()

    def test_parse_freezer_items_raises_on_ai_failure(self):
        # AI-first: a failed AI call must propagate as an exception so the caller
        # can abort with a clear error, not silently fall back to naive parsing.
        ai = MagicMock()
        ai.call.side_effect = RuntimeError("AI service unavailable")
        with self.assertRaises(RuntimeError):
            parse_freezer_items(ai, "leftover chicken thighs, half an onion")

    def test_parse_freezer_items_raises_on_invalid_ai_response(self):
        ai = MagicMock()
        ai.call.return_value = "not valid json"
        with self.assertRaises(ValidationError):
            parse_freezer_items(ai, "leftover chicken thighs")

    def test_parse_exclusions_empty_text_short_circuits(self):
        from datetime import datetime
        ai = MagicMock()
        start = datetime(2026, 5, 30)
        end = datetime(2026, 6, 5)
        self.assertEqual(parse_exclusions(ai, "", start, end), {})
        self.assertEqual(parse_exclusions(ai, "   ", start, end), {})
        ai.call.assert_not_called()

    def test_parse_exclusions_raises_on_ai_failure(self):
        from datetime import datetime
        ai = MagicMock()
        ai.call.side_effect = RuntimeError("AI service unavailable")
        start = datetime(2026, 5, 30)
        end = datetime(2026, 6, 5)
        with self.assertRaises(RuntimeError):
            parse_exclusions(ai, "skip dinner on Friday", start, end)

    def test_parse_exclusions_raises_on_invalid_ai_response(self):
        from datetime import datetime
        ai = MagicMock()
        ai.call.return_value = "not valid json"
        start = datetime(2026, 5, 30)
        end = datetime(2026, 6, 5)
        with self.assertRaises(ValidationError):
            parse_exclusions(ai, "skip dinner on Friday", start, end)

    @patch("httpx.Client")
    def test_unified_mealie_client_singleton(self, mock_httpx_client):
        # Configure mock get request inside MealieClient initialization connection check
        mock_client_instance = MagicMock()
        mock_httpx_client.return_value = mock_client_instance
        
        from mealie_planner.unified_client import UnifiedMealieClient
        
        # Reset singleton state for testing
        UnifiedMealieClient._instance = None
        
        # Instantiate twice
        c1 = UnifiedMealieClient(base_url="http://mock-mealie", api_key="dummy-key")
        c2 = UnifiedMealieClient(base_url="http://mock-mealie", api_key="dummy-key")
        
        # Assert they are the exact same instance
        self.assertIs(c1, c2)
        # Assert connection test get was called only once
        self.assertEqual(mock_client_instance.get.call_count, 1)

if __name__ == "__main__":
    unittest.main()
