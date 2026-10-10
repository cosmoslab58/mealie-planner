"""Flask application factory for the Mealie planner web UI."""
import logging
import os

from flask import Flask, current_app

_scheduler_started = False


def get_services():
    """Return the Services container attached to the current app."""
    return current_app.extensions['services']


def create_app(services=None, start_scheduler=None):
    """Build the Flask app.

    `services` allows tests to inject a mocked Services container.
    `start_scheduler` controls the background email scheduler; defaults to the
    ENABLE_SCHEDULER env var (on unless set to '0').
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=os.getenv('LOG_LEVEL', 'INFO').upper(),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    app = Flask(
        'mealie_planner',
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static'),
    )

    from mealie_planner.config import get_secret_key
    app.secret_key = get_secret_key()

    if services is None:
        from .services import build_services
        services = build_services()
    app.extensions['services'] = services

    # Cache-busting version for extracted static assets, computed once at startup.
    asset_version = 0
    for asset in ('css/app.css', 'js/app.js'):
        try:
            asset_version = max(asset_version, int(os.path.getmtime(os.path.join(app.static_folder, asset))))
        except OSError:
            pass

    @app.context_processor
    def inject_asset_version():
        return {'asset_version': asset_version}

    from .routes.admin import admin_bp
    from .routes.chat import chat_bp
    from .routes.planning import planning_bp
    from .routes.shopping import shopping_bp
    app.register_blueprint(planning_bp)
    app.register_blueprint(shopping_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(admin_bp)

    if start_scheduler is None:
        start_scheduler = os.getenv('ENABLE_SCHEDULER', '1') != '0'
    global _scheduler_started
    if start_scheduler and not _scheduler_started:
        from mealie_planner.email_notifier import setup_scheduler
        setup_scheduler(services)
        _scheduler_started = True
        logging.getLogger(__name__).info("Background email scheduler started.")

    return app
