# Mealie AI Companion Planner

An intelligent, AI-powered weekly menu planner, shopping list syncer, and email briefing companion for **[Mealie](https://mealie.io)**.

This companion app interfaces with your Mealie instance to automate weekly menu curation, sequence meals by ingredient perishability, sync shopping lists, and send automated kitchen briefings. It also features an interactive AI chatbot for on-the-fly plan adjustments.

---

## 🤝 How this splits with Mealie

Mealie is not a redundant backend here; it owns the parts that are genuinely
expensive to rebuild, and this app owns the week.

| Mealie owns | This app owns |
|---|---|
| Recipes, and importing them from a URL (`recipe-scrapers` plus schema.org fallbacks, maintained against sites that keep changing) | Which recipes get cooked on which day |
| The canonical foods and units tables, which is what lets shopping-list items dedupe by UUID instead of by fuzzy string match | Shopping list curation and sync |
| Recipe images and assets | Weekly and daily briefing emails |
| The recipe reader you actually open at the stove | The AI chat for plan adjustments |

Practical consequences:

* **Recipe titles, images, and ingredients are edited in Mealie**, never here.
* **Mealie's own Meal Planner and Shopping List pages still work**, and nothing
  hides them. If you add a meal there, this app will keep it: regeneration
  clears only the entries it created (see below) and plans around yours.
* This app's meal-plan writes go through `PlanGenerator.clear_owned_entries`,
  which consults the `plan_entries` ownership table in the local SQLite. An
  entry that is not recorded there was put in Mealie's calendar by a human and
  is never deleted. If that ownership table is ever lost, the next generation
  re-adopts whatever it finds, which is the pre-tracking behavior.

## 🚀 Quick Start (Docker Compose)

Get up and running with standard Docker commands:

1. **Configure Environment:**
   Copy the example environment file and edit it to fill in your Mealie details and AI credentials:
   ```bash
   cp .env.example .env
   ```

2. **Launch the Container:**
   Start the application and its background workers:
   ```bash
   docker compose up -d --build
   ```
   *Note: If the git submodule or data configuration templates are missing, the Docker build and container startup will automatically fetch and initialize them.*

The dashboard will be accessible at **`http://localhost:9926`**.

> [!TIP]
> **Optional Interactive Setup:** If you prefer an interactive walkthrough to configure your `.env` variables and initialize the submodule manually on your host machine, you can run the helper script:
> ```bash
> python scripts/setup_wizard.py
> ```

---

## 🌟 Key Features

* **Intelligent Weekly Planning:** Schedules plans for a Saturday-to-Friday week. Sequences meals by perishability (fresh fish/greens first; frozen/pantry later) and prioritizes inventory you already have.
* **Active Shopping List Syncer:** Automatically syncs ingredients, cleans names, filters staples, and pushes to Mealie. Prevents item duplication by using database UUID mapping.
* **Interactive AI Chatbot:** Adjust the weekly plan dynamically ("swap Thursday's dinner with a beef recipe") using the chat panel.
* **Smart Swaps:** Suggests alternative recipes from your collection that reuse unused ingredients to minimize food waste.
* **Automated Email Briefings:** Sends weekly menus on Saturdays and daily reminders with prep steps and macro/micro nutrition tracking.
* **Progressive Web App (PWA):** Fully installable on iOS and Android devices.
* **Griddle Compatibility tagging (Optional):** Optionally flags recipes suitable for outdoor griddles/flat-tops and suggests batch-cooking optimizations.

---

## ⚙️ Configuration & Customization

All configurations are managed in your `.env` file, which is created automatically by the setup script.

> [!TIP]
> **Docker Networking:** If this companion app runs on the same host as Mealie and you prefer internal container-to-container routing, uncomment the network block at the bottom of `docker-compose.yml` to join Mealie's Docker network.

### Custom Household Rules
To keep your private family preferences out of git, the app loads dietary constraints and banned recipes dynamically from files in the `data/` directory (which is gitignored). 

Templates for these files are provided in the repository root and are copied automatically to the `data/` folder when you run the setup script:

1. **Custom Dietary Rules (`data/dietary_rules.txt`):**
   Define specific family guidelines, dietary styles, or severe allergies (copied from `dietary_rules.example.txt`).
2. **Banned Recipes (`data/banned_recipes.txt`):**
   List recipe titles (one per line) that the menu generator should never schedule (copied from `banned_recipes.example.txt`).

---

## 🛠️ Tech Stack

* **Backend:** Python 3.12, Flask (app factory + blueprints), APScheduler, served by gunicorn in Docker
* **Frontend:** Vanilla JS + CSS design tokens (`static/js/app.js`, `static/css/app.css`), Jinja partials in `templates/partials/`
* **AI Engine:** Anthropic Claude (`claude-haiku-5-5` for every feature) via the official `anthropic` SDK; set `ANTHROPIC_API_KEY` in `.env`
* **MCP Integration:** [mealie-mcp-server](https://github.com/rldiao/mealie-mcp-server) via a persistent chat session

### Code layout

```
app.py                       # thin entrypoint (dev: python app.py, prod: gunicorn app:app)
mealie_planner/
  web/                       # Flask app factory + services composition root
    routes/                  # planning, shopping, chat, admin blueprints
  plan_generator.py          # AI weekly plan generation
  shopping_sync.py           # non-destructive AI shopping list sync
  chat_session.py            # persistent MCP chat session
  maintenance.py             # destructive wipe operations (web + CLI)
templates/partials/          # per-view Jinja partials
```

---

## 📜 Helper Scripts

The project includes CLI utilities in the `scripts/` directory. Run them from the project root:

```bash
# View upcoming meal plans
python -m scripts.list_plans

# Check active ingredients in the shopping list
python -m scripts.check_current_ingredients

# Wipe current plans and shopping list
python -m scripts.clear_mealie
```
*See [scripts/README.md](scripts/README.md) for the full CLI utility list.*

---

## 🧪 Tests & Lint

Verify your setup by running the offline mocked test suite and linter (both run in CI on every push):
```bash
pip install -r requirements.txt ruff pytest
ruff check .
pytest
```

---

## 📄 License

MIT
