"""Routes for shopping list and staples management."""
import json
import logging
import re

from flask import Blueprint, flash, jsonify, redirect, request, url_for

from mealie_planner.config import ACTIVE_LIST_ID, NEXT_LIST_ID, STAPLES_LIST_ID
from mealie_planner.database import load_state_from_db as load_state
from mealie_planner.database import save_state_to_db as save_state
from mealie_planner.shopping_sync import normalize_ingredient_name
from mealie_planner.utils import resolve_week, sanitize_input
from mealie_planner.web import get_services

logger = logging.getLogger(__name__)

shopping_bp = Blueprint('shopping', __name__)


@shopping_bp.route('/update-staples', methods=['POST'])
def update_staples():
    """Fast endpoint specifically for the staples modal."""
    services = get_services()
    week_ctx = resolve_week(request.form.get('week', 'current'))
    if request.form.get('staples_submitted'):
        low_staples = request.form.getlist('low_staples')
        save_state({'low_staples': low_staples})

        try:
            services.shopping.sync_staples_only(low_staples, list_id=week_ctx.list_id)
            flash("Staples updated successfully!", "success")
        except Exception as e:
            logger.error("Error updating staples: %s", e)
            flash(f"Error updating staples: {str(e)}", "danger")

    return redirect(url_for('planning.index', week=week_ctx.week))


@shopping_bp.route('/sync', methods=['POST'])
def sync():
    """Manual trigger to re-sync the shopping list based on current plans."""
    services = get_services()
    week_ctx = resolve_week(request.form.get('week', 'current'))

    low_staples = load_state().get('low_staples', [])

    # If the request comes from the staples modal, update the state
    if request.form.get('staples_submitted'):
        low_staples = request.form.getlist('low_staples')
        save_state({'low_staples': low_staples})

    lock = services.week_lock(week_ctx.week)
    if not lock.acquire(blocking=False):
        flash("A plan generation is currently running for this week. Please retry once it finishes.", "danger")
        return redirect(url_for('planning.index', week=week_ctx.week))

    try:
        sync_ok = services.shopping.sync_shopping_list(
            week_ctx.start_str, week_ctx.end_str,
            low_staples_ids=low_staples, list_id=week_ctx.list_id,
        )
        if sync_ok:
            flash("Recalculated active shopping list successfully!", "success")
        else:
            flash("Shopping list sync failed: the AI did not return a valid list. Please try again.", "danger")
    except Exception as e:
        logger.error("Error syncing shopping list: %s", e)
        flash(f"Error syncing shopping list: {str(e)}", "danger")
    finally:
        lock.release()

    return redirect(url_for('planning.index', week=week_ctx.week))


def _add_item_to_list(list_id: str, item_type: str):
    """Add an item to a specific Mealie shopping list, auto-categorized by AI."""
    services = get_services()
    try:
        data = request.get_json()
        note = sanitize_input(data.get('note', ''))
        if not note:
            return jsonify(success=False, error=f"{item_type} name is required"), 400

        label_id = None
        try:
            labels = services.mealie.get_labels()
            if labels:
                label_names = [label['name'] for label in labels]
                prompt = (
                    f"Categorize the grocery item '{note}' into one of these categories:\n"
                    f"{json.dumps(label_names)}\n\n"
                    "Reply with ONLY the exact category name from the list, nothing else."
                )
                category = services.ai.call(prompt, expect_json=False).strip()
                category_clean = re.sub(r'["\']', '', category).strip().lower()
                matched_label = next(
                    (label for label in labels if label['name'].lower().strip() == category_clean), None
                )
                if matched_label:
                    label_id = matched_label['id']
        except Exception as e:
            logger.warning("Error auto-categorizing item: %s", e)

        services.mealie.add_shopping_list_item(list_id, note, label_id=label_id)
        return jsonify(success=True)
    except Exception as e:
        logger.error("Error adding %s: %s", item_type.lower(), e)
        return jsonify(success=False, error=str(e)), 500


@shopping_bp.route('/add-shopping-item', methods=['POST'])
def add_shopping_item():
    """Add a single manual item to the active shopping list."""
    week = (request.get_json() or {}).get('week', 'current')
    return _add_item_to_list(resolve_week(week).list_id, "Item")


@shopping_bp.route('/add-staple', methods=['POST'])
def add_staple():
    """Add a new staple item to the staples shopping list."""
    return _add_item_to_list(STAPLES_LIST_ID, "Staple")


@shopping_bp.route('/delete-staple', methods=['POST'])
def delete_staple():
    """Delete a staple item and remove any matching items from the active lists."""
    services = get_services()
    mealie = services.mealie
    try:
        data = request.get_json()
        item_id = data.get('item_id')
        if not item_id:
            return jsonify(success=False, error="Item ID is required"), 400

        # Fetch the staple note before deleting
        staple_note = None
        try:
            staples = mealie.get_shopping_list_items_for_list(STAPLES_LIST_ID)
            staple_item = next((item for item in staples if item['id'] == item_id), None)
            if staple_item:
                staple_note = staple_item['note']
        except Exception as e:
            logger.warning("Error fetching staple name before delete: %s", e)

        mealie.delete_shopping_list_item(item_id)

        # Also delete matching items from the active/next lists
        if staple_note:
            try:
                staple_norm = normalize_ingredient_name(staple_note)
                matching_active_ids = []
                for list_id in {ACTIVE_LIST_ID, NEXT_LIST_ID}:
                    active_items = mealie.get_shopping_list_items_for_list(list_id)
                    matching_active_ids.extend(
                        item['id'] for item in active_items
                        if normalize_ingredient_name(item['note']) == staple_norm
                    )
                if matching_active_ids:
                    logger.info("Deleting matching active items: %s", matching_active_ids)
                    mealie.delete_shopping_list_items_bulk(matching_active_ids)
            except Exception as e:
                logger.warning("Error deleting matching active item: %s", e)

        return jsonify(success=True)
    except Exception as e:
        logger.error("Error deleting staple: %s", e)
        return jsonify(success=False, error=str(e)), 500


@shopping_bp.route('/toggle-shopping-item', methods=['POST'])
def toggle_shopping_item():
    services = get_services()
    try:
        data = request.get_json()
        item_id = data.get('item_id')
        is_checked = data.get('checked')
        list_id = resolve_week(data.get('week', 'current')).list_id

        items = services.mealie.get_shopping_list_items_for_list(list_id)
        target_item = next((item for item in items if item['id'] == item_id), None)

        if not target_item:
            return jsonify(success=False, error="Item not found"), 404

        target_item['checked'] = is_checked
        services.mealie.update_shopping_list_item(item_id, target_item)

        return jsonify(success=True)
    except Exception as e:
        logger.error("Error toggling shopping item: %s", e)
        return jsonify(success=False, error=str(e)), 500


@shopping_bp.route('/check-all-items', methods=['POST'])
def check_all_items():
    """Mark all items in the active shopping list as checked."""
    services = get_services()
    try:
        week = (request.get_json(silent=True) or {}).get('week', 'current')
        items = services.mealie.get_shopping_list_items_for_list(resolve_week(week).list_id)

        bulk_items = []
        for item in items:
            if not item.get('checked'):
                item['checked'] = True
                bulk_items.append(item)

        if bulk_items:
            services.mealie.update_shopping_list_items_bulk(bulk_items)

        return jsonify(success=True, count=len(bulk_items))
    except Exception as e:
        logger.error("Error checking all items: %s", e)
        return jsonify(success=False, error=str(e)), 500


@shopping_bp.route('/delete-checked-items', methods=['POST'])
def delete_checked_items():
    """Delete all checked items from the active shopping list."""
    services = get_services()
    try:
        week = (request.get_json(silent=True) or {}).get('week', 'current')
        items = services.mealie.get_shopping_list_items_for_list(resolve_week(week).list_id)

        checked_ids = [item['id'] for item in items if item.get('checked') and 'id' in item]
        if checked_ids:
            services.mealie.delete_shopping_list_items_bulk(checked_ids)

        return jsonify(success=True, count=len(checked_ids))
    except Exception as e:
        logger.error("Error deleting checked items: %s", e)
        return jsonify(success=False, error=str(e)), 500
