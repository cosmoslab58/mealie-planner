---
name: meal-planner
description: Handle meal planning, diet rules, griddle batch cooking, organic tagging, shopping lists, and recipe scraping for the household Mealie instance.
family_names: Nathan & Kristin
timezone: America/New_York
app_url: https://mealie-planner.example.com
recipient_emails: person_a@example.com,person_b@example.com
---

# Mealie Meal Planner Skill

This skill contains the constraints, business rules, and integration workflows for managing the weekly companion meal planner.

## When to use this skill
- Use this when editing, debugging, or maintaining the `mealie-planner` application code under `~/projects/mealie-planner`.
- Use this when modifying database sync rules, dietary filtering logic, or scheduling scripts.
- Use this when resolving issues related to shopping list calculations, Blackstone griddle checks, or nutrition summaries.

## Household & Dietary Constraints
- **Diet**: [Describe general dietary preferences, e.g. vegetarian, pescatarian, omnivore]
- **Pork**: [Pork constraints, e.g. Pork is OK except processed pork]
- **Beef & Steak**: [Beef/steak constraints, e.g. schedule rarely due to cost]
- **Forbidden Meats**: [Forbidden meats to exclude, e.g. hot dogs, bacon, ham]
- **Fiber Target**: [Fiber targets if any]
- **Organic Target**: Automatically append `(Buy Organic)` to ingredients matching the USDA "Dirty Dozen" (strawberries, spinach, kale, collard/mustard greens, grapes, peaches, pears, nectarines, apples, bell peppers, hot/chili peppers, cherries, blueberries, green beans).
- **Note on Children**: [Any notes on nutrition calculations for children]

> [!NOTE]
> The application will automatically prioritize loading your custom dietary constraints from `data/dietary_rules.txt` if that file exists, and family names/members via environment variables.

## Mealie Setup & State Constants
- **Active Shopping List ID**: `your_active_shopping_list_uuid` (configured via MEALIE_ACTIVE_LIST_ID in .env).
- **Staples List ID**: `your_staples_shopping_list_uuid` (configured via MEALIE_STAPLES_LIST_ID in .env).
- **Auth Token**: Retrieved securely via the `MEALIE_TOKEN` environment variable. Never from a database fallback.
- **Planning Week format**: Anchored strictly on a **Saturday-to-Friday** schedule.

## Blackstone Griddle Logic
- **Compatibility**: Check if a recipe name, description, or instructions mention `blackstone`, `griddle`, or `flat top`.
- **Batch Optimization**: If Blackstone dinners are scheduled, prompt suggestions in the emails to batch-cook ingredients for subsequent dinners.

## Double-Submission Protection
- When users load the planning questionnaire URL (`/`), the app must check if dinner plans are already scheduled in Mealie for the upcoming Saturday-to-Friday week.
- If plans are found, the questionnaire is hidden and they are redirected to the Active Week Dashboard showing the active calendar and shopping list.

## Communication Schedule
- **Saturday Q/A email**: Sent at 8:00 AM to prompt questionnaire completion.
- **Saturday Report email**: Sent immediately upon questionnaire submission summarizing the plan, shopping list, griddle tips, and weekly nutritional averages.
- **Daily Reminder email**: Sent Sunday to Friday at 7:00 AM containing today's menu, griddle reminders, and macro/micro nutrition totals compared to daily RDAs.


