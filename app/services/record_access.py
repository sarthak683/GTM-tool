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
