"""Guard: every milestone / meeting-booked aggregation carries the canonical
live-deal scoping.

The recurring bug this locks down: a dashboard TILE and the DRILLDOWN modal it
opens read the same records through two different queries, and only the
drilldown filters to live "deal"-pipeline rows. A soft-deleted or
prospect-pipeline deal then inflates the tile above the list it opens, and the
two numbers disagree on screen.

The canonical rule (stated on the sales-dashboard deal scan in
``app/api/v1/endpoints/analytics.py``) is both of:

    Deal.pipeline_type == "deal"
    Deal.deleted_at.is_(None)

There is no test database in this suite, so this is a source-level check: parse
analytics.py and assert that every ``.where(...)`` block which reads
``CompanyStageMilestone`` (or filters on ``Deal.meeting_booked_from``) also
names both predicates. Cheap, no I/O, and it fails the moment someone adds a
sixth milestone aggregation without the scoping.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

ANALYTICS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "v1"
    / "endpoints"
    / "analytics.py"
)

REQUIRED = {"Deal.pipeline_type", "Deal.deleted_at"}


def _dotted_names(node: ast.AST) -> set[str]:
    """Every ``Model.column`` reference appearing anywhere inside ``node``."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
            found.add(f"{sub.value.id}.{sub.attr}")
    return found


def _where_blocks() -> list[tuple[int, set[str]]]:
    """(line number, referenced names) for every ``.where(...)`` call."""
    tree = ast.parse(ANALYTICS.read_text())
    blocks: list[tuple[int, set[str]]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "where"
        ):
            names: set[str] = set()
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                names |= _dotted_names(arg)
            blocks.append((node.lineno, names))
    return blocks


class MilestoneQueryScopingTests(unittest.TestCase):
    def test_analytics_module_is_present(self) -> None:
        self.assertTrue(ANALYTICS.is_file(), f"missing source file: {ANALYTICS}")

    def test_every_milestone_query_scopes_to_live_deal_pipeline_rows(self) -> None:
        offenders = [
            line
            for line, names in _where_blocks()
            if any(n.startswith("CompanyStageMilestone.") for n in names)
            and not REQUIRED.issubset(names)
        ]
        self.assertEqual(
            offenders,
            [],
            "CompanyStageMilestone query at analytics.py line(s) "
            f"{offenders} is missing Deal.pipeline_type / Deal.deleted_at — the "
            "tile it feeds will out-count the drilldown that opens beneath it.",
        )

    def test_milestone_queries_actually_exist(self) -> None:
        # Guards the guard: if a refactor moves these queries out of the module,
        # the check above would pass vacuously.
        milestone_blocks = [
            line
            for line, names in _where_blocks()
            if any(n.startswith("CompanyStageMilestone.") for n in names)
        ]
        self.assertGreaterEqual(len(milestone_blocks), 4, milestone_blocks)

    def test_meeting_booked_from_queries_scope_to_live_deal_pipeline_rows(self) -> None:
        blocks = [
            (line, names)
            for line, names in _where_blocks()
            if "Deal.meeting_booked_from" in names
        ]
        self.assertGreaterEqual(len(blocks), 2, blocks)
        offenders = [line for line, names in blocks if not REQUIRED.issubset(names)]
        self.assertEqual(
            offenders,
            [],
            "Deal.meeting_booked_from query at analytics.py line(s) "
            f"{offenders} is missing the live-deal predicates.",
        )


if __name__ == "__main__":
    unittest.main()
