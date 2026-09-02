"""Regression tests for the repository-owned visibility boundary.

These tests stay database-free so they run in the normal backend smoke suite.
They verify both halves of the contract: the predicates reject a different
rep, and endpoint modules cannot quietly reintroduce a bare entity select.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.company import Company
from app.models.deal import Deal
from app.repositories.company import can_see_company, company_visibility_filter
from app.repositories.contact import contact_visibility_filter
from app.repositories.deal import can_see_deal, deal_visibility_filter


ENDPOINTS = Path(__file__).parents[1] / "app" / "api" / "v1" / "endpoints"
SYSTEM_QUERY_DIRS = [
    Path(__file__).parents[1] / "app" / "services",
    Path(__file__).parents[1] / "app" / "tasks",
]
SCOPED_MODELS = {"Company", "Contact", "Deal"}


@pytest.mark.parametrize("model_name", sorted(SCOPED_MODELS))
def test_endpoint_modules_have_no_bare_scoped_entity_selects(model_name: str):
    """A new endpoint cannot opt out of visibility by reaching for select(Model)."""
    violations: list[str] = []
    for path in ENDPOINTS.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func_name = node.func.id if isinstance(node.func, ast.Name) else None
            if func_name not in {"select", "sm_select"}:
                continue
            first = node.args[0]
            if isinstance(first, ast.Name) and first.id == model_name:
                violations.append(f"{path.name}:{node.lineno}")
    assert violations == [], (
        f"Bare select({model_name}) bypasses the visibility repository: {violations}"
    )


@pytest.mark.parametrize("model_name", sorted(SCOPED_MODELS))
def test_system_modules_name_every_unscoped_entity_query(model_name: str):
    """Background services must use the reason-bearing escape hatch."""
    violations: list[str] = []
    for directory in SYSTEM_QUERY_DIRS:
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func_name = node.func.id if isinstance(node.func, ast.Name) else None
                first = node.args[0]
                if (
                    func_name in {"select", "sm_select"}
                    and isinstance(first, ast.Name)
                    and first.id == model_name
                ):
                    violations.append(f"{path.name}:{node.lineno}")
    assert violations == [], (
        f"Unlabelled system select({model_name}) bypasses the review boundary: "
        f"{violations}"
    )


@pytest.mark.parametrize("role", ["ae", "sdr"])
def test_company_rule_refuses_cross_rep(role: str):
    owner_id, outsider_id = uuid4(), uuid4()
    outsider = SimpleNamespace(id=outsider_id, role=role, is_admin=False)
    company = Company(
        id=uuid4(),
        name="Foreign account",
        domain="foreign.example",
        assigned_to_id=owner_id,
        sdr_id=owner_id,
    )

    assert can_see_company(company, outsider) is False
    sql = str(
        company_visibility_filter(outsider_id, False, include_disabled=True).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert str(outsider_id) in sql
    assert "assigned_to_id" in sql and "sdr_id" in sql


@pytest.mark.parametrize("role", ["ae", "sdr"])
def test_contact_rule_keeps_account_ownership_as_outer_gate(role: str):
    outsider_id = uuid4()
    sql = str(
        contact_visibility_filter(outsider_id, role).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert str(outsider_id) in sql
    assert "contacts.company_id IN (SELECT companies.id" in sql
    assert "companies.assigned_to_id" in sql
    assert "companies.sdr_id" in sql


def test_deal_rule_refuses_soft_deleted_record():
    user = SimpleNamespace(id=uuid4(), role="ae", is_admin=False)
    deal = Deal(id=uuid4(), name="Deleted deal", stage="open")
    deal.deleted_at = SimpleNamespace()  # only truthiness/non-None matters here

    assert can_see_deal(deal, user) is False
    sql = str(
        deal_visibility_filter(user.id, False).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "deals.deleted_at IS NULL" in sql
