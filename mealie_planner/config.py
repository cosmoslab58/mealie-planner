import logging
import os

logger = logging.getLogger(__name__)

# Mealie API List IDs
ACTIVE_LIST_ID = os.getenv('MEALIE_ACTIVE_LIST_ID', '9a1e2d1e33f24f27a01fef55c89a92de')
NEXT_LIST_ID = os.getenv('MEALIE_NEXT_LIST_ID', ACTIVE_LIST_ID)
STAPLES_LIST_ID = os.getenv('MEALIE_STAPLES_LIST_ID', '1196f23a527b42a9a75b1c3850251948')

# Skill parsing logic
def load_skill_md(skill_name, skill_path="SKILL.md", strip_front=True):
    """Load the content of a SKILL.md file."""
    # Find the parent of mealie_planner dir
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(base_dir, '.agents', 'skills', skill_name, skill_path)
    if os.path.exists(full_path):
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if strip_front and content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    return parts[2].strip()
            return content.strip()
    return ""

def extract_section(content, section_name):
    """Extract a specific section from markdown content."""
    lines = content.split('\n')
    start_line = -1
    for i, line in enumerate(lines):
        if line.strip().startswith(f'## {section_name}'):
            start_line = i + 1
            break
    
    if start_line == -1:
        return ""
    
    section_lines = []
    for line in lines[start_line:]:
        if line.strip().startswith('## '):
            break
        section_lines.append(line)
    
    return '\n'.join(section_lines).strip()

def parse_frontmatter(content):
    """Parse YAML-like frontmatter from markdown content."""
    if not content.startswith('---'):
        return {}
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}
    yaml_text = parts[1]
    metadata = {}
    for line in yaml_text.split('\n'):
        if ':' in line:
            key, val = line.split(':', 1)
            metadata[key.strip()] = val.strip()
    return metadata

# Read config dynamically from SKILL.md
_SKILL_MD_CONTENT = load_skill_md('meal-planner', strip_front=False)
_METADATA = parse_frontmatter(_SKILL_MD_CONTENT)

FAMILY_NAMES = os.getenv('FAMILY_NAMES', _METADATA.get('family_names', 'Nathan & Kristin'))
TIMEZONE = os.getenv('APP_TIMEZONE', _METADATA.get('timezone', 'America/New_York'))
APP_URL = os.getenv('MEALIE_PLANNER_APP_URL', _METADATA.get('app_url', 'https://mealie-planner.example.com'))
MEALIE_FRONTEND_URL = os.getenv('MEALIE_FRONTEND_URL', 'https://your-mealie-domain.example')
RECIPE_API_KEY = os.getenv('RECIPE_API_KEY')
RECENT_LOOKBACK_DAYS = int(os.getenv('RECENT_LOOKBACK_DAYS', '14'))

# --- AI Vendor and Models Configuration ---
AI_VENDOR = os.getenv("AI_VENDOR", "gemini").lower()  # "gemini" or "openai" or "deepseek"

# Models for each area
GEMINI_CORE_MODEL = os.getenv("GEMINI_CORE_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.6-flash"))

OPENAI_CORE_MODEL = os.getenv("OPENAI_CORE_MODEL", os.getenv("AI_MODEL_NAME", "gpt-4o-mini"))
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", os.getenv("AI_MODEL_NAME", "gpt-4o"))

# Resolve active models based on AI_VENDOR
if AI_VENDOR == "gemini":
    ACTIVE_CORE_MODEL = GEMINI_CORE_MODEL
    ACTIVE_CHAT_MODEL = GEMINI_CHAT_MODEL
else:
    ACTIVE_CORE_MODEL = OPENAI_CORE_MODEL
    ACTIVE_CHAT_MODEL = OPENAI_CHAT_MODEL

raw_emails = os.getenv('FAMILY_RECIPIENT_EMAILS', _METADATA.get('recipient_emails', 'nathan@example.com,kristin@example.com'))
FAMILY_RECIPIENT_EMAILS = [email.strip() for email in raw_emails.split(',')]

FAMILY_MEMBERS = os.getenv('FAMILY_MEMBERS', _METADATA.get('family_members', ''))

# Load dietary rules: check for local gitignored file first
_DIETARY_RULES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'dietary_rules.txt')
if os.path.exists(_DIETARY_RULES_FILE):
    with open(_DIETARY_RULES_FILE, 'r', encoding='utf-8') as _f:
        rules_content = _f.read().strip()
else:
    rules_content = extract_section(_SKILL_MD_CONTENT, 'Household & Dietary Constraints')

# Dynamically construct prompt from family info (env) + rules (txt/md file)
FAMILY_DIETARY_RULES_PROMPT = f"""
=== FAMILY MEMBERS ===
{FAMILY_MEMBERS if FAMILY_MEMBERS else "The Family"}

=== DIETARY RULES & PREFERENCES ===
{rules_content}
"""

CHATBOT_GUIDELINES_PROMPT = load_skill_md('meal-planner', 'CHATBOT_GUIDELINES.md')

# Markdown Prompt Templates
CHATBOT_SYSTEM_PROMPT_TEMPLATE = load_skill_md('meal-planner', 'CHATBOT_SYSTEM.md')
SWAP_RECOMMENDATIONS_PROMPT_TEMPLATE = load_skill_md('recipe-finder', 'SWAP_RECOMMENDATIONS.md')
SEMANTIC_MATCH_PROMPT_TEMPLATE = load_skill_md('recipe-finder', 'SEMANTIC_MATCH.md')

# Skill definitions (exposed for compatibility with test scripts)
_RECIPE_FINDER_SKILL_DEFINITION = load_skill_md('recipe-finder')
_MEAL_EXCLUSION_PARSING_SKILL_DEFINITION = load_skill_md('meal-exclusion-parsing')
_WEEKLY_MEAL_SELECTION_SKILL_DEFINITION = load_skill_md('weekly-meal-selection')
_SHOPPING_LIST_SYNC_SKILL_DEFINITION = load_skill_md('shopping-list-sync')
_RECIPE_NUTRITION_IMPUTATION_SKILL_DEFINITION = load_skill_md('recipe-nutrition-imputation')
_BANNED_RECIPES_SKILL_DEFINITION = load_skill_md('banned-recipes')
_INGREDIENT_PARSING_SKILL_DEFINITION = load_skill_md('ingredient-parsing')
_BLACKSTONE_COMPATIBILITY_SKILL_DEFINITION = load_skill_md('blackstone-compatibility')
_INGREDIENT_STANDARDIZATION_SKILL_DEFINITION = load_skill_md('ingredient-standardization')
_DAILY_BRIEFING_GENERATION_SKILL_DEFINITION = load_skill_md('daily-briefing-generation')
_WEEKLY_THEMES_SYNOPSIS_SKILL_DEFINITION = load_skill_md('weekly-themes-synopsis')

# Breakfast Nutrition Profiles
BREAKFAST_PROFILES = {
    "English Muffins with Jam": {
        "calories": 220, "protein": 5, "carbs": 40, "fat": 2, "fiber": 2, "sodium": 320, "sugar": 10, "cholesterol": 0
    },
    "Toast with Jam": {
        "calories": 200, "protein": 4, "carbs": 35, "fat": 2, "fiber": 2, "sodium": 300, "sugar": 10, "cholesterol": 0
    },
    "Bagels & Cream Cheese": {
        "calories": 380, "protein": 11, "carbs": 55, "fat": 12, "fiber": 2, "sodium": 500, "sugar": 6, "cholesterol": 30
    },
    "Yogurt with Granola": {
        "calories": 250, "protein": 12, "carbs": 35, "fat": 5, "fiber": 3, "sodium": 100, "sugar": 15, "cholesterol": 15
    },
    "Cereal & Milk": {
        "calories": 300, "protein": 8, "carbs": 50, "fat": 6, "fiber": 3, "sodium": 200, "sugar": 12, "cholesterol": 15
    },
    "Oats": {
        "calories": 280, "protein": 10, "carbs": 48, "fat": 5, "fiber": 8, "sodium": 115, "sugar": 12, "cholesterol": 0
    }
}

# Lunch Nutrition Profiles
LUNCH_LEFTOVER_PROFILE = {
    "calories": 500, "protein": 22, "carbs": 55, "fat": 15, "fiber": 5, "sodium": 600, "sugar": 5, "cholesterol": 40
}

LUNCH_SANDWICH_PROFILE = {
    "calories": 450, "protein": 18, "carbs": 45, "fat": 14, "fiber": 4, "sodium": 800, "sugar": 6, "cholesterol": 35
}

# Recommended Daily Allowances (Individual Baseline Reference)
RDA = {
    "calories": 2000,
    "protein": 50,
    "carbs": 275,
    "fat": 70,
    "fiber": 28,  # Target high fiber
    "sodium": 2300,
    "sugar": 50,
    "cholesterol": 300
}

def get_secret_key():
    """Return the Flask secret key.

    Prefers the SECRET_KEY env var; otherwise generates one once and persists it
    under data/ so sessions and flash messages survive container restarts.
    """
    env_key = os.getenv('SECRET_KEY')
    if env_key:
        return env_key

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    key_file = os.path.join(base_dir, 'data', '.secret_key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                key = f.read().strip()
                if key:
                    return key
        import secrets
        key = secrets.token_hex(32)
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(key)
        return key
    except OSError as e:
        logger.warning(f"[config] Could not persist secret key ({e}); using ephemeral key.")
        return os.urandom(24)


def get_banned_recipes():
    """Load the list of banned recipes from a local data file or the banned-recipes skill."""
    # Try local data/banned_recipes.txt first
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    banned_file = os.path.join(base_dir, 'data', 'banned_recipes.txt')
    if os.path.exists(banned_file):
        try:
            with open(banned_file, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
        except Exception as e:
            logger.error(f"[config] Error reading local banned recipes: {e}")

    # Fallback to skill definition
    content = load_skill_md('banned-recipes')
    if not content:
        return []
    section = extract_section(content, 'Banned Recipes List')
    banned = []
    for line in section.split('\n'):
        line = line.strip()
        if line.startswith('- ') or line.startswith('* '):
            # Extract recipe name up to the colon (if present) or just the whole line
            name = line[2:].split(':', 1)[0].strip()
            # Strip markdown formatting asterisks
            name = name.replace('**', '').replace('*', '').strip()
            if name:
                banned.append(name)
    return banned
