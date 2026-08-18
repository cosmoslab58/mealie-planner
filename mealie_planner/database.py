"""SQLite-backed persistence for planner state and chat history.

Legacy JSON files under data/ are read once as a seed if the database is
empty (first-run migration), but SQLite is the single source of truth.
"""
import json
import logging
import os
import sqlite3
import threading

logger = logging.getLogger(__name__)

DATABASE_FILE = "data/mealie_companion.db"
STATE_FILE = "data/planner_state.json"
CHAT_HISTORY_FILE = "data/chat_history.json"

# Thread-local storage to avoid sqlite thread-sharing issues
_local = threading.local()


def get_db():
    if not hasattr(_local, "conn"):
        os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)
        _local.conn = sqlite3.connect(DATABASE_FILE, timeout=10.0)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    """Initialize the SQLite database schema."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            session_id TEXT PRIMARY KEY,
            history_json TEXT,
            messages_json TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Mealie meal-plan entries this app created. Anything in Mealie's calendar
    # that is NOT listed here was put there by a human in Mealie's own Meal
    # Planner, and regeneration must leave it alone.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plan_entries (
            entry_id TEXT PRIMARY KEY,
            date TEXT,
            entry_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Bookkeeping that must not leak into the user-facing settings dict.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plan_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()


def load_state_from_db():
    """Load settings/state from SQLite. Seeds from the legacy JSON file if empty."""
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()
        if rows:
            state = {}
            for row in rows:
                try:
                    state[row['key']] = json.loads(row['value'])
                except json.JSONDecodeError:
                    state[row['key']] = row['value']
            return state
    except sqlite3.Error as e:
        logger.error("Error loading state from SQLite: %s", e)

    # One-time seed from the legacy JSON state file
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            save_state_to_db(state)
            return state
        except Exception as e:
            logger.error("Error loading fallback JSON state: %s", e)
    return {}


def save_state_to_db(updates):
    """Save/update settings in SQLite."""
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    try:
        for key, val in updates.items():
            cursor.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(val))
            )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Error saving state to SQLite: %s", e)


def load_chat_history_from_db(session_id="default"):
    """Load chat history and messages from SQLite. Seeds from legacy JSON if empty."""
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT history_json, messages_json FROM chat_history WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "history": json.loads(row['history_json'] or "[]"),
                "messages": json.loads(row['messages_json'] or "[]")
            }
    except sqlite3.Error as e:
        logger.error("Error loading chat history from SQLite: %s", e)

    # One-time seed from the legacy JSON chat history file
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, 'r') as f:
                data = json.load(f)
            save_chat_history_to_db(data.get("history", []), data.get("messages", []), session_id=session_id)
            return data
        except Exception as e:
            logger.error("Error loading chat history fallback JSON: %s", e)
    return {"history": [], "messages": []}


def save_chat_history_to_db(history, messages, session_id="default"):
    """Save chat history and messages to SQLite."""
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO chat_history (session_id, history_json, messages_json, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (session_id, json.dumps(history), json.dumps(messages))
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Error saving chat history to SQLite: %s", e)


def clear_chat_history_in_db(session_id="default"):
    """Clear chat history in SQLite (and reset the legacy JSON file if present)."""
    init_db()
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Error clearing chat history in SQLite: %s", e)

    # Reset the legacy JSON file too so it can't re-seed old history
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, 'w') as f:
                json.dump({"history": [], "messages": []}, f)
        except OSError as e:
            logger.error("Error resetting legacy chat history JSON: %s", e)


# --- Meal-plan entry ownership -------------------------------------------
#
# Mealie's calendar is shared: this app writes to it, and a human can write to
# it from Mealie's own Meal Planner UI. Regeneration clears the week before
# rewriting it, so without a record of which entries are ours, a hand-added
# meal is silently deleted. These helpers keep that record.


def record_plan_entry(entry_id, date=None, entry_type=None):
    """Remember that this app created a Mealie meal-plan entry."""
    if not entry_id:
        return
    init_db()
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO plan_entries (entry_id, date, entry_type) VALUES (?, ?, ?)",
            (str(entry_id), date, entry_type)
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Error recording plan entry %s: %s", entry_id, e)


def forget_plan_entry(entry_id):
    """Drop the ownership record for an entry that no longer exists in Mealie."""
    if not entry_id:
        return
    init_db()
    conn = get_db()
    try:
        conn.execute("DELETE FROM plan_entries WHERE entry_id = ?", (str(entry_id),))
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Error forgetting plan entry %s: %s", entry_id, e)


def filter_planner_owned(entry_ids):
    """Return the subset of entry_ids this app created."""
    ids = [str(i) for i in entry_ids if i]
    if not ids:
        return set()
    init_db()
    conn = get_db()
    try:
        owned = set()
        # Chunked to stay under SQLite's variable limit on very large weeks.
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT entry_id FROM plan_entries WHERE entry_id IN ({placeholders})",
                chunk
            ).fetchall()
            owned.update(row['entry_id'] for row in rows)
        return owned
    except sqlite3.Error as e:
        logger.error("Error reading plan entry ownership: %s", e)
        # Fail closed: an unreadable ownership table must not license a wipe.
        return set()


def adopt_plan_entries(entries):
    """Claim existing Mealie entries as ours (one-time upgrade path)."""
    for entry in entries or []:
        record_plan_entry(entry.get('id'), entry.get('date'), entry.get('entryType'))


def prune_plan_entries(before_date):
    """Forget ownership records for weeks now in the past."""
    if not before_date:
        return
    init_db()
    conn = get_db()
    try:
        conn.execute("DELETE FROM plan_entries WHERE date IS NOT NULL AND date < ?", (before_date,))
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Error pruning plan entries: %s", e)


def get_plan_meta(key):
    init_db()
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM plan_meta WHERE key = ?", (key,)).fetchone()
        return row['value'] if row else None
    except sqlite3.Error as e:
        logger.error("Error reading plan meta %s: %s", key, e)
        return None


def set_plan_meta(key, value):
    init_db()
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO plan_meta (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Error writing plan meta %s: %s", key, e)
