"""Add revisioned, non-destructive genre consolidation aggregates.

Revision ID: 027_genre_aggregate
Revises: 026_mutation_results
"""
from alembic import op
import sqlalchemy as sa


revision = "027_genre_aggregate"
down_revision = "026_mutation_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artist_consolidations") as batch:
        batch.add_column(sa.Column("created_by", sa.String(100), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_artist_consolidations_deleted_at", ["deleted_at"])
    with op.batch_alter_table("artist_consolidation_targets") as batch:
        batch.add_column(sa.Column("provenance_json", sa.JSON(), nullable=True))

    op.create_table(
        "genre_consolidations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("stable_id", sa.String(36), nullable=False),
        sa.Column("mask_name", sa.String(200), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_genre_consolidations_stable_id", "genre_consolidations", ["stable_id"], unique=True)
    op.create_index("ix_genre_consolidations_mask_name", "genre_consolidations", ["mask_name"])
    op.create_index("ix_genre_consolidations_deleted_at", "genre_consolidations", ["deleted_at"])
    op.create_table(
        "genre_consolidation_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("consolidation_id", sa.Integer(), sa.ForeignKey("genre_consolidations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_name", sa.String(200), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint("consolidation_id", "raw_name", name="uq_genre_consolidation_member"),
    )
    op.create_index("ix_genre_consolidation_members_consolidation_id", "genre_consolidation_members", ["consolidation_id"])
    op.create_index("ix_genre_consolidation_members_raw_name", "genre_consolidation_members", ["raw_name"])

    op.create_table(
        "field_provenance_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("video_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("video_stable_id", sa.String(36), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("actor_kind", sa.String(30), nullable=False),
        sa.Column("actor_id", sa.String(200), nullable=True),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("remote_id", sa.String(200), nullable=True),
        sa.Column("transformation", sa.String(200), nullable=True),
        sa.Column("prior_value_hash", sa.String(64), nullable=True),
        sa.Column("resulting_value_hash", sa.String(64), nullable=True),
        sa.Column("verification_json", sa.JSON(), nullable=True),
        sa.Column("operation_id", sa.String(80), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for column in ("video_id", "video_stable_id", "field_name", "event_type", "operation_id", "created_at"):
        op.create_index(f"ix_field_provenance_events_{column}", "field_provenance_events", [column])

    # Preserve the old alias representation while establishing stable
    # aggregates. Raw genre rows and their source tags are never rewritten.
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT a.name AS alias_name, m.name AS mask_name "
        "FROM genres a JOIN genres m ON m.id = a.master_genre_id "
        "ORDER BY m.id, a.id"
    )).mappings().all()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["mask_name"], [row["mask_name"]]).append(row["alias_name"])
    from uuid import uuid4
    for mask_name, members in grouped.items():
        result = connection.execute(sa.text(
            "INSERT INTO genre_consolidations "
            "(stable_id, mask_name, revision, created_at, updated_at) "
            "VALUES (:stable_id, :mask_name, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ), {"stable_id": str(uuid4()), "mask_name": mask_name})
        consolidation_id = result.lastrowid
        for raw_name in dict.fromkeys(members):
            connection.execute(sa.text(
                "INSERT INTO genre_consolidation_members (consolidation_id, raw_name) "
                "VALUES (:consolidation_id, :raw_name)"
            ), {"consolidation_id": consolidation_id, "raw_name": raw_name})


def downgrade() -> None:
    op.drop_table("field_provenance_events")
    op.drop_table("genre_consolidation_members")
    op.drop_table("genre_consolidations")
    with op.batch_alter_table("artist_consolidation_targets") as batch:
        batch.drop_column("provenance_json")
    with op.batch_alter_table("artist_consolidations") as batch:
        batch.drop_index("ix_artist_consolidations_deleted_at")
        batch.drop_column("deleted_at")
        batch.drop_column("created_by")
