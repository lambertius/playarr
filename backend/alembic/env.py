"""
Alembic environment configuration.
"""
import sys
from logging.config import fileConfig
from pathlib import Path
from sqlalchemy import engine_from_config, inspect, pool
from alembic import context
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

# ``alembic.exe`` lives inside ``venv/Scripts`` on Windows, so its import path
# does not reliably include the backend directory. Keep migrations runnable
# from a clean shell without requiring callers to know a PYTHONPATH incantation.
backend_root = str(Path(__file__).resolve().parents[1])
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)

# Import models so Alembic sees them
from app.database import Base
from app.models import *  # noqa
from app.new_videos.models import *  # noqa: F401,F403
from app.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the sqlalchemy URL with the runtime-resolved DB path
# so migrations work in both dev and production modes.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Historical migrations begin from the first post-bootstrap schema and
        # cannot create an empty database. A clean install therefore creates
        # the current declarative schema and stamps it at head. Existing
        # databases continue through the ordinary incremental migration path.
        user_tables = {
            name for name in inspect(connection).get_table_names()
            if name != "alembic_version"
        }
        if not user_tables:
            Base.metadata.create_all(bind=connection)
            script = ScriptDirectory.from_config(config)
            MigrationContext.configure(connection).stamp(
                script, script.get_current_head()
            )
            connection.commit()
            return
        # Schema inspection opens an implicit SQLAlchemy 2.x transaction.
        # Close it before handing ownership to Alembic; otherwise SQLite DDL
        # can leak through while the version-table update is rolled back,
        # leaving a partially upgraded database that still reports the old
        # revision.
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
