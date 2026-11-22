import os

from flask import Flask
from werkzeug.exceptions import HTTPException

from .database import make_session_factory
from .models import Base
from .routes import api


def create_app(database_url=None, *, secret_key=None):
    app = Flask(__name__)
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL to a postgresql+psycopg:// connection URL.")
    secret_key = secret_key or os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("Set SECRET_KEY to a random value for signing session cookies.")
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") != "0",
    )

    sessions = make_session_factory(database_url)
    app.extensions["db_sessions"] = sessions
    app.register_blueprint(api)

    @app.errorhandler(HTTPException)
    def http_error(error):
        return {
            "error": {
                "code": error.name.lower().replace(" ", "_"),
                "message": error.description,
            }
        }, error.code

    @app.cli.command("init-db")
    def init_db():
        """Create the tables in the configured database."""
        Base.metadata.create_all(sessions.kw["bind"])

    return app
