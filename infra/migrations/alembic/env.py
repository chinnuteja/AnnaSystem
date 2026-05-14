"""Alembic env.py — auto-generates migrations from foodleaf models.

Reads DATABASE_URL from .env and imports all models so Alembic can
detect schema changes via --autogenerate.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Make sure the project root is on sys.path so we can import packages.*
ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

# Import all models so Alembic sees them via Base.metadata
from packages.core.models import Base  # noqa: E402

# Alembic Config object
config = context.config

# Set the DB URL from .env (overrides alembic.ini)
config.set_main_option(
    "sqlalchemy.url",
    os.getenv("DATABASE_URL", "postgresql://foodleaf:foodleaf@localhost:5432/foodleaf"),
)

# Setup logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without connecting."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to DB and applies."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
