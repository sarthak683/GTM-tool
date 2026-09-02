from sqlalchemy import column
from sqlalchemy.dialects import postgresql

from app.api.v1.endpoints.global_search import (
    _fuzzy_match,
)


def _sql(expression) -> str:
    return str(
        expression.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_fuzzy_match_uses_indexable_trigram_operator_for_typo_length_queries():
    sql = _sql(_fuzzy_match(column("name"), "Beacn"))

    assert "name %% 'Beacn'" in sql


def test_short_queries_stay_substring_only():
    assert _sql(_fuzzy_match(column("name"), "Be")) == "false"
