#!/usr/bin/env python3
"""
Setup script for Mealie AI Companion Planner.
Initializes the submodule, configures environment variables, and creates data directories.
Supports interactive mode and non-interactive mode (--auto).
"""
import argparse
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

SUBMODULE_COMMIT = "f7a2a5e21e68e223629393a5ad16f55dca6ea577"
SUBMODULE_URL = f"https://github.com/rldiao/mealie-mcp-server/archive/{SUBMODULE_COMMIT}.zip"

# Colors for terminal output
def print_success(msg):
    print(f"\033[92m[✓] {msg}\033[0m")

def print_warning(msg):
    print(f"\033[93m[!] {msg}\033[0m")

def print_error(msg):
    print(f"\033[91m[✗] {msg}\033[0m")

def print_info(msg):
    print(f"\033[94m[*] {msg}\033[0m")

def setup_submodule():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mcp_dir = os.path.join(base_dir, "mealie-mcp-server")
    mcp_init = os.path.join(mcp_dir, "src", "mealie", "__init__.py")

    if os.path.exists(mcp_init):
        print_success("mealie-mcp-server submodule is already populated.")
        return True

    print_info("Populating mealie-mcp-server submodule...")

    # Method 1: Git Submodule
    if os.path.exists(os.path.join(base_dir, ".git")):
        try:
            print_info("Attempting to initialize submodule via git...")
            subprocess.run(["git", "submodule", "update", "--init", "--recursive"], cwd=base_dir, check=True)
            if os.path.exists(mcp_init):
                print_success("Submodule initialized via git.")
                return True
        except Exception as e:
            print_warning(f"Git submodule initialization failed: {e}")

    # Method 2: Download ZIP from GitHub (fallback if git not present/configured)
    try:
        print_info(f"Downloading submodule from GitHub ({SUBMODULE_COMMIT[:7]})...")
        os.makedirs(mcp_dir, exist_ok=True)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "submodule.zip")
            urllib.request.urlretrieve(SUBMODULE_URL, zip_path)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(tmpdir)
                
            extracted_dir = os.path.join(tmpdir, f"mealie-mcp-server-{SUBMODULE_COMMIT}")
            if not os.path.exists(extracted_dir):
                # GitHub might pack it under a different branch/tag name pattern
                dirs = [d for d in os.listdir(tmpdir) if os.path.isdir(os.path.join(tmpdir, d))]
                if dirs:
                    extracted_dir = os.path.join(tmpdir, dirs[0])
            
            # Copy extracted files to mealie-mcp-server
            for item in os.listdir(extracted_dir):
                s = os.path.join(extracted_dir, item)
                d = os.path.join(mcp_dir, item)
                if os.path.isdir(s):
                    if os.path.exists(d):
                        shutil.rmtree(d)
                    shutil.copytree(s, d)
                else:
                    shutil.copy(s, d)
                    
        if os.path.exists(mcp_init):
            print_success("Submodule downloaded and populated successfully.")
            return True
        else:
            print_error("Submodule downloaded but structure was unexpected.")
            return False
    except Exception as e:
        print_error(f"Failed to download submodule from GitHub: {e}")
        return False

def configure_env(auto=False):
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(base_dir, ".env")
    env_example = os.path.join(base_dir, ".env.example")

    if not os.path.exists(env_example):
        print_error(".env.example file not found! Cannot create .env")
        return False

    if os.path.exists(env_file):
        print_info(".env file already exists.")
        if auto:
            # Generate secret key if it's default/empty
            update_secret_key(env_file)
            return True
        # Read current .env contents
        env_vars = parse_env(env_file)
    else:
        print_info("Creating .env from .env.example...")
        shutil.copy(env_example, env_file)
        env_vars = parse_env(env_file)

    # Generate a random secret key if it is still default
    if env_vars.get("SECRET_KEY") in ["change_me_to_a_random_secret", "your_flask_secret_key", ""]:
        env_vars["SECRET_KEY"] = secrets.token_hex(24)
        print_success("Generated a random SECRET_KEY.")

    if not auto:
        print("\n--- Interactive Environment Configuration ---")
        print("Leave blank to keep current/default value.")
        
        # Mealie config
        env_vars["MEALIE_API_URL"] = prompt_val("Mealie API URL", env_vars.get("MEALIE_API_URL", "http://localhost:9000"))
        env_vars["MEALIE_TOKEN"] = prompt_val("Mealie API Token", env_vars.get("MEALIE_TOKEN", ""))
        env_vars["MEALIE_ACTIVE_LIST_ID"] = prompt_val("Mealie Active Shopping List UUID", env_vars.get("MEALIE_ACTIVE_LIST_ID", ""))
        env_vars["MEALIE_STAPLES_LIST_ID"] = prompt_val("Mealie Staples Shopping List UUID", env_vars.get("MEALIE_STAPLES_LIST_ID", ""))
        
        # AI (Anthropic Claude)
        env_vars["ANTHROPIC_API_KEY"] = prompt_val("Anthropic API Key", env_vars.get("ANTHROPIC_API_KEY", ""))

        # Household
        env_vars["FAMILY_NAMES"] = prompt_val("Family Names (e.g. 'John & Jane')", env_vars.get("FAMILY_NAMES", "Nathan & Kristin"))
        env_vars["FAMILY_RECIPIENT_EMAILS"] = prompt_val("Recipient Emails (comma-separated)", env_vars.get("FAMILY_RECIPIENT_EMAILS", ""))
        env_vars["APP_TIMEZONE"] = prompt_val("App Timezone", env_vars.get("APP_TIMEZONE", "America/New_York"))

    # Write back to .env
    write_env(env_file, env_vars)
    print_success(".env file configured successfully.")
    return True

def update_secret_key(env_file):
    env_vars = parse_env(env_file)
    if env_vars.get("SECRET_KEY") in ["change_me_to_a_random_secret", "your_flask_secret_key", ""]:
        env_vars["SECRET_KEY"] = secrets.token_hex(24)
        write_env(env_file, env_vars)
        print_success("Auto-generated random SECRET_KEY in .env.")

def parse_env(file_path):
    env_vars = {}
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip().strip('"').strip("'")
    return env_vars

def write_env(file_path, env_vars):
    # We want to preserve comments where possible, but if not we can rewrite the file cleanly.
    # To keep comments and formatting, we will read the file and replace values.
    lines = []
    with open(file_path, "r") as f:
        lines = f.readlines()

    keys_written = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            key = key.strip()
            if key in env_vars:
                val = env_vars[key]
                if " " in val or "," in val or "#" in val:
                    new_lines.append(f"{key}=\"{val}\"\n")
                else:
                    new_lines.append(f"{key}={val}\n")
                keys_written.add(key)
                continue
        new_lines.append(line)

    # Write any variables that weren't in the original file
    for key, val in env_vars.items():
        if key not in keys_written:
            if " " in val or "," in val or "#" in val:
                new_lines.append(f"{key}=\"{val}\"\n")
            else:
                new_lines.append(f"{key}={val}\n")

    with open(file_path, "w") as f:
        f.writelines(new_lines)

def prompt_val(prompt_text, default_val):
    val = input(f"{prompt_text} [{default_val}]: ").strip()
    return val if val else default_val

def create_directories():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir, exist_ok=True)
        print_success("Created 'data/' directory for runtime state.")
    else:
        print_success("'data/' directory already exists.")

    # Copy example files to data/ if they do not exist
    dietary_example = os.path.join(base_dir, "dietary_rules.example.txt")
    dietary_target = os.path.join(data_dir, "dietary_rules.txt")
    if os.path.exists(dietary_example) and not os.path.exists(dietary_target):
        shutil.copy(dietary_example, dietary_target)
        print_success("Copied dietary_rules.example.txt to data/dietary_rules.txt")

    banned_example = os.path.join(base_dir, "banned_recipes.example.txt")
    banned_target = os.path.join(data_dir, "banned_recipes.txt")
    if os.path.exists(banned_example) and not os.path.exists(banned_target):
        shutil.copy(banned_example, banned_target)
        print_success("Copied banned_recipes.example.txt to data/banned_recipes.txt")

def main():
    parser = argparse.ArgumentParser(description="Setup Mealie AI Companion Planner")
    parser.add_argument("--auto", action="store_true", help="Non-interactive automatic setup (uses defaults)")
    args = parser.parse_args()

    print("==================================================")
    print("      Mealie AI Companion Planner Setup           ")
    print("==================================================")

    create_directories()

    submodule_ok = setup_submodule()
    if not submodule_ok:
        print_error("Submodule setup failed. Please check internet connection or git installation.")
        sys.exit(1)

    env_ok = configure_env(auto=args.auto)
    if not env_ok:
        sys.exit(1)

    print("\n==================================================")
    print_success("Setup complete!")
    print("\nTo run the application:")
    print("  Docker (Recommended):")
    print("    docker compose up -d --build")
    print("\n  Locally:")
    print("    pip install -r requirements.txt")
    print("    python app.py")
    print("==================================================")

if __name__ == "__main__":
    main()
