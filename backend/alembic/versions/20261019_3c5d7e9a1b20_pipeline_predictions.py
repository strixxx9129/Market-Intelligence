"""production pipeline: predictions, outcomes, promotion decisions, task-run bookkeeping

Revision ID: 3c5d7e9a1b20
Revises: 72b1b61acb31
Create Date: 2026-10-19 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c5d7e9a1b20"
down_revision: str | None = "72b1b61acb31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- pipeline_runs: one row per Celery task attempt -------------------------------------
    op.execute("ALTER TYPE pipeline_run_status ADD VALUE IF NOT EXISTS 'retrying'")
    op.add_column(
        "pipeline_runs", sa.Column("attempt", sa.Integer(), server_default="1", nullable=False)
    )
    op.add_column("pipeline_runs", sa.Column("celery_task_id", sa.String(64), nullable=True))
    op.add_column("pipeline_runs", sa.Column("root_id", sa.String(64), nullable=True))
    op.add_column("pipeline_runs", sa.Column("parent_run_id", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_pipeline_runs_celery_task_id"), "pipeline_runs", ["celery_task_id"])
    op.create_index(op.f("ix_pipeline_runs_root_id"), "pipeline_runs", ["root_id"])
    op.create_foreign_key(
        op.f("fk_pipeline_runs_parent_run_id_pipeline_runs"),
        "pipeline_runs",
        "pipeline_runs",
        ["parent_run_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # --- predictions --------------------------------------------------------------------------
    op.create_table(
        "predictions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("as_of_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("horizon", sa.Integer(), nullable=False),
        sa.Column("y_pred", sa.Float(), nullable=False),
        sa.Column("p_up", sa.Float(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("pipeline_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('live', 'backfill')", name=op.f("ck_predictions_source_valid")
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            name=op.f("fk_predictions_model_version_id_model_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["assets.id"],
            name=op.f("fk_predictions_asset_id_assets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_predictions_pipeline_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_predictions")),
        sa.UniqueConstraint(
            "model_version_id",
            "asset_id",
            "as_of_ts",
            "horizon",
            name=op.f("uq_predictions_model_version_id_asset_id_as_of_ts_horizon"),
        ),
    )
    op.create_index(op.f("ix_predictions_as_of_ts"), "predictions", ["as_of_ts"])

    # --- prediction_outcomes ------------------------------------------------------------------
    op.create_table(
        "prediction_outcomes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("prediction_id", sa.BigInteger(), nullable=False),
        sa.Column("realized_return", sa.Float(), nullable=False),
        sa.Column("realized_up", sa.Boolean(), nullable=False),
        sa.Column("log_loss", sa.Float(), nullable=True),
        sa.Column("brier", sa.Float(), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"],
            ["predictions.id"],
            name=op.f("fk_prediction_outcomes_prediction_id_predictions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prediction_outcomes")),
        sa.UniqueConstraint("prediction_id", name=op.f("uq_prediction_outcomes_prediction_id")),
    )

    # --- promotion_decisions -------------------------------------------------------------------
    op.create_table(
        "promotion_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("candidate_model_version_id", sa.Integer(), nullable=True),
        sa.Column("baseline_model_version_id", sa.Integer(), nullable=True),
        sa.Column("production_model_version_id", sa.Integer(), nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reasons", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("metrics", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("pipeline_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('promoted', 'rejected', 'fallback')",
            name=op.f("ck_promotion_decisions_decision_valid"),
        ),
        *[
            sa.ForeignKeyConstraint(
                [col],
                ["model_versions.id"],
                name=op.f(f"fk_promotion_decisions_{col}_model_versions"),
                ondelete="RESTRICT",
            )
            for col in (
                "candidate_model_version_id",
                "baseline_model_version_id",
                "production_model_version_id",
            )
        ],
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"],
            ["pipeline_runs.id"],
            name=op.f("fk_promotion_decisions_pipeline_run_id_pipeline_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_decisions")),
    )


def downgrade() -> None:
    op.drop_table("promotion_decisions")
    op.drop_table("prediction_outcomes")
    op.drop_index(op.f("ix_predictions_as_of_ts"), table_name="predictions")
    op.drop_table("predictions")

    op.drop_constraint(
        op.f("fk_pipeline_runs_parent_run_id_pipeline_runs"), "pipeline_runs", type_="foreignkey"
    )
    op.drop_index(op.f("ix_pipeline_runs_root_id"), table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_celery_task_id"), table_name="pipeline_runs")
    for col in ("parent_run_id", "root_id", "celery_task_id", "attempt"):
        op.drop_column("pipeline_runs", col)

    # Remove 'retrying' by recreating the enum (Postgres can't drop enum values).
    op.execute("UPDATE pipeline_runs SET status = 'failed' WHERE status = 'retrying'")
    op.execute("ALTER TYPE pipeline_run_status RENAME TO pipeline_run_status_old")
    op.execute(
        "CREATE TYPE pipeline_run_status AS ENUM ('running', 'success', 'failed', 'skipped')"
    )
    op.execute(
        "ALTER TABLE pipeline_runs ALTER COLUMN status TYPE pipeline_run_status "
        "USING status::text::pipeline_run_status"
    )
    op.execute("DROP TYPE pipeline_run_status_old")
