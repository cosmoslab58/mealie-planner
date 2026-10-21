import json
import os
import sys
import time

# Add the project root to sys.path
sys.path.insert(0, os.path.abspath("."))
from mealie_planner.unified_client import UnifiedMealieClient


def migrate_to_zones():
    client = UnifiedMealieClient()
    
    # 1. Define Master Zones
    ZONES = [
        "1. Produce: Vegetables & Greens",
        "2. Bakery & Bread",
        "3. Meat, Poultry & Seafood",
        "4. Dairy & Eggs",
        "5. Pantry & Grains",
        "6. Baking, Spices & Oils",
        "7. Beverages",
        "8. Frozen Foods",
        "9. Household & Miscellaneous"
    ]
    
    print("--- Starting Mealie Zone Migration ---")
    
    # 2. Create/Fetch Master Labels
    existing_labels = client.get_labels()
    label_map = {l['name']: l['id'] for l in existing_labels}
    zone_ids = {}
    
    for zone_name in ZONES:
        if zone_name not in label_map:
            print(f"Creating Master Zone: {zone_name}")
            # Use Mealie's creation endpoint
            new_label = client._handle_request("POST", "/api/groups/labels", json={
                "name": zone_name,
                "color": "#3C5A54" # Default Sage
            })
            zone_ids[zone_name] = new_label['id']
        else:
            print(f"Master Zone exists: {zone_name}")
            zone_ids[zone_name] = label_map[zone_name]

    # 3. Fetch all food items (Handling Pagination)
    print("Fetching all food items...")
    all_foods = []
    page = 1
    while True:
        res = client._handle_request("GET", f"/api/foods?perPage=1000&page={page}")
        items = res.get('items', [])
        all_foods.extend(items)
        if page >= res.get('total_pages', 1):
            break
        page += 1
    
    print(f"Found {len(all_foods)} food items to migrate.")

    # 4. Batch Process Foods using AI for categorization
    # To be efficient, we categorize in batches of 50
    BATCH_SIZE = 50
    for i in range(0, len(all_foods), BATCH_SIZE):
        batch = all_foods[i:i + BATCH_SIZE]
        food_names = [f['name'] for f in batch]
        
        print(f"Categorizing batch {i//BATCH_SIZE + 1} ({len(food_names)} items)...")
        
        prompt = f"""You are a grocery store layout expert. Categorize the following food items into exactly one of these 9 zones:
{json.dumps(ZONES, indent=2)}

### FOOD ITEMS:
{json.dumps(food_names, indent=2)}

Return a JSON object where keys are food names and values are the EXACT zone name string."""

        from mealie_planner.ai_client import AIClient
        ai_resp = AIClient().call(prompt, expect_json=True)
        try:
            mapping = json.loads(ai_resp)
        except:
            print(f"Error parsing AI response for batch {i}. Skipping batch.")
            continue

        for food in batch:
            food_name = food['name']
            assigned_zone = mapping.get(food_name)
            if assigned_zone and assigned_zone in zone_ids:
                new_label_id = zone_ids[assigned_zone]
                if food.get('labelId') != new_label_id:
                    # Update food item
                    try:
                        client._handle_request("PUT", f"/api/foods/{food['id']}", json={
                            "name": food['name'],
                            "labelId": new_label_id
                        })
                    except Exception as e:
                        print(f"Failed to update food '{food_name}': {e}")
            else:
                # Default to Miscellaneous if AI failed or returned invalid zone
                misc_id = zone_ids["9. Household & Miscellaneous"]
                if food.get('labelId') != misc_id:
                    client._handle_request("PUT", f"/api/foods/{food['id']}", json={
                        "name": food['name'],
                        "labelId": misc_id
                    })

    # 5. Cleanup redundant labels
    print("Cleaning up old labels...")
    for label in existing_labels:
        if label['name'] not in ZONES:
            print(f"Deleting redundant label: {label['name']}")
            try:
                client._handle_request("DELETE", f"/api/groups/labels/{label['id']}")
            except:
                print(f"Could not delete label {label['name']} (it may be in use by non-food entities)")

    print("--- Migration to Standardized Zones Complete ---")

if __name__ == "__main__":
    migrate_to_zones()
