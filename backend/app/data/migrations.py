"""Application-owned Alembic migration entry points."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = BACKEND_ROOT / "alembic.ini"


def upgrade_database(database_url: str) -> None:
    """Upgrade *database_url* to the repository's current Alembic revision."""
    config = Config(str(ALEMBIC_CONFIG_PATH))
    # ConfigParser treats percent signs as interpolation markers. SQLAlchemy
    # URLs legitimately contain percent-encoded credentials, so escape them
    # before storing the value; ``get_main_option`` returns the original URL.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
