"""Tests never reach the real shopping list: an empty value here wins over the synced .env,
because load_dotenv does not override variables that are already set."""
import os

os.environ["SHOPPING_CALDAV_URL"] = ""
