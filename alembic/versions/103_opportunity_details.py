"""Add opportunity details and MEDDPICC fields to companies

Revision ID: 103
Revises: 102
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa

revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None


def upgrade():
    # Core opportunity fields
    op.add_column("companies", sa.Column("opp_name", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_amount", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("opp_arr", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("opp_multiyear_license_fee", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("opp_service_fee", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("opp_type", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_sales_category", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_geolocation", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_owner", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_solution_engineer", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_close_date", sa.Date(), nullable=True))
    op.add_column("companies", sa.Column("opp_forecast_category", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_probability", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("opp_stage", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_poc_start_date", sa.Date(), nullable=True))
    op.add_column("companies", sa.Column("opp_poc_status", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_aop_doc_link", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("opp_msp_doc_link", sa.String(), nullable=True))
    # MEDDPICC fields
    op.add_column("companies", sa.Column("medd_business_initiatives", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_business_pains", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_technical_pains", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_size_business_pain", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("medd_who_impacted_business", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_size_technical_pain", sa.Float(), nullable=True))
    op.add_column("companies", sa.Column("medd_who_impacted_technical", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_metrics", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_decision_criteria", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_economic_buyer", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("medd_eb_top_2_priorities", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_decision_process", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_paper_process", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_champion", sa.String(), nullable=True))
    op.add_column("companies", sa.Column("medd_champion_win", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("medd_competition", sa.Text(), nullable=True))
    # Current deal status note
    op.add_column("companies", sa.Column("opp_current_deal_status", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("companies", "opp_current_deal_status")
    op.drop_column("companies", "medd_competition")
    op.drop_column("companies", "medd_champion_win")
    op.drop_column("companies", "medd_champion")
    op.drop_column("companies", "medd_paper_process")
    op.drop_column("companies", "medd_decision_process")
    op.drop_column("companies", "medd_eb_top_2_priorities")
    op.drop_column("companies", "medd_economic_buyer")
    op.drop_column("companies", "medd_decision_criteria")
    op.drop_column("companies", "medd_metrics")
    op.drop_column("companies", "medd_who_impacted_technical")
    op.drop_column("companies", "medd_size_technical_pain")
    op.drop_column("companies", "medd_who_impacted_business")
    op.drop_column("companies", "medd_size_business_pain")
    op.drop_column("companies", "medd_technical_pains")
    op.drop_column("companies", "medd_business_pains")
    op.drop_column("companies", "medd_business_initiatives")
    op.drop_column("companies", "opp_msp_doc_link")
    op.drop_column("companies", "opp_aop_doc_link")
    op.drop_column("companies", "opp_poc_status")
    op.drop_column("companies", "opp_poc_start_date")
    op.drop_column("companies", "opp_stage")
    op.drop_column("companies", "opp_probability")
    op.drop_column("companies", "opp_forecast_category")
    op.drop_column("companies", "opp_close_date")
    op.drop_column("companies", "opp_solution_engineer")
    op.drop_column("companies", "opp_owner")
    op.drop_column("companies", "opp_geolocation")
    op.drop_column("companies", "opp_sales_category")
    op.drop_column("companies", "opp_type")
    op.drop_column("companies", "opp_service_fee")
    op.drop_column("companies", "opp_multiyear_license_fee")
    op.drop_column("companies", "opp_arr")
    op.drop_column("companies", "opp_amount")
    op.drop_column("companies", "opp_name")
