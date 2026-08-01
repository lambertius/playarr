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
