import os

from flask import Flask
from werkzeug.exceptions import HTTPException

from .database import make_session_factory
from .models import Base
from .routes import api


def create_app(database_url=None):
    app = Flask(__name__)
    database_url = database_url or os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL to a postgresql+psycopg:// connection URL.")

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
