"""Resumable installed-database repairs for durable operation tables."""
from sqlalchemy import inspect, text


def apply_operation_schema_repairs(engine) -> None:
    inspector = inspect(engine)
    if "mutation_commands" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("mutation_commands")}
    if "result_json" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE mutation_commands ADD COLUMN result_json JSON"
            ))
            connection.execute(text(
                "UPDATE mutation_commands "
                "SET result_json = json_extract(error_json, '$.result'), error_json = NULL "
                "WHERE status = 'succeeded' AND json_type(error_json, '$.result') IS NOT NULL"
            ))


def apply_consolidation_schema_repairs(engine) -> None:
    """Apply migration 027 columns to aggregates created by migration 023."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "artist_consolidations" in tables:
        columns = {
            column["name"] for column in inspector.get_columns("artist_consolidations")
        }
        with engine.begin() as connection:
            if "created_by" not in columns:
                connection.execute(text(
                    "ALTER TABLE artist_consolidations ADD COLUMN created_by VARCHAR(100)"
                ))
            if "deleted_at" not in columns:
                connection.execute(text(
                    "ALTER TABLE artist_consolidations ADD COLUMN deleted_at DATETIME"
                ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_artist_consolidations_deleted_at "
                "ON artist_consolidations(deleted_at)"
            ))

    if "artist_consolidation_targets" in tables:
        columns = {
            column["name"]
            for column in inspector.get_columns("artist_consolidation_targets")
        }
        if "provenance_json" not in columns:
            with engine.begin() as connection:
                connection.execute(text(
                    "ALTER TABLE artist_consolidation_targets "
                    "ADD COLUMN provenance_json JSON"
                ))
