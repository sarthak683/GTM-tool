"""Record ownership is independent of workspace-wide viewing access."""
from fastapi import HTTPException


def can_edit_record(user, record) -> bool:
    if getattr(user, "is_admin", False) or str(getattr(user, "role", "")).lower() in {"admin", "superadmin"}:
        return True
    user_id = getattr(user, "id", None)
    return user_id is not None and user_id in (
        getattr(record, "assigned_to_id", None), getattr(record, "sdr_id", None),
    )


def authorize_company_edit(user, company) -> None:
    if not can_edit_record(user, company):
        raise HTTPException(403, "Only the assigned AE/SDR or an admin can edit this account.")


def authorize_prospect_delete(user) -> None:
    if not (getattr(user, "is_admin", False) or str(getattr(user, "role", "")).lower() in {"admin", "superadmin", "ae", "sdr"}):
        raise HTTPException(403, "Only AEs, SDRs and admins can delete prospects.")


ACCOUNT_OWNER_FIELDS = frozenset({
    "assigned_to_id", "assigned_rep", "assigned_rep_email", "assigned_rep_name",
    "sdr_id", "sdr_email", "sdr_name", "sdr_assigned_at",
})


def authorize_account_assignment(user) -> None:
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "Only admins can assign or unassign account owners.")


def authorize_account_owner_update(user, company, update_data) -> None:
    # Full edit forms may echo unchanged owner labels; only changes require admin.
    if any(key in update_data and update_data[key] != getattr(company, key, None)
           for key in ACCOUNT_OWNER_FIELDS):
        authorize_account_assignment(user)
