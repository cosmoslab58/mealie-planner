import json
import os
import sys
import time

import requests

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_speed():
    api_key = os.getenv('GOOGLE_API_KEY')
    model = os.getenv('GEMINI_MODEL', 'gemini-3.6-flash')
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    # Sample prompt for shopping list sync
    prompt = """You are an expert in ingredient list cleaning. Clean the following list of items to be Title Case and remove units.
    Items: ['2 lbs salmon fillets', '3 tbsp butter', '4 garlic cloves, minced', '1 tbsp lemon juice']
    Return ONLY a JSON list of strings.
    """

    # Test 1: With default thinking (thinkingBudget not set)
    payload_default = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }
    
    print("Running Test 1: Default thinking config...")
    t0 = time.perf_counter()
    resp1 = requests.post(url, json=payload_default, timeout=60)
    d1 = time.perf_counter() - t0
    data1 = resp1.json()
    thoughts_tokens_1 = data1.get('usageMetadata', {}).get('thoughtsTokenCount', 0)
    print(f"  Duration: {d1:.3f}s")
    print(f"  Thoughts tokens: {thoughts_tokens_1}")
    print(f"  Response: {data1['candidates'][0]['content']['parts'][0]['text'].strip()}")

    # Test 2: With thinkingBudget set to 0
    payload_no_thinking = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    }
    
    print("\nRunning Test 2: Thinking disabled (thinkingBudget = 0)...")
    t0 = time.perf_counter()
    resp2 = requests.post(url, json=payload_no_thinking, timeout=60)
    d2 = time.perf_counter() - t0
    data2 = resp2.json()
    thoughts_tokens_2 = data2.get('usageMetadata', {}).get('thoughtsTokenCount', 0)
    print(f"  Duration: {d2:.3f}s")
    print(f"  Thoughts tokens: {thoughts_tokens_2}")
    print(f"  Response: {data2['candidates'][0]['content']['parts'][0]['text'].strip()}")

    print(f"\nSpeedup: {d1/d2:.1f}x faster (saved {d1 - d2:.3f}s)")

if __name__ == "__main__":
    test_speed()
