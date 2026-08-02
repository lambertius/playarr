from sqlalchemy import create_engine, inspect, text

from app.main import _apply_schema_upgrades


def _legacy_processing_jobs_engine(tmp_path, *, partial=False):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    correlation_columns = (
        ", request_id VARCHAR(80), operation_id VARCHAR(80)" if partial else ""
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE processing_jobs ("
            "id INTEGER PRIMARY KEY, status VARCHAR(30) NOT NULL"
            f"{correlation_columns})"
        ))
        conn.execute(text(
            "CREATE TABLE settings ("
            "id INTEGER PRIMARY KEY, user_id VARCHAR(36), "
            "key VARCHAR(100) NOT NULL, value TEXT, "
            "value_type VARCHAR(20), revision INTEGER NOT NULL DEFAULT 1)"
        ))
        if partial:
            conn.execute(text(
                "INSERT INTO processing_jobs (id, status, request_id, operation_id) "
                "VALUES (7, 'complete', 'request-existing', 'operation-existing'), "
                "(42, 'failed', NULL, NULL)"
            ))
        else:
            conn.execute(text(
                "INSERT INTO processing_jobs (id, status) "
                "VALUES (7, 'complete'), (42, 'failed')"
            ))
    return engine


def test_schema_upgrade_adds_processing_job_correlation_columns(tmp_path):
    engine = _legacy_processing_jobs_engine(tmp_path)

    _apply_schema_upgrades(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("processing_jobs")}
    assert {"request_id", "operation_id"} <= columns
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, request_id, operation_id FROM processing_jobs ORDER BY id"
        )).all()
        indexes = {
            row[1]: bool(row[2])
            for row in conn.execute(text("PRAGMA index_list('processing_jobs')"))
        }

    assert rows == [
        (7, None, "legacy_job_7"),
        (42, None, "legacy_job_42"),
    ]
    assert indexes["ix_processing_jobs_request_id"] is False
    assert indexes["ix_processing_jobs_operation_id"] is True


def test_schema_upgrade_resumes_partial_processing_job_upgrade(tmp_path):
    engine = _legacy_processing_jobs_engine(tmp_path, partial=True)

    _apply_schema_upgrades(engine)
    _apply_schema_upgrades(engine)

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, request_id, operation_id FROM processing_jobs ORDER BY id"
        )).all()

    assert rows == [
        (7, "request-existing", "operation-existing"),
        (42, None, "legacy_job_42"),
    ]


def test_schema_upgrade_repairs_existing_artist_consolidation_aggregate(tmp_path):
    engine = _legacy_processing_jobs_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE artist_consolidations ("
            "id INTEGER PRIMARY KEY, stable_id VARCHAR(36) NOT NULL, "
            "mask_name VARCHAR(500) NOT NULL, revision INTEGER NOT NULL DEFAULT 1, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        ))
        conn.execute(text(
            "CREATE TABLE artist_consolidation_targets ("
            "id INTEGER PRIMARY KEY, consolidation_id INTEGER NOT NULL, "
            "raw_name VARCHAR(500) NOT NULL, provenance VARCHAR(100), "
            "mb_artist_id VARCHAR(36))"
        ))

    _apply_schema_upgrades(engine)
    _apply_schema_upgrades(engine)

    inspector = inspect(engine)
    aggregate_columns = {
        column["name"] for column in inspector.get_columns("artist_consolidations")
    }
    target_columns = {
        column["name"] for column in inspector.get_columns("artist_consolidation_targets")
    }
    indexes = {
        index["name"] for index in inspector.get_indexes("artist_consolidations")
    }

    assert {"created_by", "deleted_at"} <= aggregate_columns
    assert "provenance_json" in target_columns
    assert "ix_artist_consolidations_deleted_at" in indexes
