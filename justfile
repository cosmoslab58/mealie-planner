# Mealie planner — dev/deploy recipes (venv-based, for local use).
# CI (.github/workflows/ci.yml) runs lint and tests GitHub-hosted without a
# venv; deploy-local.yml deploys via the self-hosted runner on bodhi.
# Verb names match the other lab repos (see ../CLAUDE.md) so the same
# commands work everywhere.

# One-time setup: venv with app deps + dev tools
install:
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt ruff pytest

# Flask dev server. Talks to the real Mealie instance via the synced .env,
# so anything it writes lands in the live meal plan.
dev:
    ./venv/bin/python app.py

lint:
    ./venv/bin/ruff check .

test:
    ./venv/bin/pytest -q

# Deploy from anywhere (ssh to bodhi and run deploy-local there). The manual
# path when CI can't do it -- an Actions outage, a missed webhook.
deploy:
    ssh bodhi.lab 'cd ~/projects/mealie-planner && just deploy-local'

# Rebuild + restart the compose stack. Run on bodhi (CI calls this).
# --recursive because the mealie-mcp-server submodule is part of the build.
deploy-local:
    git submodule update --init --recursive
    docker compose up -d --build

# Drop build cache and dangling images
prune:
    docker builder prune -f
    docker image prune -f
