"""L1/L2/L3 call classification on meetings (Sales Lifecycle SOP stage 04)

Revision ID: 118
Revises: 117
Create Date: 2026-08-17

The SOP has the AE classify each upcoming client call as L1, L2 or L3 at the
prep call, from the calendar invite's attendee list, and that classification
sets how the call is run — how deep the discovery goes, whether the platform is
demoed, and what the mandatory next booking is. The CRM had nowhere to put it:
a grep for L1/L2/L3, "research doc" or "prep call" across the deal and meeting
models returned nothing, so the single input that drives Beacon's most important
client conversation lived only in the AE's head.

It belongs on the MEETING, not the deal. The SOP classifies "the upcoming call",
and one deal runs several — discovery, technical deep dive, POC demo — each with
a different audience and therefore a different level. Hanging one level off the
deal would overwrite the discovery call's classification the moment the deep
dive was booked.

Columns:
  call_level         "L1" | "L2" | "L3", nullable (unclassified / internal).
  call_level_source  "auto" (classifier) | "manual" (an AE decided).
                     The distinction is load-bearing: attendee lists change
                     right up to the call as people accept and decline, so the
                     classifier re-runs on every sync — and it must never
                     overwrite a human's judgement made at the prep call.
  call_level_set_by_id / call_level_set_at
                     Who overrode it and when. Not a foreign key, matching the
                     convention used for zippy_generated_docs.user_id: it
                     records provenance, and a nullable FK would only add a way
                     for deleting a user to fail on an old meeting row.

No confidence column. Confidence is a property of the CURRENT attendee list, not
of the stored decision, so it is computed on read — persisting it would let it
go stale the moment someone accepts the invite.
"""
from alembic import op
import sqlalchemy as sa

revision = "118"
down_revision = "117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("call_level", sa.String(), nullable=True))
    op.add_column("meetings", sa.Column("call_level_source", sa.String(), nullable=True))
    op.add_column("meetings", sa.Column("call_level_set_by_id", sa.Uuid(), nullable=True))
    op.add_column("meetings", sa.Column("call_level_set_at", sa.DateTime(), nullable=True))
    # Indexed because the point of capturing this is to slice on it — "how do
    # L3 calls convert vs L1", "which reps are getting exec audiences".
    op.create_index("ix_meetings_call_level", "meetings", ["call_level"])


def downgrade() -> None:
    op.drop_index("ix_meetings_call_level", table_name="meetings")
    op.drop_column("meetings", "call_level_set_at")
    op.drop_column("meetings", "call_level_set_by_id")
    op.drop_column("meetings", "call_level_source")
    op.drop_column("meetings", "call_level")
